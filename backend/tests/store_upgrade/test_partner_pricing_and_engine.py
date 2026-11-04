"""ADR-056: Super Admin's price ranges bound partner pricing, a partner sets its
own customer price only inside them, and the central engine
(``channel_pricing``) gives every Store level its figures from the same
functions checkout and the commission ledger use."""

from __future__ import annotations

import uuid

import pytest

from tests.store_upgrade.conftest import audit_entry
from workeasy360.commercial.commissions import channel_pricing, margins
from workeasy360.commercial.partners import price_ranges
from workeasy360.commercial.pricing.resolution import resolve_price
from workeasy360.contracts.enums import BillingInterval
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
)
from workeasy360.contracts.values.money import CurrencyCode

pytestmark = pytest.mark.django_db


def _plan(channel, code="crm_professional"):
    return channel["catalog"]["plans"][code], channel["catalog"]["versions"][code]


def _range(ctx, channel, *, low=4000, high=6000, allowed=True, experience="reseller"):
    plan, _version = _plan(channel)
    return price_ranges.set_range(
        ctx,
        experience=experience,
        plan_id=str(plan.id),
        interval="monthly",
        currency="GBP",
        min_minor=low,
        max_minor=high,
        partner_may_set=allowed,
    )


def _own(channel, amount, code="crm_professional"):
    _plan_row, version = _plan(channel, code)
    return price_ranges.set_own_price(
        channel["actor"](),
        plan_version_id=str(version.id),
        interval="monthly",
        amount_minor=amount,
    )


def _resolved_for_partner(channel):
    _plan_row, version = _plan(channel)
    from django.utils import timezone

    return resolve_price(
        plan_version_id=version.id,
        organization_id=None,
        currency=CurrencyCode.GBP,
        interval=BillingInterval.MONTHLY,
        at=timezone.now(),
        partner_id=channel["partner"].id,
    ).total.minor_units


# --- ranges ------------------------------------------------------------------------------


def test_a_partner_sets_its_price_inside_the_range_and_checkout_charges_it(
    channel, range_ctx
):
    _range(range_ctx, channel)
    result = _own(channel, 5499)
    assert result["amount_minor"] == 5499
    assert _resolved_for_partner(channel) == 5499  # what its customers are charged
    entry = audit_entry("partner.own_price_set", resource_id=channel["partner"].id)
    assert entry is not None and entry.changes["range"] == {
        "min_minor": 4000,
        "max_minor": 6000,
    }


@pytest.mark.parametrize("amount", [100, 3999, 6001, 150000])
def test_a_price_outside_the_range_is_refused(channel, range_ctx, amount):
    _range(range_ctx, channel)
    with pytest.raises(PartnerValidationError) as refused:
        _own(channel, amount)
    assert refused.value.context["reason"] == "outside_allowed_range"
    assert _resolved_for_partner(channel) == 4999  # still the list price


def test_without_a_range_or_with_partner_pricing_off_the_price_is_super_admins(
    channel, range_ctx
):
    with pytest.raises(PartnerValidationError) as refused:
        _own(channel, 4999)
    assert refused.value.context["reason"] == "partner_pricing_not_allowed"
    _range(range_ctx, channel, allowed=False)
    with pytest.raises(PartnerValidationError):
        _own(channel, 4999)


def test_the_range_is_the_one_of_the_programme_the_partner_sells_under(
    channel, range_ctx
):
    # A white-label range does not open pricing to a reseller.
    _range(range_ctx, channel, experience="white_label", low=1, high=99999)
    with pytest.raises(PartnerValidationError):
        _own(channel, 5000)


def test_a_partner_cannot_price_a_plan_it_may_not_sell(channel, range_ctx):
    plan = channel["catalog"]["plans"]["books_professional"]
    price_ranges.set_range(
        range_ctx,
        experience="reseller",
        plan_id=str(plan.id),
        interval="monthly",
        currency="GBP",
        min_minor=1,
        max_minor=None,
        partner_may_set=True,
    )
    with pytest.raises(PartnerRecordNotFound):
        _own(channel, 2500, code="books_professional")  # granted only CRM


