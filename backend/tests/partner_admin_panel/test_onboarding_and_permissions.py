"""Who may do what before and after KYC (ADR-063, the specification's 2.2 and 12):
a draft holds the onboarding tier only; a partner made active before KYC existed
trades unless the Admin's policy restricts it; White Label grants branding and a
domain and nothing to sell or refer unless a base permission is granted beside it."""

from __future__ import annotations

import uuid

import pytest

from tests.phase7.support import CAPS
from workeasy360.commercial.partners import capabilities, kyc, resolver
from workeasy360.contracts.errors import PartnerNotActive

pytestmark = pytest.mark.django_db

ONBOARDING = {"platform.partner_dashboard.view", "platform.partner_profile.view"}


def _set_policy(**values):
    from django.core.cache import cache

    from workeasy360.platform.administration.models import PlatformSettings
    from workeasy360.platform.administration.site import POLICY_CACHE_KEY

    row, _ = PlatformSettings.objects.get_or_create(id=1)
    for key, value in values.items():
        setattr(row, key, value)
    row.save()
    cache.delete(POLICY_CACHE_KEY)


def _draft(staff_factory, user_factory, *caps):
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.access import partner_actor

    _u, sctx = staff_factory("platform.staff_partner.manage")
    partner = services.create_partner(
        sctx,
        code=f"d-{uuid.uuid4().hex[:8]}",
        display_name="Draft Partner",
        payout_currency="GBP",
        contact_email="draft@partner.example.com",
    )
    member = user_factory()
    services.add_member(sctx, partner.id, email=member.email, role="owner")
    for cap in caps:
        services.grant_capability(
            sctx, partner.id, capability=cap, reason="Test fixture grant"
        )
    return partner, partner_actor(user_id=member.id, partner_id=partner.id)


def test_a_draft_holds_the_onboarding_tier_and_nothing_commercial(
    staff_factory, user_factory
):
    _partner, ctx = _draft(staff_factory, user_factory, *CAPS)
    held = resolver.partner_permissions(ctx)
    assert held == resolver.permission_registry.expand(
        capabilities.ONBOARDING_PERMISSIONS
    )
    for denied in (
        "platform.partner_referral.create",
        "platform.partner_customer.provision",
        "platform.partner_subscription.sell",
        "platform.partner_wallet.manage",
        "platform.partner_payout.request",
    ):
        assert denied not in held


def test_a_draft_can_reach_support_but_not_trade(staff_factory, user_factory):
    from workeasy360.commercial.partners import access, tickets

    _partner, ctx = _draft(staff_factory, user_factory, *CAPS)
    opened = tickets.open_platform_ticket(
        ctx,
        subject="Help with my KYC",
        message="Which documents do you need from a sole trader?",
        category="general",
        priority="normal",
    )
    assert opened["subject"] == "Help with my KYC"
    with pytest.raises(PartnerNotActive):
        access.require_transacting(ctx)


def test_the_portal_reports_kyc_and_account_states_separately(
    staff_factory, user_factory
):
    from workeasy360.commercial.partners import selectors

    partner, ctx = _draft(staff_factory, user_factory, *CAPS)
    entry = selectors.partners_for_user(ctx.user_id)[0]
    assert entry["onboarding"] == {
        "kyc_status": "draft",
        "account_status": "draft",
        "cleared": False,
        "reason": "kyc_pending",
        "legacy": False,
        "kyc_required": False,
    }
    assert entry["category"] == "reseller"


def test_a_legacy_partner_trades_unless_the_admin_restricts_it(partner_factory, settings):
    from workeasy360.commercial.partners import access

    fx = partner_factory(*CAPS)  # active, no KYC application: legacy
    settings.PARTNER_KYC_REQUIRED = True
    assert kyc.standing("active", kyc.status_of(fx["partner"].id))["legacy"] is True
    assert "platform.partner_subscription.sell" in resolver.partner_permissions(
        fx["actor"]()
    )
    access.require_transacting(fx["actor"]())

    _set_policy(partner_legacy_kyc_policy="restrict")
    held = resolver.partner_permissions(fx["actor"]())
    assert "platform.partner_subscription.sell" not in held
    assert ONBOARDING <= held
    with pytest.raises(PartnerNotActive):
        access.require_transacting(fx["actor"]())
    state = kyc.onboarding_state(fx["partner"].id, status="active")
    assert state["cleared"] is False
    assert state["reason"] == "legacy_kyc_required"

    _set_policy(partner_legacy_kyc_policy="allow")
    assert "platform.partner_subscription.sell" in resolver.partner_permissions(
        fx["actor"]()
    )


