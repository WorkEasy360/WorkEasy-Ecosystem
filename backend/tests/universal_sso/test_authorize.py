"""The identity provider applies the person's preference (ADR-044, design
section 3.2) - only after every access check, and never as a way around one."""

from __future__ import annotations

import pytest

from tests.universal_sso.conftest import AUTHORIZE, authorize_query, path_of, query_of
from workeasy360.platform.ecosystem import protocol

pytestmark = pytest.mark.django_db


def _authorize(client, registration, org_id, **overrides):
    _verifier, query = authorize_query(registration, org_id, **overrides)
    return client.get(AUTHORIZE, query)


def _codes(registration):
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    return AuthorizationCode.objects.filter(
        integration__client_id=registration["client_id"]
    ).count()


def test_a_person_who_never_chose_is_asked_before_anything_is_minted(
    client, signing_key, sso_product, entitled_org, signed_in
):
    from workeasy360.platform.ecosystem.models import PendingAuthorization

    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    response = _authorize(client, crm, org["organization_id"])
    assert response.status_code == 302
    assert path_of(response) == "/product-access"
    assert _codes(crm) == 0
    parked = PendingAuthorization.objects.get()
    assert parked.awaiting_choice is True
    assert parked.user_id == org["user"].id


def test_automatic_sign_in_reuses_the_session_with_no_question(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="auto_sso")
    response = _authorize(client, crm, org["organization_id"])
    assert response["Location"].startswith(crm["redirect_uri"] + "?")
    params = query_of(response)
    assert params["state"] == ["client-state"] and params["code"][0]
    assert _codes(crm) == 1


def test_a_per_product_override_changes_that_product_only(
    client, signing_key, sso_product, entitled_org, second_org_for, signed_in, prefer
):
    crm, books = sso_product("crm"), sso_product("books")
    org = entitled_org("crm")
    other = second_org_for(org["user"], "books")
    signed_in(org["user"], sign_in_preference="ask")
    prefer(org["user"], "auto_sso", "crm")
    assert _authorize(client, crm, org["organization_id"])["Location"].startswith(
        crm["redirect_uri"]
    )
    assert (
        path_of(_authorize(client, books, other["organization_id"])) == "/product-access"
    )