def test_ranges_are_staff_only_validated_and_audited(channel, range_ctx):
    with pytest.raises(PermissionDenied):
        _range(channel["actor"](), channel)
    with pytest.raises(PartnerValidationError):
        _range(range_ctx, channel, low=5000, high=4000)
    with pytest.raises(PartnerValidationError):
        _range(range_ctx, channel, experience="affiliate")
    first = _range(range_ctx, channel)
    _range(range_ctx, channel, low=4500)
    entry = audit_entry(
        "commercial.selling_price_range.set", resource_id=uuid.UUID(first["id"])
    )
    assert entry.changes["previous"]["min_minor"] == 4000
    assert entry.changes["new"]["min_minor"] == 4500


def test_an_affiliate_only_partner_cannot_set_prices(partner_factory, catalog, range_ctx):
    affiliate = partner_factory("referral.create", "commission.earn")
    plan = catalog["plans"]["crm_professional"]
    price_ranges.set_range(
        range_ctx,
        experience="reseller",
        plan_id=str(plan.id),
        interval="monthly",
        currency="GBP",
        min_minor=1,
        max_minor=None,
        partner_may_set=True,
    )
    with pytest.raises((PartnerValidationError, PermissionDenied)):
        price_ranges.set_own_price(
            affiliate["actor"](),
            plan_version_id=str(catalog["versions"]["crm_professional"].id),
            interval="monthly",
            amount_minor=5000,
        )


# --- the central engine --------------------------------------------------------------------


def test_the_engine_gives_each_channel_its_share_of_one_price(channel, staff_factory):
    _u, margin_ctx = staff_factory("platform.staff_commission.manage")
    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Reseller terms"
    )
    _plan_row, version = _plan(channel)
    quote = lambda ch, **kw: channel_pricing.resolve_channel_price(  # noqa: E731
        plan_version_id=version.id, interval="monthly", currency="GBP", channel=ch, **kw
    )
    direct = quote("direct")
    assert (
        direct.customer_minor,
        direct.partner_share_minor,
        direct.platform_share_minor,
    ) == (4999, 0, 4999)
    affiliate = quote("affiliate")
    assert (
        affiliate.earning == "commission" and affiliate.commission_minor == 500
    )  # 10% default
    assert affiliate.platform_share_minor == 4499
    reseller = quote("reseller", partner_id=channel["partner"].id)
    assert reseller.earning == "margin"
    assert (reseller.partner_cost_minor, reseller.margin_minor) == (3999, 1000)
    assert reseller.platform_share_minor == 3999
    # No margin rule for white-label: its partners earn the commission instead.
    white = quote("white_label")
    assert white.earning == "commission" and white.commission_minor == 500
    assert direct.tax_minor is None and direct.tax_behaviour == "exclusive"


def test_the_engine_reflects_a_partners_own_price_and_the_range(
    channel, range_ctx, staff_factory
):
    _u, margin_ctx = staff_factory("platform.staff_commission.manage")
    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Reseller terms"
    )
    _range(range_ctx, channel)
    _own(channel, 5999)
    _plan_row, version = _plan(channel)
    exact = channel_pricing.resolve_channel_price(
        plan_version_id=version.id,
        interval="monthly",
        currency="GBP",
        channel="reseller",
        partner_id=channel["partner"].id,
    )
    assert (exact.customer_minor, exact.margin_minor, exact.price_source) == (
        5999,
        2000,
        "partner_book",
    )
    assert exact.price_range["min_minor"] == 4000
    plans = [
        {
            "plan_version_id": version.id,
            "plan_id": _plan_row.id,
            "product_code": "crm",
            "plan_code": "crm_professional",
        }
    ]
    bulk = channel_pricing.channel_prices(
        plans, currency="GBP", channel="reseller", partner_id=channel["partner"].id
    )[(str(version.id), "monthly")]
    # The screen's bulk read and the exact quote agree.
    assert bulk.as_dict()["customer_minor"] == exact.customer_minor
    assert (
        bulk.margin_minor == exact.margin_minor and bulk.price_range == exact.price_range
    )


def test_the_partner_pricing_screen_reads_the_engine(channel, range_ctx):
    from workeasy360.commercial.controlplane import partner_portal

    _range(range_ctx, channel)
    data = partner_portal.plans(channel["actor"]())
    pro = next(p for p in data["plans"] if p["plan_code"] == "crm_professional")
    (line,) = pro["prices"]
    assert line["customer_price_minor"] == 4999 and line["list_price_minor"] == 4999
    assert line["can_set_price"] is True
    assert line["price_range"]["max_minor"] == 6000
    assert line["expected_earning_minor"] == 500  # the 10% commission
