"""A white-label partner's branded Store (ADR-053, Store Working Flow page 7):
the partner's brand over the central catalog, only the products it may sell,
at its prices - bought through the platform's own order, payment, subscription
and entitlement. Branding changes presentation, never authorization."""

from __future__ import annotations

import pytest

from tests.extension.conftest import pay_order, place_order, url
from tests.phase7.support import CAPS

pytestmark = pytest.mark.django_db

WHITE_LABEL = (*CAPS, "branding.manage", "domain.manage")


@pytest.fixture
def store(partner_factory, catalog):
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.models import PartnerBrand

    fx = partner_factory(*WHITE_LABEL, code="acme-partners")
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    PartnerBrand.objects.create(
        partner_id=fx["partner"].id,
        brand_name="Acme Cloud",
        accent_color="#0f766e",
        updated_by_id=fx["user"].id,
    )
    fx["catalog"] = catalog
    return fx


def test_the_store_lists_only_the_partners_products_at_its_prices(store, client):
    response = client.get("/api/v1/public/storefront/acme-partners")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["brand"]["brand_name"] == "Acme Cloud"
    assert [p["code"] for p in body["products"]] == ["crm"]  # books not granted
    plans = {p["plan_code"]: p for p in body["products"][0]["plans"]}
    assert plans["crm_professional"]["prices"] == [
        {"currency": "GBP", "interval": "monthly", "amount_minor": 4999}
    ]
    # Presentation only: nothing that identifies or authorizes.
    assert "partner_id" not in str(body) and "capabilit" not in str(body)


@pytest.mark.parametrize("missing", ["brand", "white_label", "status"])
def test_no_store_without_a_brand_the_white_label_pair_or_an_active_partner(
    store, client, missing
):
    from workeasy360.commercial.partners.models import PartnerAccount, PartnerBrand

    if missing == "brand":
        PartnerBrand.objects.filter(partner_id=store["partner"].id).delete()
    elif missing == "white_label":
        from workeasy360.commercial.partners import services

        services.revoke_capability(
            store["staff_ctx"],
            store["partner"].id,
            capability="branding.manage",
            reason="Test takes the brand capability away",
        )
    else:
        PartnerAccount.objects.filter(id=store["partner"].id).update(status="suspended")
    assert client.get("/api/v1/public/storefront/acme-partners").status_code == 404


def test_buying_in_the_store_is_a_central_order_attributed_to_the_partner(
    store, shop, staff_factory
):
    from workeasy360.commercial.commissions import margins
    from workeasy360.commercial.commissions.models import CommissionAccrual
    from workeasy360.commercial.partners import selectors

    _u, margin_ctx = staff_factory("platform.staff_commission.manage")
    margins.set_margin(
        margin_ctx, experience="white_label", discount_bp=2500, reason="White-label terms"
    )

    plan = str(shop["catalog"]["versions"]["crm_professional"].id)
    placed = place_order(
        shop["client"], shop["slug"], plan_version_id=plan, storefront="acme-partners"
    )
    assert placed.status_code == 201, placed.content
    order = placed.json()
    assert order["channel"] == "storefront" and order["status"] == "pending_payment"
    # Not the partner's customer until the order is paid and fulfilled.
    assert selectors.live_link_for_organization(shop["id"]) is None
    paid = pay_order(shop["client"], shop["slug"], shop["id"], order)
    assert paid["status"] == "fulfilled"
    link = selectors.live_link_for_organization(shop["id"])
    assert link is not None and link.partner_id == store["partner"].id
    accrual = CommissionAccrual.objects.get(organization_id=shop["id"])
    assert accrual.partner_id == store["partner"].id
    assert accrual.attribution_kind == "managed"
    # The white-label margin: 4999 at list, cost 3749 (25% off), margin 1250.
    assert (accrual.earning, accrual.cost_minor, accrual.amount_minor) == (
        "margin",
        3749,
        1250,
    )


def test_the_store_sells_only_what_the_partner_may_sell(store, shop):
    plan = str(shop["catalog"]["versions"]["books_professional"].id)
    refused = place_order(
        shop["client"], shop["slug"], plan_version_id=plan, storefront="acme-partners"
    )
    assert refused.status_code == 409, refused.content
    assert refused.json()["reason"] == "not_for_sale"


def test_a_customer_of_another_partner_cannot_buy_here(store, shop, channel):
    from django.utils import timezone

    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    PartnerOrganizationLink.objects.create(
        partner_id=channel["partner"].id,
        organization_id=shop["id"],
        started_at=timezone.now(),
        created_by_id=channel["user"].id,
    )
    plan = str(shop["catalog"]["versions"]["crm_professional"].id)
    refused = shop["client"].post(
        url(shop["slug"], "orders/quote"),
        {"plan_version_id": plan, "storefront": "acme-partners"},
        content_type="application/json",
    )
    assert refused.status_code == 409
    assert refused.json()["reason"] == "storefront_other_partner"


def test_an_unknown_store_is_refused_at_checkout(shop):
    plan = str(shop["catalog"]["versions"]["crm_professional"].id)
    refused = shop["client"].post(
        url(shop["slug"], "orders/quote"),
        {"plan_version_id": plan, "storefront": "nobody"},
        content_type="application/json",
    )
    assert refused.status_code == 409
    assert refused.json()["reason"] == "storefront_unavailable"