def test_preferring_the_product_sign_in_still_asks_when_the_product_itself_sent_the_person(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm", mode="sso_optional")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="product_login")
    assert path_of(_authorize(client, crm, org["organization_id"])) == "/product-access"


@pytest.mark.parametrize(
    "prompt", ["select_account", "consent", "select_account consent"]
)
def test_a_product_can_ask_to_confirm_the_account_even_for_automatic_sign_in(
    client, signing_key, sso_product, entitled_org, signed_in, prompt
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="auto_sso")
    assert (
        path_of(_authorize(client, crm, org["organization_id"], prompt=prompt))
        == "/product-access"
    )


def test_prompt_none_never_shows_a_page(
    client, signing_key, sso_product, entitled_org, signed_in, prefer
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    # Asked-for interaction is reported, not performed.
    signed_in(org["user"], sign_in_preference="ask")
    response = _authorize(client, crm, org["organization_id"], prompt="none")
    assert response["Location"].startswith(crm["redirect_uri"] + "?")
    assert query_of(response)["error"] == [protocol.INTERACTION_REQUIRED]
    assert query_of(response)["state"] == ["client-state"]
    assert _codes(crm) == 0
    # An organization that still has to be chosen is interaction too.
    response = _authorize(client, crm, None, prompt="none")
    assert query_of(response)["error"] == [protocol.INTERACTION_REQUIRED]
    # Automatic: silent, as asked.
    prefer(org["user"], "auto_sso")
    assert "code" in query_of(
        _authorize(client, crm, org["organization_id"], prompt="none")
    )


def test_prompt_none_without_a_session_answers_login_required(
    client, signing_key, sso_product, entitled_org
):
    from workeasy360.platform.ecosystem.models import PendingAuthorization

    crm = sso_product("crm")
    org = entitled_org("crm")
    response = _authorize(client, crm, org["organization_id"], prompt="none")
    assert response["Location"].startswith(crm["redirect_uri"] + "?")
    assert query_of(response)["error"] == [protocol.LOGIN_REQUIRED]
    assert not PendingAuthorization.objects.exists()


@pytest.mark.parametrize("prompt", ["none login", "sometimes", "none consent"])
def test_an_unsupported_or_contradictory_prompt_is_refused(
    client, signing_key, sso_product, entitled_org, signed_in, prompt
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    response = _authorize(client, crm, org["organization_id"], prompt=prompt)
    assert query_of(response)["error"] == [protocol.INVALID_REQUEST]


def test_a_local_only_registration_is_never_signed_in(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm", mode="local_only")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="auto_sso")
    response = _authorize(client, crm, org["organization_id"])
    assert query_of(response)["error"] == [protocol.UNAUTHORIZED_CLIENT]
    assert _codes(crm) == 0


def test_a_product_with_no_usable_credential_is_refused_never_offered(
    client, signing_key, sso_product, entitled_org, signed_in, platform_admin, in_tenant
):
    """Active, but its credential revoked: a code would be one it can never
    redeem. Refused for every preference - no chooser pretends SSO works."""
    from workeasy360.platform.ecosystem import services

    crm = sso_product("crm")
    with in_tenant(platform_admin["ctx"]):
        services.revoke_credential(
            platform_admin["ctx"],
            client_id=crm["client_id"],
            credential_id=crm["credential_id"],
            reason="no usable credential",
        )
    org = entitled_org("crm")
    for preference in ("auto_sso", None):
        signed_in(org["user"], sign_in_preference=preference)
        response = _authorize(client, crm, org["organization_id"])
        assert query_of(response)["error"] == [protocol.UNAUTHORIZED_CLIENT]
    assert _codes(crm) == 0


# --- a preference grants nothing -----------------------------------------------------------------


def test_automatic_sign_in_is_no_way_around_entitlement(
    client, signing_key, sso_product, organization_factory, signed_in
):
    crm = sso_product("crm")
    org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference="auto_sso")
    response = _authorize(client, crm, str(org.id))
    assert query_of(response)["error"] == [protocol.PRODUCT_NOT_ENTITLED]
    assert _codes(crm) == 0


def test_automatic_sign_in_is_no_way_around_a_seat(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm", seat=False)
    signed_in(org["user"], sign_in_preference="auto_sso")
    assert query_of(_authorize(client, crm, org["organization_id"]))["error"] == [
        protocol.PRODUCT_NO_SEAT
    ]


def test_automatic_sign_in_is_no_way_into_another_organization(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    mine, theirs = entitled_org("crm"), entitled_org("crm")
    signed_in(mine["user"], sign_in_preference="auto_sso")
    response = _authorize(client, crm, theirs["organization_id"])
    assert query_of(response)["error"] == [protocol.MEMBERSHIP_INACTIVE]


def test_a_refusal_is_never_hidden_behind_the_chooser(
    client, signing_key, sso_product, organization_factory, signed_in
):
    """Asked or not, a person without access learns it at once - the chooser
    is only ever shown for a request that would otherwise succeed."""
    crm = sso_product("crm")
    org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference="ask")
    response = _authorize(client, crm, str(org.id))
    assert query_of(response)["error"] == [protocol.PRODUCT_NOT_ENTITLED]


def test_staff_and_partner_standing_grant_no_product_access(
    client,
    signing_key,
    sso_product,
    entitled_org,
    staff_factory,
    partner_factory,
    signed_in,
):
    """The brief: StaffGrant and PartnerMember never make someone a product
    user. Only a membership of the organization does."""
    crm = sso_product("crm")
    org = entitled_org("crm")
    staff_user, _ctx = staff_factory(
        "platform.staff_catalog.manage", "platform.staff_integration.manage"
    )
    signed_in(staff_user, sign_in_preference="auto_sso", aal=2)
    assert query_of(_authorize(client, crm, org["organization_id"]))["error"] == [
        protocol.MEMBERSHIP_INACTIVE
    ]
    partner = partner_factory()
    signed_in(partner["user"], sign_in_preference="auto_sso")
    assert query_of(_authorize(client, crm, org["organization_id"]))["error"] == [
        protocol.MEMBERSHIP_INACTIVE
    ]


def test_trial_sso_works_until_the_trials_end_and_not_a_second_after(
    client, signing_key, sso_product, trial_org, signed_in, monkeypatch
):
    """7-day trial users get SSO while the trial entitlement is active; at its
    end (server time, before any sweeper runs) the next SSO attempt is
    refused with product_not_entitled - the product shows Upgrade."""
    from datetime import timedelta

    from django.utils import timezone

    books = sso_product("books")
    trial = trial_org("books")
    signed_in(trial["user"], sign_in_preference="auto_sso")
    assert "code" in query_of(_authorize(client, books, trial["organization_id"]))

    monkeypatch.setattr(timezone, "now", lambda: trial["ends_at"] - timedelta(seconds=1))
    signed_in(trial["user"], sign_in_preference="auto_sso")  # a session from this clock
    assert "code" in query_of(_authorize(client, books, trial["organization_id"]))

    monkeypatch.setattr(timezone, "now", lambda: trial["ends_at"])
    signed_in(trial["user"], sign_in_preference="auto_sso")
    response = _authorize(client, books, trial["organization_id"])
    assert query_of(response)["error"] == [protocol.PRODUCT_NOT_ENTITLED]
