"""Partner eligibility (Store Working Flow PDF page 8; ADR-051/052): the Product
Manager's Availability flags decide which channels a product may be offered
through. A partner product grant cannot widen them, a sale re-checks them, and
an affiliate earns nothing on a product closed to affiliates. Every flag is on
by default, so existing products and partners behave exactly as before."""

from __future__ import annotations

import pytest

from tests.phase7 import conftest as _phase7
from tests.phase7.support import CAPS, provision, sell
from workeasy360.commercial.commissions import referrals
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db

channel = _phase7.channel


def _close(product_code, **flags):
    from workeasy360.commercial.catalog.models import Product

    Product.objects.filter(code=product_code).update(**flags)


def test_a_reseller_cannot_be_granted_a_product_closed_to_resellers(
    catalog, partner_factory
):
    from workeasy360.commercial.partners import services

    fx = partner_factory(*CAPS)
    _close("books", reseller_enabled=False)
    with pytest.raises(PartnerValidationError) as refused:
        services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="books")
    assert refused.value.context["field"] == "product_code"
    # The other product is still open to resellers.
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")


def test_closing_a_product_to_resellers_stops_new_partner_sales_at_once(channel):
    from workeasy360.commercial.partners import selectors

    managed, _owner = provision(channel)
    assert selectors.may_sell(
        channel["partner"].id, product_code="crm", plan_code="crm_professional"
    )
    _close("crm", reseller_enabled=False)
    assert not selectors.may_sell(
        channel["partner"].id, product_code="crm", plan_code="crm_professional"
    )
    from workeasy360.contracts.errors import PermissionDenied

    with pytest.raises(PermissionDenied) as refused:
        sell(channel, managed.organization_id)
    assert refused.value.context["reason"] == "product_not_granted"
    _close("crm", reseller_enabled=True)
    sell(channel, managed.organization_id)


def test_a_branded_partner_needs_the_product_open_to_white_label(
    catalog, partner_factory
):
    from workeasy360.commercial.partners import services

    fx = partner_factory(*CAPS, "branding.manage")
    _close("crm", white_label_enabled=False)
    with pytest.raises(PartnerValidationError):
        services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    _close("crm", white_label_enabled=True, reseller_enabled=False)
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")


def test_an_affiliate_earns_nothing_on_a_product_closed_to_affiliates(
    partner_factory, staff_factory, organization_factory, catalog
):
    from workeasy360.commercial.billing import services as billing
    from workeasy360.commercial.commissions import rules
    from workeasy360.commercial.commissions.models import CommissionAccrual
    from workeasy360.commercial.subscriptions import services as subs
    from workeasy360.contracts.values.money import CurrencyCode
    from workeasy360.platform.authz.control_plane import (
        acting_scope,
        context_for_organization,
    )

    affiliate = partner_factory(
        "referral.create", "commission.earn", "commission.view", "payout.view"
    )
    _u, commission_ctx = staff_factory("platform.staff_commission.manage")
    rules.create_rule_version(
        commission_ctx, code="default", rate_bp=1000, reason="Programme default rate"
    )
    _close("crm", affiliate_enabled=False)

    def referred_customer_buys(plan):
        token = referrals.capture_referral(
            referrals.create_referral_code(affiliate["actor"]()).code
        )
        org, _m, owner = organization_factory()
        assert referrals.convert_referral(
            user_id=owner.id, token=token, organization_id=org.id
        )
        _s, sctx = staff_factory("platform.staff_subscription.manage")
        customer = context_for_organization(sctx, org.id)
        with acting_scope(customer):
            billing.open_billing_account(customer, currency=CurrencyCode.GBP)
            subs.purchase_plan(
                customer,
                plan_version_id=catalog["versions"][plan].id,
                idempotency_key=f"sale-{plan}",
            )
        return org.id

    closed = referred_customer_buys("crm_professional")
    assert not CommissionAccrual.objects.filter(organization_id=closed).exists()
    # The same affiliate still earns on a product open to affiliates.
    open_ = referred_customer_buys("books_professional")
    assert CommissionAccrual.objects.filter(
        organization_id=open_, attribution_kind="referral"
    ).exists()
