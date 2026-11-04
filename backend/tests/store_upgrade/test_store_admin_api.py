"""ADR-056 over HTTP: Super Admin's Store dashboard, product-wise commercial
rules, plans & pricing board, plan duplication and price actions; the
customer's product page with its plan comparison; and a price change reaching
every Store level from the one source."""

from __future__ import annotations

import pytest

from tests.extension.conftest import url
from tests.store_upgrade.conftest import list_products
from workeasy360.commercial.pricing import services as pricing
from workeasy360.contracts.enums import BillingInterval

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin/store"
VIEWER = ("platform.staff_catalog.view", "platform.staff_commission.view")
MANAGER = (
    *VIEWER,
    "platform.staff_catalog.manage",
    "platform.staff_pricing.manage",
    "platform.staff_commission.manage",
)


def test_store_endpoints_need_staff_grants(catalog, staff_login, login, console_org):
    nobody, _staff = staff_login("platform.staff_user.view")
    for path in ("/dashboard", "/commercial-rules", "/price-ranges"):
        assert nobody.get(ADMIN + path).status_code == 403
    customer = login(console_org()["owner"])
    assert customer.get(ADMIN + "/dashboard").status_code in (401, 403)


def test_the_dashboard_counts_the_catalog(catalog, staff_login):
    client, _staff = staff_login(*VIEWER)
    data = client.get(ADMIN + "/dashboard?days=30").json()
    assert data["catalog"]["plans_active"] >= 3
    assert data["period"]["days"] == 30
    assert "sales" not in data  # needs the payments grant
    assert client.get(ADMIN + "/dashboard?days=0").status_code == 400


def test_commercial_rules_show_every_level_from_one_price(catalog, staff_login):
    client, _staff = staff_login(*VIEWER)
    data = client.get(ADMIN + "/commercial-rules?currency=GBP").json()
    crm = next(p for p in data["products"] if p["code"] == "crm")
    pro = next(p for p in crm["plans"] if p["code"] == "crm_professional")
    monthly = {c: q["monthly"] for c, q in pro["channels"].items()}
    assert {q["customer_minor"] for q in monthly.values()} == {4999}
    assert monthly["direct"]["platform_share_minor"] == 4999
    assert set(crm["channels"]) == {
        "customer_store",
        "affiliate",
        "reseller",
        "white_label",
    }
    assert client.get(ADMIN + "/commercial-rules?currency=XXX").status_code == 400


def test_setting_product_commission_from_the_rules_screen(catalog, staff_login):
    client, _staff = staff_login(*MANAGER)
    made = client.post(
        ADMIN + "/commission",
        {
            "product_code": "crm",
            "rate_bp": 3000,
            "recurring": True,
            "recurring_months": 12,
        },
        content_type="application/json",
    )
    assert made.status_code == 201, made.content
    assert made.json()["code"] == "cr-crm"
    crm = next(
        p
        for p in client.get(ADMIN + "/commercial-rules").json()["products"]
        if p["code"] == "crm"
    )
    assert crm["affiliate_commission"]["rate_bp"] == 3000
    assert crm["affiliate_commission"]["recurring_months"] == 12
    pro = next(p for p in crm["plans"] if p["code"] == "crm_professional")
    assert pro["channels"]["affiliate"]["monthly"]["commission_minor"] == 1500


def test_quote_preview_is_the_engine(catalog, staff_login):
    client, _staff = staff_login(*VIEWER)
    version = catalog["versions"]["books_professional"]
    q = client.get(
        ADMIN
        + f"/quote?plan_version_id={version.id}&interval=monthly&currency=GBP&channel=direct"
    ).json()
    assert q["customer_minor"] == 2999 and q["payable_minor"] == 2999
    bad = client.get(
        ADMIN + f"/quote?plan_version_id={version.id}&channel=nope&currency=GBP"
    )
    assert bad.status_code == 400


def test_plan_board_edit_price_schedule_and_duplicate(catalog, staff_login):
    client, _staff = staff_login(*MANAGER)
    product = catalog["products"]["crm"]
    board = client.get(ADMIN + f"/products/{product.id}/plans").json()
    pro = next(p for p in board["plans"] if p["code"] == "crm_professional")
    assert [r["amount_minor"] for r in pro["prices"]] == [4999]
    assert board["books"][0]["currency"] == "GBP"

    # Edit price through the shared prices endpoint, with discount, fee and a start date.
    from datetime import timedelta

    from django.utils import timezone

    starts = (timezone.now() + timedelta(days=5)).isoformat()
    published = client.post(
        "/api/v1/admin/catalog/prices",
        {
            "price_book_id": board["books"][0]["id"],
            "plan_version_id": pro["version"]["id"],
            "interval": "monthly",
            "amount_minor": 5999,
            "discount_bp": 1000,
            "setup_fee_minor": 2500,
            "valid_from": starts,
        },
        content_type="application/json",
    )
    assert published.status_code == 201, published.content
    board = client.get(ADMIN + f"/products/{product.id}/plans").json()
    pro = next(p for p in board["plans"] if p["code"] == "crm_professional")
    scheduled = [r for r in pro["prices"] if r["state"] == "scheduled"]
    assert [
        (r["amount_minor"], r["net_amount_minor"], r["setup_fee_minor"])
        for r in scheduled
    ] == [(5999, 5399, 2500)]
    assert (
        client.post(ADMIN + f"/prices/{scheduled[0]['price_id']}/cancel").status_code
        == 204
    )

    copied = client.post(
        ADMIN + f"/plans/{pro['id']}/duplicate",
        {"code": "crm_professional_plus", "name": "CRM Professional Plus"},
        content_type="application/json",
    )
    assert copied.status_code == 201, copied.content
    assert copied.json()["prices_copied"] == 1
    board = client.get(ADMIN + f"/products/{product.id}/plans").json()
    plus = next(p for p in board["plans"] if p["code"] == "crm_professional_plus")
    assert plus["status"] == "draft" and plus["draft_version"]["included_seats"] == 10


