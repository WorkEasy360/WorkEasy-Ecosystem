"""Query bounds for the Phase 9 read paths that grow with the platform: the
public catalog (every product, plan, price and feature) and the order lists
(a customer's and the staff view across customers). Each must cost the same
number of queries whatever the number of rows - an N+1 here would be paid by
every anonymous visitor and every page of the Super Admin console.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from tests.extension.conftest import place_order, url

pytestmark = pytest.mark.django_db


def _queries(fetch) -> tuple[int, object]:
    with CaptureQueriesContext(connection) as captured:
        response = fetch()
    assert response.status_code == 200, response.content
    return len(captured.captured_queries), response.json()


def _add_products(platform_admin, in_tenant, catalog, count: int) -> None:
    """``count`` more listed products, each with a public feature and a priced
    plan - the shape that would expose a per-product query."""
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.contracts.enums import (
        BillingInterval,
        CatalogStatus,
        FeatureKind,
        ProductStatus,
    )

    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        for n in range(count):
            code = f"extra{n}"
            product = catalog_services.create_product(ctx, code=code, name=f"Extra {n}")
            catalog_services.set_product_status(
                ctx, product_id=product.id, status=ProductStatus.AVAILABLE
            )
            catalog_services.create_feature(
                ctx,
                product_id=product.id,
                key=f"{code}.reports.enabled",
                name="Reports",
                kind=FeatureKind.BOOLEAN,
                default_value=False,
            )
            plan = catalog_services.create_plan(
                ctx, product_id=product.id, code=f"{code}_std", name="Standard"
            )
            catalog_services.set_plan_status(
                ctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE
            )
            version = catalog_services.draft_plan_version(
                ctx, plan_id=plan.id, features={f"{code}.reports.enabled": True}
            )
            catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
            pricing_services.publish_price(
                ctx,
                price_book_id=catalog["price_book"].id,
                plan_version_id=version.id,
                interval=BillingInterval.MONTHLY,
                amount_minor=1000 + n,
            )


def test_the_public_catalog_costs_the_same_whatever_its_size(
    catalog, platform_admin, in_tenant
):
    visitor = Client()

    def fetch():
        cache.clear()  # measure the build, not the cache
        return visitor.get("/api/v1/public/catalog?currency=GBP")

    small, body = _queries(fetch)
    assert len(body["products"]) == 2

    _add_products(platform_admin, in_tenant, catalog, 4)
    large, body = _queries(fetch)
    assert len(body["products"]) == 6
    assert all(p["features"] and p["plans"] for p in body["products"])
    assert large == small


@pytest.fixture
def buyer(console_org, login, catalog, fake_payments):
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "billing_email": "billing@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    return {**org, "client": owner, "catalog": catalog}


def _place_and_cancel(buyer, times: int) -> None:
    plan = str(buyer["catalog"]["versions"]["crm_professional"].id)
    for _ in range(times):
        placed = place_order(buyer["client"], buyer["slug"], plan_version_id=plan)
        assert placed.status_code == 201, placed.content
        cancelled = buyer["client"].post(
            url(buyer["slug"], f"orders/{placed.json()['order_id']}/cancel")
        )
        assert cancelled.status_code == 200, cancelled.content


def test_an_order_page_costs_the_same_whatever_its_length(buyer, staff_login):
    staff, _ = staff_login("platform.staff_payment.view")

    def mine():
        return buyer["client"].get(url(buyer["slug"], "orders"))

    def everyone():
        return staff.get("/api/v1/admin/orders")

    _place_and_cancel(buyer, 1)
    small_mine, body = _queries(mine)
    small_staff, staff_body = _queries(everyone)
    assert len(body["results"]) == 1 and len(staff_body["results"]) == 1

    _place_and_cancel(buyer, 4)
    large_mine, body = _queries(mine)
    large_staff, staff_body = _queries(everyone)
    assert len(body["results"]) == 5 and len(staff_body["results"]) == 5
    assert large_mine == small_mine
    assert large_staff == small_staff
