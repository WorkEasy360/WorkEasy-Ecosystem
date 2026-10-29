"""Additive workspaces, set-up fees and the Support request (ADR-053)."""

from __future__ import annotations

import pytest

from workeasy360.commercial.partners import capabilities, upgrades
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db


# --- workspaces: additive, presentation only ----------------------------------------------


def test_a_white_label_partner_also_opens_the_reseller_workspace():
    held = set(capabilities.EXPERIENCE_BUNDLES[capabilities.WHITE_LABEL])
    assert capabilities.workspaces(held) == ["reseller", "white_label"]


def test_an_upgraded_affiliate_switches_between_all_three():
    held = {
        *capabilities.EXPERIENCE_BUNDLES[capabilities.AFFILIATE],
        *capabilities.EXPERIENCE_BUNDLES[capabilities.WHITE_LABEL],
    }
    assert capabilities.workspaces(held) == ["reseller", "white_label", "affiliate"]
    assert capabilities.resolve_landing("white_label", held) == "white_label"


def test_an_affiliate_alone_opens_only_the_affiliate_workspace():
    held = set(capabilities.EXPERIENCE_BUNDLES[capabilities.AFFILIATE])
    assert capabilities.workspaces(held) == ["affiliate"]
    # The stored landing preference cannot open a workspace the grants do not earn.
    assert capabilities.resolve_landing("white_label", held) == "affiliate"


def test_the_landing_preference_grants_no_permission(partner_factory):
    from workeasy360.commercial.partners.models import PartnerAccount
    from workeasy360.platform.authz import services as authz

    fx = partner_factory("referral.create", "referral.view")
    before = authz.effective_permissions(fx["actor"]())
    PartnerAccount.objects.filter(id=fx["partner"].id).update(
        default_experience="white_label"
    )
    assert authz.effective_permissions(fx["actor"]()) == before


# --- set-up fees --------------------------------------------------------------------------


@pytest.fixture
def fee_staff(staff_factory):
    _u, ctx = staff_factory("platform.staff_partner.manage")
    return ctx


def test_a_fee_is_set_by_staff_and_zero_removes_it(fee_staff):
    assert upgrades.program_fee("reseller") is None
    upgrades.set_program_fee(
        fee_staff, experience="reseller", amount_minor=2_000_000, currency="INR"
    )
    assert upgrades.program_fee("reseller") == {
        "amount_minor": 2_000_000,
        "currency": "INR",
    }
    upgrades.set_program_fee(
        fee_staff, experience="reseller", amount_minor=0, currency="INR"
    )
    assert upgrades.program_fee("reseller") is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"experience": "affiliate", "amount_minor": 1, "currency": "INR"},
        {"experience": "reseller", "amount_minor": -1, "currency": "INR"},
        {"experience": "reseller", "amount_minor": 10.5, "currency": "INR"},
        {"experience": "reseller", "amount_minor": 1, "currency": "XXX"},
    ],
)
def test_a_fee_is_validated(fee_staff, kwargs):
    with pytest.raises(PartnerValidationError):
        upgrades.set_program_fee(fee_staff, **kwargs)


def test_a_partner_cannot_set_a_fee(partner_factory):
    from workeasy360.contracts.errors import PermissionDenied

    fx = partner_factory("referral.create", "referral.view")
    with pytest.raises(PermissionDenied):
        upgrades.set_program_fee(
            fx["actor"](), experience="reseller", amount_minor=0, currency="INR"
        )


def test_a_request_keeps_the_fee_it_started_with(fee_staff, partner_factory):
    upgrades.set_program_fee(
        fee_staff, experience="reseller", amount_minor=2_000_000, currency="INR"
    )
    fx = partner_factory(
        "referral.create",
        "referral.view",
        "commission.earn",
        "commission.view",
        "payout.view",
    )
    opened = upgrades.start(fx["actor"](), to="reseller")
    upgrades.set_program_fee(
        fee_staff, experience="reseller", amount_minor=9_000_000, currency="INR"
    )
    assert opened["setup_fee"] == {
        "amount_minor": 2_000_000,
        "currency": "INR",
        "settled": False,
    }
    state = upgrades.state(fx["actor"]())
    assert state["open"]["setup_fee"]["amount_minor"] == 2_000_000
    assert state["fees"]["reseller"]["amount_minor"] == 9_000_000


def test_approval_needs_the_fee_payment_reference():
    class Row:
        fee_amount_minor = 2_000_000

    with pytest.raises(PartnerValidationError) as refused:
        upgrades.require_fee_settled(Row(), "")
    assert refused.value.context["field"] == "commercial_reference"
    assert upgrades.require_fee_settled(Row(), "INV-2026-0042") is True

    class Free:
        fee_amount_minor = None

    assert upgrades.require_fee_settled(Free(), "") is False


# --- support ----------------------------------------------------------------------------------


def test_a_support_request_is_sent_by_the_platform(partner_factory, settings):
    from workeasy360.commercial.controlplane import partner_help
    from workeasy360.platform.notifications.models import Notification

    settings.W360_PARTNER_SUPPORT_EMAIL = "partners@workeasy360.example.com"
    fx = partner_factory("referral.create", "referral.view")
    assert partner_help.support(fx["actor"]())["can_send"] is True
    sent = partner_help.send_support_request(
        fx["actor"](),
        subject="Renewal question",
        message="When does our customer renew?\nThanks.",
        priority="high",
        workspace="affiliate",
    )
    assert sent["sent"] is True
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the queued support email", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        assert Notification.unscoped.filter(
            template_key="platform.partner.support_request"
        ).exists()


def test_a_support_request_is_refused_without_an_address_or_words(
    partner_factory, settings
):
    from workeasy360.commercial.controlplane import partner_help

    fx = partner_factory("referral.create", "referral.view")
    settings.W360_PARTNER_SUPPORT_EMAIL = ""
    with pytest.raises(PartnerValidationError):
        partner_help.send_support_request(fx["actor"](), subject="Hi", message="Help")
    settings.W360_PARTNER_SUPPORT_EMAIL = "partners@workeasy360.example.com"
    with pytest.raises(PartnerValidationError):
        partner_help.send_support_request(fx["actor"](), subject="", message="Help")
    with pytest.raises(PartnerValidationError):
        partner_help.send_support_request(
            fx["actor"](), subject="Hi", message="Help", priority="critical"
        )
