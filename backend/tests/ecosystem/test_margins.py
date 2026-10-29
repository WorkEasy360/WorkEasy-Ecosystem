"""Partner margins (ADR-053): a discount off list price, versioned, earned on
the one ledger *instead of* a commission - never both - and only on a paid or
approved sale. The partner's cost and margin are the server's figures."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase7.support import provision, sell
from workeasy360.commercial.commissions import domain, margins
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.money import CurrencyCode

pytestmark = pytest.mark.django_db


# --- the pure rules ----------------------------------------------------------------------


def test_partner_cost_is_list_less_the_discount_half_up():
    assert (
        domain.partner_cost(list_minor=4999, currency=CurrencyCode.GBP, discount_bp=2000)
        == 3999
    )
    assert (
        domain.partner_cost(list_minor=1000, currency=CurrencyCode.GBP, discount_bp=0)
        == 1000
    )
    assert domain.margin_bp(customer_minor=4999, cost_minor=3999) == 2000
    assert domain.margin_bp(customer_minor=0, cost_minor=0) == 0


def _view(**kw):
    base = {
        "id": uuid.uuid4(),
        "code": "x",
        "version": 1,
        "experience": "reseller",
        "partner_id": None,
        "product_code": "",
        "discount_bp": 1000,
        "effective_from": timezone.now() - timedelta(days=1),
        "retired_at": None,
    }
    base.update(kw)
    return domain.MarginView(**base)


def test_a_partner_override_and_a_product_rule_outrank_the_programme_default():
    partner = uuid.uuid4()
    default = _view(discount_bp=1000)
    product = _view(product_code="crm", discount_bp=1500)
    override = _view(experience="", partner_id=partner, discount_bp=2500)
    other = _view(experience="white_label", discount_bp=3000)
    rules = [default, product, override, other]
    now = timezone.now()
    pick = lambda **kw: domain.select_margin(rules, at=now, **kw)  # noqa: E731
    assert (
        pick(partner_id=partner, experience="reseller", product_code="books") is override
    )
    assert (
        pick(partner_id=uuid.uuid4(), experience="reseller", product_code="crm")
        is product
    )
    assert (
        pick(partner_id=uuid.uuid4(), experience="reseller", product_code="books")
        is default
    )
    assert (
        pick(partner_id=uuid.uuid4(), experience="white_label", product_code="books")
        is other
    )


# --- the staff command ---------------------------------------------------------------------


def test_setting_a_margin_versions_it_and_retires_the_previous(margin_ctx):
    from workeasy360.commercial.commissions.models import MarginRule

    first = margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Launch terms"
    )
    again = margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Launch terms"
    )
    assert again.id == first.id  # the discount already in force: nothing new
    second = margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2500, reason="Better terms"
    )
    assert second.version == 2
    first.refresh_from_db()
    assert first.retired_at is not None
    assert (
        MarginRule.objects.filter(code=first.code, retired_at__isnull=True).count() == 1
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"experience": "reseller", "discount_bp": 10001},
        {"experience": "reseller", "discount_bp": 12.5},
        {"experience": "affiliate", "discount_bp": 1000},
        {"experience": "", "discount_bp": 1000},
    ],
)
def test_a_margin_must_be_whole_basis_points_for_a_selling_programme(margin_ctx, kwargs):
    with pytest.raises(PartnerValidationError):
        margins.set_margin(margin_ctx, reason="Partner terms", **kwargs)


def test_a_margin_cannot_be_backdated(margin_ctx):
    with pytest.raises(PartnerValidationError) as refused:
        margins.set_margin(
            margin_ctx,
            experience="reseller",
            discount_bp=1000,
            reason="Partner terms",
            effective_from=timezone.now() - timedelta(days=2),
        )
    assert refused.value.context["field"] == "effective_from"


def test_a_partner_may_not_set_its_own_margin(channel):
    from workeasy360.contracts.errors import PermissionDenied

    with pytest.raises(PermissionDenied):
        margins.set_margin(
            channel["actor"](),
            experience="reseller",
            discount_bp=9000,
            reason="My own margin",
        )


# --- the ledger --------------------------------------------------------------------------


def _accruals(org_id):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    return list(CommissionAccrual.objects.filter(organization_id=org_id))


def test_a_reseller_sale_under_a_margin_earns_the_margin_not_a_commission(
    channel, margin_ctx
):
    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Reseller terms"
    )
    managed, _owner = provision(channel)
    sell(channel, managed.organization_id)  # crm_professional, list 4999 GBP
    rows = _accruals(managed.organization_id)
    assert len(rows) == 1  # never both a margin and a commission
    row = rows[0]
    assert row.earning == "margin"
    assert row.cost_minor == 3999
    assert row.amount_minor == 1000  # 4999 - 3999
    assert row.product_code == "crm" and row.plan_code == "crm_professional"
    assert row.rule_id is None and row.margin_rule_id is not None


def test_without_a_margin_rule_the_partner_earns_its_commission(channel):
    managed, _owner = provision(channel)
    sell(channel, managed.organization_id)
    (row,) = _accruals(managed.organization_id)
    assert row.earning == "commission"
    assert row.amount_minor == 500  # 10% of 4999, half up
    assert row.cost_minor is None


def test_a_partner_price_above_list_widens_the_margin(channel, margin_ctx, staff_factory):
    from workeasy360.commercial.partners import services

    _u, pricing_ctx = staff_factory("platform.staff_pricing.manage")
    services.set_partner_price(
        pricing_ctx,
        channel["partner"].id,
        plan_version_id=channel["catalog"]["versions"]["crm_professional"].id,
        interval="monthly",
        currency="GBP",
        amount_minor=5999,
    )
    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Reseller terms"
    )
    managed, _owner = provision(channel)
    sell(channel, managed.organization_id)
    (row,) = _accruals(managed.organization_id)
    assert row.earning == "margin"
    assert (row.basis_minor, row.cost_minor, row.amount_minor) == (5999, 3999, 2000)


def test_a_new_margin_never_rewrites_an_earlier_sale(channel, margin_ctx):
    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Reseller terms"
    )
    managed, _owner = provision(channel)
    sell(channel, managed.organization_id)
    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=5000, reason="New partner terms"
    )
    from workeasy360.commercial.commissions import accrual

    accrual.accrue_for_organization(managed.organization_id)
    (row,) = _accruals(managed.organization_id)
    assert row.amount_minor == 1000  # computed under version 1, kept
    assert accrual.reconcile(organization_ids=[managed.organization_id]).is_clean


def test_the_partner_sees_its_cost_and_margin_per_plan(channel, margin_ctx):
    from workeasy360.commercial.controlplane import partner_portal

    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Reseller terms"
    )
    plans = partner_portal.plans(channel["actor"]())
    row = next(p for p in plans["plans"] if p["plan_code"] == "crm_professional")
    assert row["earning"] == {"model": "margin", "discount_bp": 2000}
    price = row["prices"][0]
    assert price["list_price_minor"] == 4999
    assert price["customer_price_minor"] == 4999
    assert price["partner_cost_minor"] == 3999
    assert price["margin_minor"] == 1000
    assert price["margin_bp"] == 2000
    assert "partner_cost" not in plans["not_implemented"]
