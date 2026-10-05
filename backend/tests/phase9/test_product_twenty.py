"""The future-product test (brief section 27): tomorrow Workeasy360 adds
Product #20.

It must take registering the product, its plans and prices, its integration
(SSO) and listing it - and **no change to this repository**. So the product
here has a code that appears nowhere in the source tree (asserted), and every
step goes through the same HTTP surfaces staff, the product and a customer use:
staff build it in the Super Admin console, the product's manifest registers
it, a visitor finds it in the public catalog, a customer buys it through a
pay-first order and launches it.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.extension.conftest import place_and_pay, url

pytestmark = pytest.mark.django_db

CODE = "fieldops"
SLUG = "field-operations"
CATALOG = "/api/v1/admin/catalog"
ROOT = pathlib.Path(__file__).resolve().parents[3]


def _staff_post(client, path, body):
    response = client.post(f"{CATALOG}/{path}", body, content_type="application/json")
    assert response.status_code in (200, 201), (path, response.content)
    return response.json()


def test_the_product_code_is_nowhere_in_the_source_tree():
    """What the rest of this module proves only means something if nothing in
    the platform or the shell knows the product in advance."""
    sources = [
        *(ROOT / "backend" / "workeasy360").rglob("*.py"),
        *(ROOT / "frontend" / "src").rglob("*.ts"),
        *(ROOT / "frontend" / "src").rglob("*.tsx"),
    ]
    assert len(sources) > 100
    mentions = [
        str(path.relative_to(ROOT))
        for path in sources
        if CODE in path.read_text(encoding="utf-8", errors="ignore").lower()
    ]
    assert mentions == []


def test_product_twenty_is_registered_sold_and_launched_without_code_changes(
    staff_login, catalog, register_product, console_org, login, fake_payments
):
    # 1-2. Staff register the product and its plan and price (Super Admin).
    staff, _ = staff_login(
        "platform.staff_catalog.manage", "platform.staff_pricing.manage"
    )
    product = _staff_post(
        staff,
        "products",
        {
            "code": CODE,
            "name": "Field Operations",
            "licensing_mode": "per_organization",
            "slug": SLUG,
            "tagline": "Dispatch, visits and job sheets.",
            "listing": "listed",
        },
    )
    _staff_post(staff, f"products/{product['id']}/status", {"status": "available"})
    plan = _staff_post(
        staff,
        "plans",
        {"product_id": product["id"], "code": f"{CODE}_team", "name": "Field Team"},
    )
    version = _staff_post(
        staff,
        "plan-versions",
        {"plan_id": plan["id"], "included_seats": 5, "trial_days": 0, "publish": True},
    )
    _staff_post(
        staff,
        "prices",
        {
            "price_book_id": str(catalog["price_book"].id),
            "plan_version_id": version["id"],
            "interval": "monthly",
            "amount_minor": 1900,
        },
    )
    _staff_post(staff, f"plans/{plan['id']}/status", {"status": "available"})

    # 4. The product registers its integration (manifest + credential).
    register_product(CODE)

    # 5. A visitor finds it in the public catalog, priced by the server.
    from django.test import Client

    visitor = Client()
    listed = visitor.get("/api/v1/public/catalog?currency=GBP").json()
    assert SLUG in {p["slug"] for p in listed["products"]}
    page = visitor.get(f"/api/v1/public/catalog/products/{SLUG}?currency=GBP")
    assert page.status_code == 200, page.content
    assert page.json()["product"]["name"] == "Field Operations"

    # A customer buys it: order -> payment -> subscription -> entitlement.
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "billing_email": "billing@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    before = owner.get(url(org["slug"], "apps")).json()["apps"]
    assert not any(a["product_code"] == CODE and a["can_launch"] for a in before)

    order = place_and_pay(
        owner, org["slug"], org["org"].id, plan_version_id=version["id"]
    )
    assert order["status"] == "fulfilled", order
    assert order["amount_due_minor"] == 1900

    # 6. It appears in My Products and launches through the one launcher.
    apps = {
        a["product_code"]: a for a in owner.get(url(org["slug"], "apps")).json()["apps"]
    }
    assert apps[CODE]["can_launch"] is True
    assert apps[CODE]["entitlement_status"] == "active"
    launched = owner.post(url(org["slug"], f"apps/{CODE}/launch"))
    assert launched.status_code == 302
    assert f"organization={org['org'].id}" in launched["Location"]
