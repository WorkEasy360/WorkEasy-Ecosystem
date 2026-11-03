"""ADR-056 checkout and earnings: the server re-prices every order (discount and
set-up fee included, the browser's numbers ignored), a set-up fee is a separate,
non-commissionable invoice line paid once, and commission rules can be
plan-specific, fixed-amount and limited in duration - all decided server-side,
only after a verified payment."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.utils import timezone

from tests.extension.conftest import pay_order, place_order, url
from tests.phase7.support import provision, sell
from workeasy360.commercial.commissions import domain, rules
from workeasy360.commercial.pricing import services as pricing
from workeasy360.contracts.enums import BillingInterval
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.money import CurrencyCode

pytestmark = pytest.mark.django_db


def _v(shop, plan):
    return str(shop["catalog"]["versions"][plan].id)


def _reprice(pricing_scope, catalog, plan="books_professional", **terms):
    with pricing_scope() as ctx:
        pricing.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"][plan].id,
            interval=BillingInterval.MONTHLY,
            **terms,
        )


def _quote(shop, body):
    return shop["client"].post(
        url(shop["slug"], "orders/quote"), body, content_type="application/json"
    )


# --- checkout ------------------------------------------------------------------------------


def test_checkout_charges_the_discounted_price_plus_a_first_set_up_fee(
    shop, pricing_scope
):
    _reprice(
        pricing_scope,
        shop["catalog"],
        amount_minor=2999,
        discount_bp=1000,
        setup_fee_minor=5000,
    )
    q = _quote(shop, {"plan_version_id": _v(shop, "books_professional")}).json()
    assert q["recurring_amount_minor"] == 2699  # 2999 less 10%, half up
    assert q["setup_fee_minor"] == 5000
    assert q["amount_due_minor"] == 2699 + 5000


def test_the_browser_cannot_lower_the_price(shop, pricing_scope):
    _reprice(pricing_scope, shop["catalog"], amount_minor=2999, setup_fee_minor=5000)
    q = _quote(
        shop,
        {
            "plan_version_id": _v(shop, "books_professional"),
            "amount_due_minor": 1,
            "setup_fee_minor": 0,
            "discount_bp": 10000,
        },
    )
    # Unknown fields are either refused or ignored - never obeyed.
    if q.status_code == 200:
        assert q.json()["amount_due_minor"] == 2999 + 5000
    else:
        assert q.status_code == 400


def test_a_set_up_fee_is_its_own_unlinked_invoice_line_and_earns_nothing(
    shop, pricing_scope
):
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import InvoiceLine

    _reprice(pricing_scope, shop["catalog"], amount_minor=2999, setup_fee_minor=5000)
    placed = place_order(
        shop["client"], shop["slug"], plan_version_id=_v(shop, "books_professional")
    )
    assert placed.status_code == 201, placed.content
    order = placed.json()
    assert order["amount_due_minor"] == 7999
    paid = pay_order(shop["client"], shop["slug"], shop["id"], order)
    assert paid["status"] == "fulfilled"
    with organization_scope(shop["id"], reason="Test reads the invoice lines"):
        lines = list(
            InvoiceLine.unscoped.filter(
                organization_id=shop["id"], invoice__order_id=order["order_id"]
            ).values(
                "description", "product_code", "amount_minor", "subscription_item_id"
            )
        )
    product = [r for r in lines if r["product_code"] == "books"]
    fee = [r for r in lines if r["product_code"] == ""]
    assert [r["amount_minor"] for r in product] == [2999]
    assert product[0]["subscription_item_id"] is not None
    assert [r["amount_minor"] for r in fee] == [5000]
    # The fee pays for no subscription line, so the commission ledger (which
    # earns only on lines naming one) never pays a share of it.
    assert fee[0]["subscription_item_id"] is None
    assert fee[0]["description"].startswith("Set-up fee")


def test_a_cart_charges_each_plans_set_up_fee_once(shop, pricing_scope):
    _reprice(pricing_scope, shop["catalog"], amount_minor=2999, setup_fee_minor=5000)
    body = {
        "cart_items": [
            {"plan_version_id": _v(shop, "crm_professional")},
            {"plan_version_id": _v(shop, "books_professional")},
        ]
    }
    q = _quote(shop, body).json()
    assert q["amount_due_minor"] == 4999 + 2999 + 5000
    assert q["setup_fee_minor"] == 5000
    assert {(line["product_code"], line["setup_fee_minor"]) for line in q["lines"]} == {
        ("crm", 0),
        ("books", 5000),
    }


def test_an_existing_subscription_keeps_its_price_after_a_rise(shop, pricing_scope):
    order = place_order(
        shop["client"], shop["slug"], plan_version_id=_v(shop, "books_professional")
    ).json()
    pay_order(shop["client"], shop["slug"], shop["id"], order)
    _reprice(pricing_scope, shop["catalog"], amount_minor=3999)
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    with organization_scope(shop["id"], reason="Test reads the frozen price snapshot"):
        (item,) = SubscriptionItem.unscoped.filter(organization_id=shop["id"])
    assert item.total_amount_minor == 2999  # the historical fact stands
    # A new customer pays the new price.
    q = _quote(shop, {"plan_version_id": _v(shop, "crm_starter")}).json()
    assert q["recurring_amount_minor"] == 1499


# --- commission rules ---------------------------------------------------------------------


def _rule(**kw):
    base = {
        "id": uuid.uuid4(),
        "code": "x",
        "version": 1,
        "partner_id": None,
        "product_code": "",
        "rate_bp": 1000,
        "effective_from": timezone.now() - timedelta(days=1),
        "retired_at": None,
    }
    base.update(kw)
    return domain.RuleView(**base)


def test_a_plan_rule_outranks_a_product_rule_and_the_default():
    default = _rule()
    product = _rule(product_code="crm", rate_bp=2000)
    plan = _rule(product_code="crm", plan_code="crm_professional", rate_bp=3000)
    pick = lambda **kw: domain.select_rule(  # noqa: E731
        [default, product, plan], partner_id=uuid.uuid4(), at=timezone.now(), **kw
    )
    assert pick(product_code="crm", plan_code="crm_professional") is plan
    assert pick(product_code="crm", plan_code="crm_starter") is product
    assert pick(product_code="books", plan_code="books_professional") is default


def test_a_fixed_rule_pays_its_amount_per_payment_in_its_currency_only():
    fixed = _rule(rate_bp=0, fixed_amount_minor=700, fixed_currency="GBP")
    gbp, usd = CurrencyCode.GBP, CurrencyCode.USD
    assert domain.rule_amount(fixed, basis_minor=4999, currency=gbp, whole=True) == 700
    assert domain.rule_amount(fixed, basis_minor=4999, currency=usd, whole=True) == 0
    # A plan change's increase is not a new customer payment.
    assert domain.rule_amount(fixed, basis_minor=1000, currency=gbp, whole=False) == 0
    rate = _rule(rate_bp=1500)
    assert domain.rule_amount(rate, basis_minor=4999, currency=gbp, whole=True) == 750


def test_a_recurring_rule_earns_only_inside_its_duration():
    rule = _rule(recurring=True, recurring_months=12)
    sold = datetime(2026, 1, 31, tzinfo=dt_timezone.utc)
    assert domain.add_months(sold, 1) == datetime(2026, 2, 28, tzinfo=dt_timezone.utc)
    assert domain.within_recurring_window(
        rule, sold_at=sold, paid_at=sold + timedelta(days=300)
    )
    assert not domain.within_recurring_window(
        rule, sold_at=sold, paid_at=sold + timedelta(days=370)
    )
    forever = _rule(recurring=True)
    assert domain.within_recurring_window(
        forever, sold_at=sold, paid_at=sold + timedelta(days=3000)
    )


def test_rule_terms_are_validated(commission_ctx):
    bad = [
        {"plan_code": "crm_starter"},  # a plan rule needs its product
        {"fixed_amount_minor": 500},  # a fixed amount needs its currency
        {"fixed_amount_minor": 0, "fixed_currency": "GBP"},
        {"recurring_months": 6},  # only a recurring rule has a duration
        {"recurring": True, "recurring_months": 0},
    ]
    for terms in bad:
        with pytest.raises(PartnerValidationError):
            rules.create_rule_version(
                commission_ctx,
                code=f"t-{uuid.uuid4().hex[:6]}",
                rate_bp=1000,
                reason="Rule test terms",
                **terms,
            )


def test_a_plan_specific_fixed_commission_accrues_on_a_paid_sale(channel, commission_ctx):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    rules.create_rule_version(
        commission_ctx,
        code="cr-crm-pro",
        rate_bp=0,
        reason="Fixed commission on CRM Professional",
        product_code="crm",
        plan_code="crm_professional",
        fixed_amount_minor=1200,
        fixed_currency="GBP",
    )
    managed, _owner = provision(channel)
    sell(channel, managed.organization_id)  # crm_professional
    (row,) = CommissionAccrual.objects.filter(organization_id=managed.organization_id)
    assert row.earning == "commission"
    assert row.amount_minor == 1200  # the plan's fixed rule, not the 10% default
