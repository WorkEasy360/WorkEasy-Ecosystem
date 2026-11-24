"""Accounts V3 (ADR-075): "Use another account" on a launch for someone else's
organization offers the new person their own organizations (V3 6.5, 7.1), and
a revoked sign-in stops working on the next request - userinfo and a code
minted just before the revocation included (V3 9.2)."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.universal_sso.conftest import (
    AUTHORIZE,
    CHOOSE,
    authorize_query,
    path_of,
    query_of,
    resume_token_of,
)

pytestmark = pytest.mark.django_db

RESUME = "/api/v1/integrations/oauth/resume"
USERINFO = "/api/v1/integrations/oauth/userinfo"


def _choose(client, token, choice):
    return client.post(
        CHOOSE,
        {"resume": token, "choice": choice, "remember": False},
        content_type="application/json",
    )


@pytest.fixture
def ravi_asked(client, signing_key, sso_product, entitled_org, signed_in):
    """Ravi, signed in and set to "ask", launches CRM for his organization:
    the access chooser."""
    product = sso_product("crm", mode="sso_optional")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="ask")
    verifier, query = authorize_query(product, org["organization_id"])
    asked = client.get(AUTHORIZE, query)
    assert path_of(asked) == "/product-access"
    return {
        "product": product,
        "org": org,
        "token": resume_token_of(asked),
        "verifier": verifier,
    }


def test_another_person_is_offered_their_own_organizations_not_a_dead_end(
    client, ravi_asked, organization_factory, with_password, password_login
):
    _org, _membership, priya = organization_factory()
    priya = with_password(priya)
    answered = _choose(client, ravi_asked["token"], "switch_account")
    target = answered.json()["redirect_url"]
    password_login(client, priya)
    resumed = client.get(f"{RESUME}?resume={target.split('resume=', 1)[1]}")

    # Priya has exactly one organization of her own: the product's sign-in
    # restarts with it - no code for Ravi's organization, nothing of it shown.
    location = resumed["Location"]
    assert location.startswith("http://localhost:9100/launch?")
    assert query_of(resumed)["organization"] == [str(_org.id)]
    assert str(ravi_asked["org"]["organization_id"]) not in location
    assert "code" not in query_of(resumed)


def test_a_person_with_several_organizations_chooses_and_membership_is_checked_again(
    client, ravi_asked, organization_factory, with_password, password_login
):
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    first_org, _m, priya = organization_factory()
    second_org, _m2, _other_owner = organization_factory()
    with (
        unscoped_access(reason="Test adds a member to an organization", module="tests"),
        organization_db_scope(second_org.id),
    ):
        membership = Membership(
            organization_id=second_org.id,
            user_id=priya.id,
            status="active",
            member_type="internal",
        )
        membership.save()
    projection.sync_access_for_membership(
        user_id=priya.id, membership=membership, organization=second_org
    )
    priya = with_password(priya)
    target = _choose(client, ravi_asked["token"], "switch_account").json()["redirect_url"]
    password_login(client, priya)
    resumed = client.get(f"{RESUME}?resume={target.split('resume=', 1)[1]}")
    assert path_of(resumed) == "/organizations/select"
    token = resume_token_of(resumed)

    # Ravi's organization, typed into the chooser's answer, is still refused.
    sneaked = client.get(
        f"{RESUME}?resume={token}&organization_id={ravi_asked['org']['organization_id']}"
    )
    assert "error=" in sneaked["Location"] and "code=" not in sneaked["Location"]
    assert "localhost:9100/launch" not in sneaked["Location"]


def test_the_chosen_organization_restarts_the_product_with_it(
    client, ravi_asked, organization_factory, with_password, password_login
):
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    _first, _m, priya = organization_factory()
    second_org, _m2, _owner = organization_factory()
    with (
        unscoped_access(reason="Test adds a member to an organization", module="tests"),
        organization_db_scope(second_org.id),
    ):
        membership = Membership(
            organization_id=second_org.id,
            user_id=priya.id,
            status="active",
            member_type="internal",
        )
        membership.save()
    projection.sync_access_for_membership(
        user_id=priya.id, membership=membership, organization=second_org
    )
    priya = with_password(priya)
    target = _choose(client, ravi_asked["token"], "switch_account").json()["redirect_url"]
    password_login(client, priya)
    offered = client.get(f"{RESUME}?resume={target.split('resume=', 1)[1]}")
    token = resume_token_of(offered)
    restarted = client.get(f"{RESUME}?resume={token}&organization_id={second_org.id}")
    assert restarted["Location"].startswith("http://localhost:9100/launch?")
    assert query_of(restarted)["organization"] == [str(second_org.id)]


def test_a_person_with_no_organization_gets_the_refusal_as_before(
    client, ravi_asked, user_factory, with_password, password_login
):
    stranger = with_password(user_factory(email="no.org@example.com"))
    target = _choose(client, ravi_asked["token"], "switch_account").json()["redirect_url"]
    password_login(client, stranger)
    resumed = client.get(f"{RESUME}?resume={target.split('resume=', 1)[1]}")
    assert resumed["Location"].startswith(ravi_asked["product"]["redirect_uri"] + "?")
    assert query_of(resumed).get("error")
    assert "code" not in query_of(resumed)


# --- revocation on the next request ------------------------------------------------------


def _signed_in_product(client, sso_product, entitled_org, password_login):
    """A person signed in with a real password, and CRM's authorization
    request answered from that sign-in (so the code names the sign-in)."""
    from tests.universal_sso.conftest import PASSWORD

    org = entitled_org("crm")
    product = sso_product("crm", scopes=("openid", "profile"))
    user = org["user"]
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    password_login(client, user)
    verifier, query = authorize_query(
        product, org["organization_id"], scope="openid profile"
    )
    granted = client.get(AUTHORIZE, query)
    if path_of(granted) == "/product-access":
        granted = client.get(
            _choose(client, resume_token_of(granted), "workeasy360").json()[
                "redirect_url"
            ]
        )
    return org, product, verifier, granted


def test_userinfo_stops_answering_after_sign_out_everywhere(
    client, signing_key, sso_product, entitled_org, password_login, product_client
):
    from workeasy360.platform.identity import account

    org, product, verifier, granted = _signed_in_product(
        client, sso_product, entitled_org, password_login
    )
    reference = product_client(product)
    issued = reference.exchange_code(
        code=query_of(granted)["code"][0], code_verifier=verifier
    )
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {issued['access_token']}"}
    assert Client().get(USERINFO, **bearer).status_code == 200

    account.sign_out_everywhere(org["user"].pk)
    refused = Client().get(USERINFO, **bearer)
    assert refused.status_code == 401


@pytest.mark.parametrize("ended_by", ["this_device", "other_devices"])
def test_userinfo_stops_answering_when_only_that_sign_in_ends(
    ended_by,
    client,
    signing_key,
    sso_product,
    entitled_org,
    password_login,
    product_client,
):
    """Second review, M1: a product without back-channel logout. Ending one
    sign-in moves no epoch, so its access token names the sign-in
    (``w360_sin``) and userinfo checks that it is still open."""
    import jwt as pyjwt

    from workeasy360.platform.identity import account
    from workeasy360.platform.identity.models import UserSession

    org, product, verifier, granted = _signed_in_product(
        client, sso_product, entitled_org, password_login
    )
    issued = product_client(product).exchange_code(
        code=query_of(granted)["code"][0], code_verifier=verifier
    )
    claims = pyjwt.decode(issued["access_token"], options={"verify_signature": False})
    assert claims["w360_sin"] and "sid" not in claims
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {issued['access_token']}"}
    assert Client().get(USERINFO, **bearer).status_code == 200

    user_id = org["user"].pk
    if ended_by == "this_device":
        session = UserSession.objects.filter(user_id=user_id).first()
        assert account.revoke_session(user_id, session.public_id) >= 1
    else:
        assert account.revoke_other_sessions(user_id, keep_public_id=None) >= 1
    assert Client().get(USERINFO, **bearer).status_code == 401


def test_a_code_minted_just_before_its_sign_in_ended_is_not_redeemed(
    client, signing_key, sso_product, entitled_org, password_login, product_client
):
    from workeasy360.platform.identity.models import UserSession

    org, product, verifier, granted = _signed_in_product(
        client, sso_product, entitled_org, password_login
    )
    code = query_of(granted)["code"][0]
    # "Sign out of this device", between the redirect and the exchange.
    UserSession.objects.filter(user=org["user"]).delete()
    reference = product_client(product)
    with pytest.raises(Exception) as refused:
        reference.exchange_code(code=code, code_verifier=verifier)
    assert "invalid, expired or already used" in str(refused.value)


# --- Third review (24 November) -----------------------------------------------------------


def test_own_organization_refused_answers_the_product_about_its_own_request(
    client, ravi_asked, organization_factory, with_password, password_login
):
    """L-5: Priya's only organization refuses her network. The product asked
    for Ravi's organization, so it is told she cannot open that one -
    never a refusal about an organization it did not ask for."""
    from workeasy360.platform.organizations.models import Organization

    _org, _membership, priya = organization_factory()
    Organization.objects.filter(id=_org.id).update(
        ip_allowlist_enabled=True, ip_allowlist=["10.0.0.0/8"]
    )
    priya = with_password(priya)
    answered = _choose(client, ravi_asked["token"], "switch_account")
    target = answered.json()["redirect_url"]
    password_login(client, priya)
    resumed = client.get(f"{RESUME}?resume={target.split('resume=', 1)[1]}")

    answer = query_of(resumed)
    assert answer["error"] == ["membership_inactive"]
    assert "access_policy_denied" not in resumed["Location"]
    assert str(_org.id) not in resumed["Location"]
    assert "code" not in answer


def test_a_password_change_stops_old_product_tokens_and_sso_signs_straight_back_in(
    client, signing_key, sso_product, entitled_org, password_login, product_client
):
    """L-4, by design (ADR-069/075): a new password ends every product
    session, this browser's included, so the old access token answers 401 at
    userinfo. The browser stays signed in to WorkEasy360, so the product's
    next sign-in goes straight through and its new token answers."""
    from tests.universal_sso.conftest import PASSWORD

    org, product, verifier, granted = _signed_in_product(
        client, sso_product, entitled_org, password_login
    )
    reference = product_client(product)
    first = reference.exchange_code(
        code=query_of(granted)["code"][0], code_verifier=verifier
    )
    old = {"HTTP_AUTHORIZATION": f"Bearer {first['access_token']}"}
    assert Client().get(USERINFO, **old).status_code == 200

    changed = client.post(
        "/api/v1/auth/password",
        {
            "current_password": PASSWORD,
            "new_password": "An entirely new passphrase 8812!",  # pragma: allowlist secret
        },
        content_type="application/json",
    )
    assert changed.status_code == 200
    refused = Client().get(USERINFO, **old)
    assert refused.status_code == 401
    assert 'error="invalid_token"' in refused["WWW-Authenticate"]

    verifier, query = authorize_query(
        product, org["organization_id"], scope="openid profile"
    )
    again = client.get(AUTHORIZE, query)
    second = reference.exchange_code(
        code=query_of(again)["code"][0], code_verifier=verifier
    )
    new = {"HTTP_AUTHORIZATION": f"Bearer {second['access_token']}"}
    assert Client().get(USERINFO, **new).status_code == 200
