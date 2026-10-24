"""The "one login for all products" brief's ARCHITECTURE GUARD
(docs/universal-sso/one-login.md).

Most of the brief's list was already pinned by ``test_architecture.py`` (no
product branch, no password to a product, one identity provider, the hostname
grants nothing, the registry) and by the scenarios in ``test_authorize.py``.
This module adds what the brief's changes made necessary:

- **Automatic is now the default**, so a person who never chose anything must
  meet every refusal an explicit "automatic" meets: entitlement, seat,
  another organization, an ended trial;
- **no duplicate identity implementation**: one person model holds a
  password, and one endpoint takes one;
- **a product opened by typing its address names no organization**: the
  person's only organization is used - and still checked like a named one -
  while someone in several organizations is asked (ADR-044 item 16);
- an index of the brief's guards, so deleting one fails here.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest

from tests.universal_sso.conftest import (
    AUTHORIZE,
    CHOOSE,
    PENDING,
    RESUME,
    authorize_query,
    path_of,
    query_of,
    resume_token_of,
)
from workeasy360.platform.ecosystem import protocol

pytestmark = pytest.mark.django_db


def _authorize(client, registration, org_id):
    _verifier, query = authorize_query(registration, org_id)
    return client.get(AUTHORIZE, query)


def _codes(registration) -> int:
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    return AuthorizationCode.objects.filter(
        integration__client_id=registration["client_id"]
    ).count()


# --- the automatic default is no way around anything -------------------------------------------------


def test_the_default_is_no_way_around_entitlement(
    client, signing_key, sso_product, organization_factory, signed_in
):
    crm = sso_product("crm")
    org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference=None)
    assert query_of(_authorize(client, crm, str(org.id)))["error"] == [
        protocol.PRODUCT_NOT_ENTITLED
    ]
    assert _codes(crm) == 0


def test_the_default_is_no_way_around_a_seat(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm", seat=False)
    signed_in(org["user"], sign_in_preference=None)
    assert query_of(_authorize(client, crm, org["organization_id"]))["error"] == [
        protocol.PRODUCT_NO_SEAT
    ]
    assert _codes(crm) == 0


def test_the_default_is_no_way_into_another_organization(
    client, signing_key, sso_product, entitled_org, signed_in
):
    """Cross-organization SSO: being signed in, entitled elsewhere and set to
    Automatic opens nothing in an organization the person is not a member of."""
    crm = sso_product("crm")
    mine, theirs = entitled_org("crm"), entitled_org("crm")
    signed_in(mine["user"], sign_in_preference=None)
    assert query_of(_authorize(client, crm, theirs["organization_id"]))["error"] == [
        protocol.MEMBERSHIP_INACTIVE
    ]
    assert _codes(crm) == 0


def test_the_default_is_no_way_past_the_end_of_a_trial(
    client, signing_key, sso_product, trial_org, signed_in, monkeypatch
):
    """Trial access requires an active trial: silent sign-in while the 7-day
    trial runs; from ``ends_at`` (server time, before any sweeper) refused."""
    from django.utils import timezone

    books = sso_product("books")
    trial = trial_org("books")
    signed_in(trial["user"], sign_in_preference=None)
    assert "code" in query_of(_authorize(client, books, trial["organization_id"]))

    monkeypatch.setattr(timezone, "now", lambda: trial["ends_at"] + timedelta(seconds=1))
    signed_in(trial["user"], sign_in_preference=None)
    assert query_of(_authorize(client, books, trial["organization_id"]))["error"] == [
        protocol.PRODUCT_NOT_ENTITLED
    ]


def test_the_default_is_no_way_into_a_product_that_is_not_ready(
    client, signing_key, sso_product, entitled_org, signed_in
):
    """A registration without a usable credential is refused, never signed in
    silently because the person's default is Automatic."""
    crm = sso_product("crm", activate=False)
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    response = _authorize(client, crm, org["organization_id"])
    assert "code" not in query_of(response)
    assert _codes(crm) == 0


# --- a product opened directly names no organization ------------------------------------------------


