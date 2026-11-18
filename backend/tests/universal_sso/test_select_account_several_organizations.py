"""A person in several organizations, opening a product by its address (no
organization named): which account, then which organization.

Two flows asked both questions and lost one of them:

- ``prompt=select_account`` (a product's "Use another account") from someone
  already signed in: ``authorize`` could not imply an organization, parked the
  request for the organization chooser, and ``resume`` applied only the
  person's preference - the account was never asked about.
- "Ask me each time": the organization chooser, then the access chooser (as
  ``test_one_login_guards`` pins); "Continue" then parked the request for the
  organization chooser again, unbound, and the chosen organization's resume
  asked again - the person went round the two choosers for ever.

Now the account comes first when the product asks for it, and an organization
asked for after the person has answered the access chooser is asked of them
only, and completes without asking how again.
"""

from __future__ import annotations

import pytest

from tests.universal_sso.conftest import (
    AUTHORIZE,
    CHOOSE,
    RESUME,
    authorize_query,
    path_of,
    query_of,
    resume_token_of,
)

pytestmark = pytest.mark.django_db


def _authorize(client, registration, org_id, **extra):
    _verifier, query = authorize_query(registration, org_id, **extra)
    return client.get(AUTHORIZE, query)


def _choose(client, token, choice):
    response = client.post(
        CHOOSE,
        {"resume": token, "choice": choice, "remember": False},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()


def _granted(response, registration) -> bool:
    return response["Location"].startswith(registration["redirect_uri"] + "?") and bool(
        query_of(response).get("code")
    )


def _codes(registration) -> int:
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    return AuthorizationCode.objects.filter(
        integration__client_id=registration["client_id"]
    ).count()


def _issued_to(registration) -> tuple[str, str]:
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    code = AuthorizationCode.objects.filter(
        integration__client_id=registration["client_id"]
    ).get()
    return str(code.user_id), str(code.organization_id)


def test_use_another_account_asks_which_account_before_which_organization(
    client, signing_key, sso_product, entitled_org, second_org_for, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    second_org_for(org["user"], "crm")
    signed_in(org["user"], sign_in_preference="auto_sso")

    asked = _authorize(client, crm, None, prompt="select_account")
    assert path_of(asked) == "/product-access", asked["Location"]
    assert _codes(crm) == 0

    answered = _choose(client, resume_token_of(asked), "workeasy360")
    to_organizations = client.get(answered["redirect_url"])
    assert path_of(to_organizations) == "/organizations/select", to_organizations[
        "Location"
    ]

    granted = client.get(
        RESUME,
        {
            "resume": resume_token_of(to_organizations),
            "organization_id": org["organization_id"],
        },
    )
    assert _granted(granted, crm), granted["Location"]
    assert _issued_to(crm) == (str(org["user"].id), org["organization_id"])


def test_use_another_account_lets_a_different_person_sign_in(
    client,
    signing_key,
    sso_product,
    entitled_org,
    second_org_for,
    signed_in,
    with_password,
    password_login,
):
    crm = sso_product("crm")
    first = entitled_org("crm")
    second_org_for(first["user"], "crm")
    other = entitled_org("crm")
    signed_in(first["user"], sign_in_preference="auto_sso")

    asked = _authorize(client, crm, None, prompt="select_account")
    assert path_of(asked) == "/product-access"
    switched = _choose(client, resume_token_of(asked), "switch_account")
    assert switched["redirect_url"].startswith("/login?resume=")
    token = query_of(_Location(switched["redirect_url"]))["resume"][0]

    password_login(client, with_password(other["user"]))
    granted = client.get(RESUME, {"resume": token})
    assert _granted(granted, crm), granted["Location"]
    assert _issued_to(crm) == (str(other["user"].id), other["organization_id"])


def test_ask_each_time_with_several_organizations_completes_after_continue(
    client, signing_key, sso_product, entitled_org, second_org_for, signed_in
):
    crm = sso_product("crm", mode="sso_optional")
    org = entitled_org("crm")
    second_org_for(org["user"], "crm")
    signed_in(org["user"], sign_in_preference="ask")

    parked = _authorize(client, crm, None)
    assert path_of(parked) == "/organizations/select"
    asked = client.get(
        RESUME,
        {"resume": resume_token_of(parked), "organization_id": org["organization_id"]},
    )
    assert path_of(asked) == "/product-access"

    answered = _choose(client, resume_token_of(asked), "workeasy360")
    again = client.get(answered["redirect_url"])
    assert path_of(again) == "/organizations/select", again["Location"]
    granted = client.get(
        RESUME,
        {"resume": resume_token_of(again), "organization_id": org["organization_id"]},
    )
    assert _granted(granted, crm), granted["Location"]
    assert _issued_to(crm) == (str(org["user"].id), org["organization_id"])


def test_an_organization_asked_after_the_answer_is_asked_of_that_person_only(
    client,
    signing_key,
    sso_product,
    entitled_org,
    second_org_for,
    signed_in,
    with_password,
    password_login,
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    second_org_for(org["user"], "crm")
    other = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="auto_sso")

    asked = _authorize(client, crm, None, prompt="select_account")
    answered = _choose(client, resume_token_of(asked), "workeasy360")
    to_organizations = client.get(answered["redirect_url"])
    token = resume_token_of(to_organizations)

    client.logout()
    password_login(client, with_password(other["user"]))
    refused = client.get(
        RESUME, {"resume": token, "organization_id": other["organization_id"]}
    )
    assert "error=resume_invalid" in refused["Location"]
    assert _codes(crm) == 0


class _Location(dict):
    """A bare URL in the shape ``query_of`` reads."""

    def __init__(self, url: str):
        super().__init__(Location=url)
