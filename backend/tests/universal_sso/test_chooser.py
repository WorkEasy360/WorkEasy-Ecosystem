"""The access chooser (ADR-044, design section 3.3): what it shows, what each
answer does, and that no answer can skip a check or reach another person."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.universal_sso.conftest import (
    AUTHORIZE,
    CHOOSE,
    LOGIN_URL,
    PENDING,
    authorize_query,
    path_of,
    query_of,
    resume_token_of,
)
from workeasy360.platform.ecosystem import protocol

pytestmark = pytest.mark.django_db


@pytest.fixture
def asked(client, signing_key, sso_product, entitled_org, signed_in):
    """A signed-in person (default: ask) whose product sign-in is parked at
    the chooser. ``mode`` is the product's sign-in mode."""

    def make(mode: str = "sso_optional", code: str = "crm"):
        product = sso_product(code, mode=mode)
        org = entitled_org(code)
        signed_in(org["user"], sign_in_preference=None)
        verifier, query = authorize_query(product, org["organization_id"])
        response = client.get(AUTHORIZE, query)
        assert path_of(response) == "/product-access"
        return {
            "product": product,
            "org": org,
            "token": resume_token_of(response),
            "verifier": verifier,
        }

    return make


def _choose(client, token, choice, remember=False):
    return client.post(
        CHOOSE,
        {"resume": token, "choice": choice, "remember": remember},
        content_type="application/json",
    )


def test_the_chooser_shows_display_facts_and_nothing_of_the_request(client, asked):
    flow = asked()
    body = client.get(PENDING, {"resume": flow["token"]}).json()
    assert body["product"] == {"code": "crm", "name": "CRM", "icon": ""}
    assert body["organization"]["name"]
    assert body["account"]["email"] == flow["org"]["user"].email
    assert body["choices"] == {"workeasy360": True, "product": True}
    assert body["remembered"] == "inherit"
    flat = repr(body)
    for secret in (
        "client-state",
        "client-nonce",
        flow["product"]["redirect_uri"],
        flow["product"]["client_id"],
    ):
        assert secret not in flat


def test_an_sso_only_product_offers_no_sign_in_of_its_own(client, asked):
    flow = asked(mode="sso_only")
    assert client.get(PENDING, {"resume": flow["token"]}).json()["choices"] == {
        "workeasy360": True,
        "product": False,
    }
    refused = _choose(client, flow["token"], "product")
    assert refused.status_code == 400 and refused.json()["code"] == "pending_invalid"


def test_continuing_releases_the_request_to_resume_which_rechecks_and_issues_the_code(
    client, asked, product_client
):
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    flow = asked()
    answered = _choose(client, flow["token"], "workeasy360")
    assert answered.status_code == 200
    resume_url = answered.json()["redirect_url"]
    assert resume_url == f"/api/v1/integrations/oauth/resume?resume={flow['token']}"
    granted = client.get(resume_url)
    assert granted["Location"].startswith(flow["product"]["redirect_uri"] + "?")
    params = query_of(granted)
    assert params["state"] == ["client-state"]
    reference = product_client(flow["product"])
    tokens = reference.exchange_code(
        code=params["code"][0], code_verifier=flow["verifier"]
    )
    claims = reference.verify_id_token(tokens["id_token"], expected_nonce="client-nonce")
    assert claims["sub"] == str(flow["org"]["user"].id)
    # Not remembered: the next sign-in asks again.
    assert not UserSignInPreference.objects.exists()


def test_remembering_continue_makes_this_product_automatic_and_only_this_one(
    client, asked
):
    from workeasy360.platform.ecosystem.models import UserSignInPreference
    from workeasy360.platform.identity.models import AccountEvent

    flow = asked()
    _choose(client, flow["token"], "workeasy360", remember=True)
    rows = list(UserSignInPreference.objects.values_list("product_code", "mode"))
    assert rows == [("crm", "auto_sso")]
    assert AccountEvent.objects.filter(
        user=flow["org"]["user"],
        kind=AccountEvent.Kind.SIGN_IN_PREFERENCE_CHANGED,
        reason="crm=auto_sso",
    ).exists()
    # The next sign-in to this product is silent.
    _verifier, query = authorize_query(flow["product"], flow["org"]["organization_id"])
    assert "code" in query_of(client.get(AUTHORIZE, query))


