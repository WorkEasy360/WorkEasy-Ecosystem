"""Add Partner with a login (ADR-047, ADR-048): the three types, end to end.

Add Partner creates a draft; staff complete its profile and activate it (the
helpers call the same endpoints the Complete Your Profile wizard does). Each test
then signs the administrator in through the real login endpoint with the exact
email and password Super Admin typed, and reads what the portal tells the
browser - because that is the claim: *the same credentials work, and the partner
lands in the right workspace while authorization stays capability-driven*.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.hashers import get_hasher, identify_hasher

from tests.partner_experience.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    CREATE,
    activate,
    complete_profile,
    make_body,
    send,
    sign_in,
)
from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerCapabilityGrant,
    PartnerMember,
    PartnerProductGrant,
)
from workeasy360.platform.identity.models import User

pytestmark = pytest.mark.django_db


def held(partner_id) -> set[str]:
    return set(
        PartnerCapabilityGrant.objects.filter(
            partner_id=partner_id, status="active"
        ).values_list("capability", flat=True)
    )


def ready(admin, pid: str) -> None:
    """Complete the profile and activate, as staff do after Add Partner."""
    complete_profile(admin, pid)
    response = activate(admin, pid)
    assert response.status_code == 200, response.content


def portal_of(email: str, password: str = ADMIN_PASSWORD) -> dict:
    client, response = sign_in(email, password)
    assert response.status_code == 200, response.content
    partners = client.get("/api/v1/partner/portal").json()["partners"]
    assert len(partners) == 1
    return partners[0]


# --- the three types -------------------------------------------------------------------


def test_a_reseller_is_created_and_signs_in_with_the_typed_credentials(catalog, admin):
    response = send(admin, CREATE, make_body("reseller"))
    assert response.status_code == 201, response.content
    data = response.json()

    assert data["status"] == "draft"
    assert data["experience"] == "reseller"
    assert data["default_experience"] == "reseller"
    assert data["administrator"] == {"email": ADMIN_EMAIL, "account_created": True}
    assert set(data["capabilities"]) == set(capabilities.EXPERIENCE_BUNDLES["reseller"])
    assert data["payout_currency"] == "GBP"

    ready(admin, data["id"])
    me = portal_of(ADMIN_EMAIL)
    assert me["partner_id"] == data["id"]
    assert me["role"] == "owner"
    assert me["landing"] == "reseller"
    assert [w["key"] for w in me["workspaces"]] == ["reseller"]
    assert "platform.partner_customer.provision" in me["permissions"]
    assert "platform.partner_subpartner.manage" in me["permissions"]
    # A reseller is not white-label and not an affiliate.
    assert "platform.partner_branding.manage" not in me["permissions"]
    assert "platform.partner_referral.create" not in me["permissions"]


def test_a_white_label_partner_holds_the_commercial_set_plus_brand_and_domain(
    catalog, admin
):
    response = send(admin, CREATE, make_body("white_label", name="WL Partner"))
    assert response.status_code == 201, response.content
    pid = response.json()["id"]
    ready(admin, pid)

    assert held(pid) == set(capabilities.EXPERIENCE_BUNDLES["white_label"])
    me = portal_of(ADMIN_EMAIL)
    assert me["landing"] == "white_label"
    # ADR-053: additive - the white-label bundle holds the reseller set, so the
    # Reseller workspace is offered beside the White-label one.
    assert [w["key"] for w in me["workspaces"]] == ["reseller", "white_label"]
    assert "platform.partner_branding.manage" in me["permissions"]
    assert "platform.partner_domain.manage" in me["permissions"]
    assert "platform.partner_customer.provision" in me["permissions"]
    # White-label is a label: it has no sub-partners unless granted separately.
    assert "platform.partner_subpartner.manage" not in me["permissions"]


def test_an_affiliate_gets_referral_and_money_capabilities_and_nothing_to_sell(
    catalog, admin
):
    response = send(admin, CREATE, make_body("affiliate", name="Aff Partner"))
    assert response.status_code == 201, response.content
    data = response.json()
    pid = data["id"]
    ready(admin, pid)

    assert held(pid) == set(capabilities.EXPERIENCE_BUNDLES["affiliate"])
    # Promotional scope is not selling authority: no product is granted.
    assert not PartnerProductGrant.objects.filter(partner_id=pid).exists()
    me = portal_of(ADMIN_EMAIL)
    assert me["landing"] == "affiliate"
    assert [w["key"] for w in me["workspaces"]] == ["affiliate"]
    forbidden = {
        "platform.partner_customer.view",
        "platform.partner_customer.provision",
        "platform.partner_customer.manage",
        "platform.partner_subscription.sell",
        "platform.partner_subscription.manage",
        "platform.partner_pricing.view",
        "platform.partner_branding.manage",
        "platform.partner_domain.manage",
        "platform.partner_subpartner.manage",
    }
    assert not forbidden & set(me["permissions"])
    assert "platform.partner_referral.create" in me["permissions"]
    assert "platform.partner_commission.view" in me["permissions"]
    assert "platform.partner_payout.view" in me["permissions"]


# --- the account ------------------------------------------------------------------------


def test_the_password_is_stored_only_as_a_hash_and_the_account_is_unverified(
    catalog, admin
):
    assert send(admin, CREATE, make_body("reseller")).status_code == 201
    user = User.objects.get(email=ADMIN_EMAIL)
    assert user.password != ADMIN_PASSWORD
    assert ADMIN_PASSWORD not in user.password
    # Made by the configured hasher (Django's approved mechanism), not by hand.
    assert identify_hasher(user.password).algorithm == get_hasher("default").algorithm
    assert user.password.count("$") >= 2
    assert user.check_password(ADMIN_PASSWORD)
    assert user.status == "active"
    # Nobody has proved the mailbox, so the platform does not claim it is verified.
    assert user.email_verified_at is None
    assert user.acquisition_source == "operator"
    # Typed by staff, but not a forced-change temporary password.
    assert user.password_change_required is False


def test_the_response_and_headers_never_carry_the_password(catalog, admin):
    response = send(admin, CREATE, make_body("reseller"))
    assert response.status_code == 201
    text = response.content.decode()
    assert ADMIN_PASSWORD not in text
    assert "password" not in response.json()
    assert response["Cache-Control"] == "no-store"


def test_the_partner_is_one_ordinary_account_with_an_owner_member(catalog, admin):
    pid = send(admin, CREATE, make_body("reseller")).json()["id"]
    partner = PartnerAccount.objects.get(id=pid)
    assert partner.default_experience == "reseller"
    assert partner.status == "draft"
    member = PartnerMember.objects.get(partner_id=pid)
    assert member.role == "owner" and member.status == "active"
    assert member.invitation_deferred is False and member.invitation_sent_at is None
    # One partner, one account, one member: nothing was created per type.
    assert PartnerAccount.objects.filter(code="acme-partner").count() == 1


def test_a_second_create_with_the_same_email_creates_nothing_more(catalog, admin):
    assert send(admin, CREATE, make_body("reseller")).status_code == 201
    again = send(admin, CREATE, make_body("affiliate", name="Another Partner"))
    assert again.status_code == 400
    assert any(p["field"] == "administrator.email" for p in again.json()["problems"])
    assert PartnerAccount.objects.count() == 1
    assert User.objects.filter(email=ADMIN_EMAIL).count() == 1