def test_typing_a_products_address_signs_a_one_organization_person_in_to_it(
    client, signing_key, sso_product, entitled_org, signed_in, product_client
):
    """Journey B: no organization hint (the product cannot know one), one
    organization - signed in silently, bound to that organization."""
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    reference = product_client(crm)
    request = reference.begin_authorization(organization_id="")
    request["query"].pop("organization")  # typed address: nothing to name
    granted = client.get(request["url"], request["query"])
    assert granted["Location"].startswith(crm["redirect_uri"] + "?")
    code = reference.handle_callback(granted["Location"], expected_state=request["state"])
    tokens = reference.exchange_code(code=code, code_verifier=request["code_verifier"])
    claims = reference.verify_id_token(
        tokens["id_token"], expected_nonce=request["nonce"]
    )
    assert claims["organization_id"] == org["organization_id"]
    assert claims["sub"] == str(org["user"].id)


def test_someone_in_several_organizations_is_asked_which(
    client, signing_key, sso_product, entitled_org, second_org_for, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    second_org_for(org["user"], "crm")
    signed_in(org["user"], sign_in_preference=None)
    response = _authorize(client, crm, None)
    assert path_of(response) == "/organizations/select"
    assert _codes(crm) == 0


def test_the_only_organization_is_checked_like_a_named_one(
    client, signing_key, sso_product, organization_factory, entitled_org, signed_in
):
    """Implied is not granted: an organization without the product refuses
    (the product shows Start trial / Buy), and so does a missing seat."""
    crm = sso_product("crm")
    _org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference=None)
    assert query_of(_authorize(client, crm, None))["error"] == [
        protocol.PRODUCT_NOT_ENTITLED
    ]
    unseated = entitled_org("crm", seat=False)
    signed_in(unseated["user"], sign_in_preference=None)
    assert query_of(_authorize(client, crm, None))["error"] == [protocol.PRODUCT_NO_SEAT]
    assert _codes(crm) == 0


def test_after_the_central_sign_in_the_only_organization_is_used(
    client, signing_key, sso_product, entitled_org, signed_in
):
    """No Workeasy360 session: the central sign-in, then straight on to the
    product - no organization question for a person with one."""
    crm = sso_product("crm")
    org = entitled_org("crm")
    parked = _authorize(client, crm, None)
    assert path_of(parked) == "/login"
    signed_in(org["user"], sign_in_preference=None)
    resumed = client.get(RESUME, {"resume": resume_token_of(parked)})
    assert resumed["Location"].startswith(crm["redirect_uri"] + "?")
    assert query_of(resumed)["code"][0]