def test_choosing_the_products_own_sign_in_goes_to_its_registered_page(client, asked):
    from workeasy360.platform.ecosystem.models import (
        PendingAuthorization,
        UserSignInPreference,
    )

    flow = asked()
    answered = _choose(client, flow["token"], "product", remember=True)
    assert answered.json()["redirect_url"] == LOGIN_URL
    assert PendingAuthorization.objects.get().consumed_at is not None  # spent
    assert list(UserSignInPreference.objects.values_list("product_code", "mode")) == [
        ("crm", "product_login")
    ]
    # The spent request can no longer be resumed.
    resumed = client.get(f"/api/v1/integrations/oauth/resume?resume={flow['token']}")
    assert "resume_invalid" in resumed["Location"]


def test_cancelling_tells_the_product_the_person_declined(client, asked):
    flow = asked()
    answered = _choose(client, flow["token"], "cancel")
    target = answered.json()["redirect_url"]
    assert target.startswith(flow["product"]["redirect_uri"] + "?")
    from urllib.parse import parse_qs, urlsplit

    params = parse_qs(urlsplit(target).query)
    assert params["error"] == [protocol.ACCESS_DENIED]
    assert params["state"] == ["client-state"]
    assert "code" not in params


def test_a_resume_link_cannot_skip_the_question(client, asked):
    flow = asked()
    skipped = client.get(f"/api/v1/integrations/oauth/resume?resume={flow['token']}")
    assert path_of(skipped) == "/product-access"
    assert query_of(skipped)["resume"] == [flow["token"]]
    # Still answerable afterwards: skipping spent nothing.
    assert _choose(client, flow["token"], "workeasy360").status_code == 200


def test_continue_is_refused_if_the_product_lost_its_credential_meanwhile(
    client, asked, platform_admin, in_tenant
):
    from workeasy360.platform.ecosystem import authorization, services

    flow = asked()
    with in_tenant(platform_admin["ctx"]):
        services.revoke_credential(
            platform_admin["ctx"],
            client_id=flow["product"]["client_id"],
            credential_id=flow["product"]["credential_id"],
            reason="lost meanwhile",
        )
    assert _choose(client, flow["token"], "workeasy360", remember=True).status_code == 400
    parked = authorization.find_pending(token=flow["token"])
    assert parked is not None and parked.awaiting_choice  # not released, not remembered


def test_an_answer_works_once(client, asked):
    flow = asked()
    assert _choose(client, flow["token"], "workeasy360").status_code == 200
    assert _choose(client, flow["token"], "workeasy360").status_code == 400
    assert _choose(client, flow["token"], "cancel").status_code == 400


def test_the_request_belongs_to_the_person_it_was_parked_for(asked, user_factory, client):
    flow = asked()
    intruder = Client()
    intruder.force_login(user_factory())
    assert intruder.get(PENDING, {"resume": flow["token"]}).status_code == 404
    assert _choose(intruder, flow["token"], "workeasy360").status_code == 400
    # Released by its person, then resumed in someone else's session: spent and refused.
    assert _choose(client, flow["token"], "workeasy360").status_code == 200
    stolen = intruder.get(f"/api/v1/integrations/oauth/resume?resume={flow['token']}")
    assert "resume_invalid" in stolen["Location"]
    again = client.get(f"/api/v1/integrations/oauth/resume?resume={flow['token']}")
    assert "resume_invalid" in again["Location"]


def test_signed_out_and_unknown_requests_get_the_uniform_answer(asked):
    flow = asked()
    anonymous = Client()
    assert anonymous.get(PENDING, {"resume": flow["token"]}).status_code == 401
    assert _choose(anonymous, flow["token"], "workeasy360").status_code == 401