def test_recommended_is_one_plan_per_product(catalog, staff_login):
    client, _staff = staff_login(*MANAGER)
    plans = catalog["plans"]
    for code in ("crm_starter", "crm_professional"):
        r = client.patch(
            f"/api/v1/admin/catalog/plans/{plans[code].id}",
            {"recommended": True},
            content_type="application/json",
        )
        assert r.status_code == 200, r.content
    product = catalog["products"]["crm"]
    product.refresh_from_db()
    # One reference per product: the second choice replaced the first.
    assert product.recommended_plan_id == plans["crm_professional"].id
    board = client.get(ADMIN + f"/products/{product.id}/plans").json()
    flags = {p["code"]: p["recommended"] for p in board["plans"]}
    assert flags["crm_professional"] is True and flags["crm_starter"] is False


def test_price_ranges_over_http(catalog, staff_login):
    client, _staff = staff_login(*MANAGER)
    plan = catalog["plans"]["crm_professional"]
    made = client.post(
        ADMIN + "/price-ranges",
        {
            "experience": "white_label",
            "plan_id": str(plan.id),
            "interval": "monthly",
            "currency": "GBP",
            "min_minor": 50000,
            "max_minor": 90000,
            "partner_may_set": True,
        },
        content_type="application/json",
    )
    assert made.status_code == 201, made.content
    assert client.get(ADMIN + "/price-ranges").json()["results"][0]["max_minor"] == 90000
    assert client.delete(ADMIN + f"/price-ranges/{made.json()['id']}").status_code == 204


# --- customer -----------------------------------------------------------------------------


def test_the_customer_product_page_compares_plans(shop):
    list_products("crm", "books")
    page = shop["client"].get(url(shop["slug"], "store/crm"))
    assert page.status_code == 200, page.content
    data = page.json()
    assert data["product"]["code"] == "crm"
    codes = [p["plan_code"] for p in data["plans"]]
    assert {"crm_starter", "crm_professional"} <= set(codes)
    rows = {r["key"]: r for r in data["comparison"]}
    assert "seats" in rows and "crm.pipelines.max" in rows
    pro = next(p for p in data["plans"] if p["plan_code"] == "crm_professional")
    assert rows["crm.pipelines.max"]["values"][pro["plan_version_id"]] == 25
    assert shop["client"].get(url(shop["slug"], "store/nothing-here")).status_code == 404


def test_a_price_change_reaches_the_customer_store(shop, staff_login):
    from tests.extension.conftest import staff_scope

    list_products("crm", "books")
    catalog = shop["catalog"]
    with staff_scope(
        "platform.staff_catalog.manage", "platform.staff_pricing.manage"
    ) as ctx:
        pricing.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"]["books_professional"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=3999,
            discount_bp=2500,
        )
    store = shop["client"].get(url(shop["slug"], "store")).json()
    books = next(c for c in store["products"] if c["code"] == "books")
    assert books["price"]["amount_minor"] == 2999  # 3999 less 25%, half up
    assert books["price"]["list_amount_minor"] == 3999
    detail = shop["client"].get(url(shop["slug"], "store/books")).json()
    (plan,) = detail["plans"]
    assert plan["prices"][0]["amount_minor"] == 2999
    quoted = (
        shop["client"]
        .post(
            url(shop["slug"], "orders/quote"),
            {"plan_version_id": plan["plan_version_id"]},
            content_type="application/json",
        )
        .json()
    )
    assert quoted["amount_due_minor"] == 2999  # card, page and checkout agree


def test_store_collections_filter_featured_products(shop, staff_login):
    list_products("crm", "books")
    from workeasy360.commercial.catalog.models import Product

    Product.objects.filter(code="books").update(featured=True, tags=["accounting"])
    store = shop["client"].get(url(shop["slug"], "store") + "?collection=featured").json()
    assert [c["code"] for c in store["products"]] == ["books"]
    assert "featured" in store["products"][0]["badges"]
    found = shop["client"].get(url(shop["slug"], "store") + "?q=accounting").json()
    assert [c["code"] for c in found["products"]] == ["books"]
    assert (
        shop["client"].get(url(shop["slug"], "store") + "?collection=bogus").status_code
        == 400
    )