def test_asked_with_no_organization_named_the_chooser_shows_the_implied_one(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm", mode="sso_optional")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="ask")
    parked = _authorize(client, crm, None)
    assert path_of(parked) == "/product-access"
    token = resume_token_of(parked)
    shown = client.get(PENDING, {"resume": token}).json()
    assert shown["organization"]["name"]
    answered = client.post(
        CHOOSE,
        {"resume": token, "choice": "workeasy360"},
        content_type="application/json",
    )
    granted = client.get(answered.json()["redirect_url"])
    assert granted["Location"].startswith(crm["redirect_uri"] + "?")


def test_prompt_none_with_one_organization_completes_silently(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    _verifier, query = authorize_query(crm, None, prompt="none")
    response = client.get(AUTHORIZE, query)
    assert response["Location"].startswith(crm["redirect_uri"] + "?")
    assert query_of(response)["code"][0]


def test_an_implied_organization_that_cannot_be_signed_in_to_is_refused_as_such(
    client, signing_key, sso_product, organization_factory, signed_in
):
    from workeasy360.contracts.enums import OrganizationStatus

    crm = sso_product("crm")
    # Locked (a suspended organization still signs in, read-only).
    _org, _membership, user = organization_factory(status=OrganizationStatus.LOCKED)
    signed_in(user, sign_in_preference=None)
    assert query_of(_authorize(client, crm, None))["error"] == [
        protocol.ORGANIZATION_INACTIVE
    ]
    assert _codes(crm) == 0


def test_prompt_none_with_several_organizations_reports_the_question(
    client, signing_key, sso_product, entitled_org, second_org_for, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    second_org_for(org["user"], "crm")
    signed_in(org["user"], sign_in_preference=None)
    _verifier, query = authorize_query(crm, None, prompt="none")
    response = client.get(AUTHORIZE, query)
    assert query_of(response)["error"] == [protocol.INTERACTION_REQUIRED]
    assert _codes(crm) == 0


def test_another_account_on_the_chooser_gets_its_own_organization_not_the_first_ones(
    client,
    signing_key,
    sso_product,
    entitled_org,
    signed_in,
    with_password,
    password_login,
    product_client,
):
    """Review finding (P2): the first person's implied organization must not
    travel with the request to whoever signs in next."""
    crm = sso_product("crm", mode="sso_optional")
    first = entitled_org("crm")
    signed_in(first["user"], sign_in_preference="ask")
    reference = product_client(crm)
    request = reference.begin_authorization(organization_id="")
    request["query"].pop("organization")
    parked = client.get(request["url"], request["query"])
    assert path_of(parked) == "/product-access"
    shown = client.get(PENDING, {"resume": resume_token_of(parked)}).json()
    assert shown["organization"]["name"] == first["org"].display_name
    switched = client.post(
        CHOOSE,
        {"resume": resume_token_of(parked), "choice": "switch_account"},
        content_type="application/json",
    ).json()["redirect_url"]
    token = switched.split("resume=")[1].split("&")[0]

    second = entitled_org("crm")
    password_login(client, with_password(second["user"]))
    granted = client.get(RESUME, {"resume": token})
    assert granted["Location"].startswith(crm["redirect_uri"] + "?")
    code = reference.handle_callback(granted["Location"], expected_state=request["state"])
    tokens = reference.exchange_code(code=code, code_verifier=request["code_verifier"])
    claims = reference.verify_id_token(
        tokens["id_token"], expected_nonce=request["nonce"]
    )
    assert claims["sub"] == str(second["user"].id)
    assert claims["organization_id"] == second["organization_id"]


def test_a_resume_link_opened_in_a_signed_in_browser_still_asks_who_chose_ask(
    client, signing_key, sso_product, entitled_org, signed_in
):
    """Review finding (P3): a request parked for sign-in elsewhere, resumed by
    someone already signed in here, applies their preference - no silent code
    for a person set to "Ask me each time"."""
    from django.test import Client

    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="ask")
    parked = _authorize(Client(), crm, None)  # another browser, no session
    assert path_of(parked) == "/login"
    resumed = client.get(RESUME, {"resume": resume_token_of(parked)})
    assert path_of(resumed) == "/product-access"
    assert _codes(crm) == 0


def test_whoever_just_signed_in_for_this_request_is_not_asked_again(
    client, signing_key, sso_product, entitled_org, prefer, with_password, password_login
):
    """The central sign-in for this very request is the person's answer: no
    access chooser after it, even for "Ask me each time" (as before)."""
    crm = sso_product("crm")
    org = entitled_org("crm")
    prefer(org["user"], "ask")
    parked = _authorize(client, crm, None)
    assert path_of(parked) == "/login"
    password_login(client, with_password(org["user"]))
    resumed = client.get(RESUME, {"resume": resume_token_of(parked)})
    assert resumed["Location"].startswith(crm["redirect_uri"] + "?")
    assert query_of(resumed)["code"][0]


def test_an_organization_chosen_by_someone_set_to_ask_then_asks_how(
    client, signing_key, sso_product, entitled_org, second_org_for, signed_in
):
    """Several organizations and "Ask me each time": the organization chooser,
    then the access chooser - the preference is no longer skipped."""
    crm = sso_product("crm", mode="sso_optional")
    org = entitled_org("crm")
    second_org_for(org["user"], "crm")
    signed_in(org["user"], sign_in_preference="ask")
    parked = _authorize(client, crm, None)
    assert path_of(parked) == "/organizations/select"
    resumed = client.get(
        RESUME,
        {"resume": resume_token_of(parked), "organization_id": org["organization_id"]},
    )
    assert path_of(resumed) == "/product-access"
    assert _codes(crm) == 0


def test_the_log_says_where_the_organization_came_from(
    client, signing_key, sso_product, entitled_org, signed_in, caplog
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    with caplog.at_level("DEBUG"):
        _authorize(client, crm, None)
        _authorize(client, crm, org["organization_id"])
    assert "organization_source" in caplog.text
    assert "implied" in caplog.text and "named" in caplog.text


def test_only_the_authorization_endpoint_implies_an_organization():
    """``sole_organization_id`` is reached only through ``organization_for``,
    and ``resolve_subject`` itself still refuses a request with none."""
    import pathlib
    import re

    backend = pathlib.Path(__file__).resolve().parents[2] / "workeasy360"
    callers = sorted(
        path.relative_to(backend).as_posix()
        for path in backend.rglob("*.py")
        if "/tests/" not in path.as_posix()
        and re.search(r"\bsole_organization_id\(", path.read_text(encoding="utf-8"))
    )
    assert callers == [
        "platform/ecosystem/authorization.py",
        "platform/organizations/resolver.py",
    ]
    from uuid import uuid4

    from workeasy360.platform.ecosystem import authorization

    with pytest.raises(protocol.OAuthError) as refused:
        authorization.resolve_subject(user_id=uuid4(), organization_id=None)
    assert refused.value.error == protocol.ORGANIZATION_REQUIRED


# --- one identity implementation ------------------------------------------------------------------------


def test_one_person_model_holds_a_password():
    """No product, partner or integration table keeps a credential of a person:
    the identity user is the only model with a password column."""
    from django.apps import apps
    from django.conf import settings

    assert settings.AUTH_USER_MODEL == "identity.User"
    holders = sorted(
        model._meta.label
        for model in apps.get_models()
        if any(field.name == "password" for field in model._meta.get_fields())
    )
    assert holders == ["identity.User"]


def test_one_endpoint_takes_a_password_to_sign_in():
    """One sign-in endpoint, the central one. Products are OIDC clients: no
    endpoint signs a person in on a product's behalf with a password."""
    from workeasy360.interfaces.api.v1 import urls

    patterns = [str(p.pattern) for p in urls.urlpatterns]
    sign_ins = [p for p in patterns if p.endswith("auth/login")]
    assert sign_ins == ["auth/login"]
    assert not [p for p in patterns if "password" in p and "integrations" in p]


# --- the brief's list, indexed ---------------------------------------------------------------------------

#: The brief's ARCHITECTURE GUARD, each to the test that pins it.
GUARDS = {
    "Workeasy360 password is never sent to a product": [
        (
            "test_architecture",
            "test_passwords_never_cross_the_product_boundary_from_the_backend",
        ),
        (
            "test_architecture",
            "test_passwords_never_cross_the_product_boundary_from_the_frontend",
        ),
    ],
    "only one central identity provider exists": [
        ("test_architecture", "test_there_is_one_identity_provider"),
    ],
    "product authentication is registry-driven": [
        (
            "test_architecture",
            "test_products_are_oidc_clients_and_future_ones_use_the_registry",
        ),
        (
            "test_architecture",
            "test_every_destination_comes_from_the_registry_and_is_exact",
        ),
    ],
    "no duplicate identity implementation": [
        ("test_one_login_guards", "test_one_person_model_holds_a_password"),
        ("test_one_login_guards", "test_one_endpoint_takes_a_password_to_sign_in"),
    ],
    "product access requires entitlement": [
        ("test_authorize", "test_automatic_sign_in_is_no_way_around_entitlement"),
        ("test_one_login_guards", "test_the_default_is_no_way_around_entitlement"),
        (
            "test_architecture",
            "test_the_preference_is_read_only_after_every_access_check",
        ),
    ],
    "trial access requires an active trial": [
        (
            "test_authorize",
            "test_trial_sso_works_until_the_trials_end_and_not_a_second_after",
        ),
        ("test_one_login_guards", "test_the_default_is_no_way_past_the_end_of_a_trial"),
    ],
    "hostname does not grant authorization": [
        (
            "test_architecture",
            "test_the_hostname_grants_nothing_in_the_sign_in_machinery",
        ),
    ],
    "future products require no platform auth code branch": [
        (
            "test_architecture",
            "test_no_product_specific_authentication_branch_in_the_backend",
        ),
        (
            "test_architecture",
            "test_no_product_specific_authentication_branch_in_the_frontend",
        ),
        ("test_future_product", None),
    ],
}


@pytest.mark.parametrize("guard", sorted(GUARDS))
def test_every_guard_of_the_brief_is_pinned(guard):
    for module_name, test_name in GUARDS[guard]:
        module = importlib.import_module(f"tests.universal_sso.{module_name}")
        if test_name is None:
            assert [n for n in vars(module) if n.startswith("test_")], module_name
        else:
            assert callable(getattr(module, test_name, None)), f"{guard}: {test_name}"