def test_an_unknown_token_or_choice_is_refused(client, asked):
    flow = asked()
    assert client.get(PENDING, {"resume": "not-a-token"}).status_code == 404
    assert _choose(client, flow["token"], "everything").status_code == 400
    assert _choose(client, "not-a-token", "workeasy360").status_code == 400


def test_using_another_account_signs_this_one_out_and_the_next_person_gets_their_own_code(
    asked,
    client,
    organization_factory,
    entitled,
    with_password,
    password_login,
    product_client,
):
    """Account switching on the chooser: the first person's whole sign-in
    ends, and whoever signs in next resumes the same verified request -
    every check running for *them*, the code naming *them*."""
    from workeasy360.platform.entitlements.services.seats import assign_seat
    from workeasy360.platform.identity.models import UserSession
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    flow = asked()
    first = flow["org"]["user"]
    answered = _choose(client, flow["token"], "switch_account")
    target = answered.json()["redirect_url"]
    assert target.startswith("/login?resume=")
    assert not UserSession.objects.filter(user=first).exists()  # every session of it
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}

    # The second person: a member of the same organization, with a seat.
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    second = with_password(organization_factory()[2])
    org = flow["org"]["org"]
    with (
        unscoped_access(reason="Test adds a member to an organization", module="tests"),
        organization_db_scope(org.id),
    ):
        membership = Membership(
            organization_id=org.id,
            user_id=second.id,
            status="active",
            member_type="internal",
        )
        membership.save()
    with tenant_context(flow["org"]["ctx"]), tenant_db_scope(flow["org"]["ctx"]):
        assign_seat(org.id, pool_key="crm", membership_id=membership.id)
    from workeasy360.platform.organizations import projection

    projection.sync_access_for_membership(
        user_id=second.id, membership=membership, organization=org
    )

    password_login(client, second)
    new_token = target.split("resume=", 1)[1]
    granted = client.get(f"/api/v1/integrations/oauth/resume?resume={new_token}")
    assert granted["Location"].startswith(flow["product"]["redirect_uri"] + "?")
    reference = product_client(flow["product"])
    tokens = reference.exchange_code(
        code=query_of(granted)["code"][0], code_verifier=flow["verifier"]
    )
    claims = reference.verify_id_token(tokens["id_token"], expected_nonce="client-nonce")
    assert claims["sub"] == str(second.id)
    assert claims["sub"] != str(first.id)


def test_an_answer_needs_the_csrf_token(signing_key, sso_product, entitled_org):
    product = sso_product("crm", mode="sso_optional")
    org = entitled_org("crm")
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(org["user"])
    strict.get("/api/v1/auth/session")
    _verifier, query = authorize_query(product, org["organization_id"])
    token = resume_token_of(strict.get(AUTHORIZE, query))
    body = {"resume": token, "choice": "workeasy360", "remember": False}
    assert strict.post(CHOOSE, body, content_type="application/json").status_code == 403
    csrf = strict.cookies["w360_csrf"].value
    assert (
        strict.post(
            CHOOSE, body, content_type="application/json", HTTP_X_CSRFTOKEN=csrf
        ).status_code
        == 200
    )


def test_the_token_endpoint_still_rechecks_after_a_chooser_answer(
    client, asked, product_client
):
    """Removed between the answer and the exchange: no token."""
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope

    flow = asked()
    _choose(client, flow["token"], "workeasy360")
    granted = client.get(f"/api/v1/integrations/oauth/resume?resume={flow['token']}")
    code = query_of(granted)["code"][0]
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org = flow["org"]["org"]
    with (
        unscoped_access(reason="Test suspends a member", module="tests"),
        organization_db_scope(org.id),
    ):
        Membership.unscoped.filter(pk=flow["org"]["membership"].pk).update(
            status="suspended"
        )
    reference = product_client(flow["product"])
    from tests.factories.reference_product_client import IntegrationError

    with pytest.raises(IntegrationError) as refused:
        reference.exchange_code(code=code, code_verifier=flow["verifier"])
    assert refused.value.error == protocol.INVALID_GRANT