def test_a_restricted_legacy_partner_earns_no_new_attribution(partner_factory, settings):
    from workeasy360.commercial.commissions import referrals

    fx = partner_factory(capabilities.REFERRAL_CREATE, capabilities.REFERRAL_VIEW)
    settings.PARTNER_KYC_REQUIRED = True
    code = referrals.create_referral_code(fx["actor"](), label="Launch")
    assert referrals.capture_visit(code.code) is not None
    _set_policy(partner_legacy_kyc_policy="restrict")
    assert referrals.capture_visit(code.code) is None


def test_the_store_link_lands_on_the_store(partner_factory):
    from workeasy360.commercial.commissions import referrals

    fx = partner_factory(capabilities.REFERRAL_CREATE, capabilities.REFERRAL_VIEW)
    code = referrals.create_referral_code(fx["actor"](), label="Store link")
    _token, landing = referrals.capture_visit(code.code, to="store")
    assert landing == "/store"
    _token, landing = referrals.capture_visit(code.code, to="https://evil.example.com")
    assert landing == "/signup"


def test_a_suspended_partner_stops_earning_when_the_admin_says_so(partner_factory):
    from workeasy360.commercial.commissions import accrual
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerStatus

    fx = partner_factory(*CAPS)
    services.set_partner_status(
        fx["staff_ctx"],
        fx["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Under review by staff",
    )
    from django.utils import timezone

    now = timezone.now()
    assert accrual._partner_problem(fx["partner"].id, now, "GBP") is None
    _set_policy(partner_suspended_commission_policy="stop")
    assert accrual._partner_problem(fx["partner"].id, now, "GBP") == "partner_suspended"


def test_white_label_alone_sells_refers_and_earns_nothing():
    wl = set(capabilities.EXPERIENCE_BUNDLES[capabilities.WHITE_LABEL])
    assert wl == {capabilities.BRANDING_MANAGE, capabilities.DOMAIN_MANAGE}
    assert capabilities.workspaces(wl) == [capabilities.WHITE_LABEL]
    for key in (
        capabilities.SUBSCRIPTION_SELL,
        capabilities.REFERRAL_CREATE,
        capabilities.COMMISSION_EARN,
    ):
        assert key not in wl


def test_base_permissions_beside_white_label_are_explicit(staff_factory, user_factory):
    from workeasy360.commercial.partners import experience, selectors

    partner, _ctx = _draft(staff_factory, user_factory)
    _u, sctx = staff_factory("platform.staff_partner.manage")
    experience.grant_experience(
        sctx, partner.id, experience="white_label", reason="Approved by the review team"
    )
    held = selectors.active_capabilities(partner.id)
    assert capabilities.SUBSCRIPTION_SELL not in held
    experience.grant_experience(
        sctx,
        partner.id,
        experience="white_label",
        also=["reseller"],
        reason="Approved to sell as well",
    )
    held = selectors.active_capabilities(partner.id)
    assert capabilities.SUBSCRIPTION_SELL in held
    assert capabilities.REFERRAL_CREATE not in held
    assert capabilities.workspaces(held) == [
        capabilities.RESELLER,
        capabilities.WHITE_LABEL,
    ]


def test_also_takes_partner_types_only(staff_factory, user_factory):
    from workeasy360.commercial.partners import experience
    from workeasy360.contracts.errors import PartnerValidationError

    partner, _ctx = _draft(staff_factory, user_factory)
    _u, sctx = staff_factory("platform.staff_partner.manage")
    for bad in ("reseller", ["superuser"], [1]):
        with pytest.raises(PartnerValidationError):
            experience.grant_experience(
                sctx,
                partner.id,
                experience="white_label",
                also=bad,
                reason="Approved by the review team",
            )


def test_a_role_never_exceeds_the_partners_grants(partner_factory, user_factory):
    """Partner staff roles (the specification's 11.2): a Sales member of an
    affiliate cannot sell, whatever its role allows elsewhere."""
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.access import partner_actor

    fx = partner_factory(capabilities.REFERRAL_CREATE, capabilities.REFERRAL_VIEW)
    seller = user_factory()
    services.add_member(
        fx["staff_ctx"], fx["partner"].id, email=seller.email, role="sales"
    )
    held = resolver.partner_permissions(
        partner_actor(user_id=seller.id, partner_id=fx["partner"].id)
    )
    assert "platform.partner_referral.create" in held
    assert "platform.partner_subscription.sell" not in held
    assert "platform.partner_wallet.view" not in held
