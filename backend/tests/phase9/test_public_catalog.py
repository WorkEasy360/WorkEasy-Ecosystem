"""The public site's catalog and the product manager (Phase 9): data-driven
listing, availability states, anonymous access that reveals nothing private,
product editing with validation and audit, and platform settings."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client

from tests.phase9.conftest import PASSWORD

pytestmark = pytest.mark.django_db

CATALOG = "/api/v1/public/catalog"


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


def _catalog(currency=None):
    response = Client().get(CATALOG, {"currency": currency} if currency else {})
    assert response.status_code == 200, response.content
    return response.json()


def _edit(staff, product_id, **changes):
    return staff.patch(
        f"/api/v1/admin/catalog/products/{product_id}",
        changes,
        content_type="application/json",
    )


def test_the_catalog_is_anonymous_and_built_from_the_database(catalog):
    body = _catalog()
    assert body["currency"] == "GBP" and body["currencies"] == ["GBP"]
    products = {p["code"]: p for p in body["products"]}
    assert set(products) == {"crm", "books"}
    crm = products["crm"]
    assert (crm["slug"], crm["availability"]) == ("crm", "available")
    assert [p["plan_code"] for p in crm["plans"]] == [
        "crm_professional",
        "crm_starter",
    ] or [p["plan_code"] for p in crm["plans"]] == ["crm_starter", "crm_professional"]
    assert crm["starting_price"] == {
        "amount_minor": 1499,
        "currency": "GBP",
        "interval": "monthly",
    }
    assert {f["name"] for f in crm["features"]} == {"Pipelines", "Sales forecasting"}
    assert [b["bundle_code"] for b in body["bundles"]] == ["sales_suite"]


def test_an_unknown_currency_falls_back_to_one_that_has_prices(catalog):
    assert _catalog("XYZ")["currency"] == "GBP"
    assert _catalog("usd")["currency"] == "GBP"


def test_the_catalog_reveals_no_private_or_internal_detail(catalog):
    text = Client().get(CATALOG).content.decode()
    for forbidden in (
        "launch_url",
        "client_id",
        "price_book",
        "partner",
        "licensing_mode",
    ):
        assert forbidden not in text, forbidden


def test_listing_controls_what_a_visitor_sees(catalog, staff_login):
    staff, _ = staff_login("platform.staff_catalog.manage")
    books = catalog["products"]["books"]
    assert _edit(staff, books.id, listing="hidden").status_code == 200
    cache.clear()
    body = _catalog()
    assert [p["code"] for p in body["products"]] == ["crm"]
    # A bundle that would sell a hidden product is no longer an offer.
    assert body["bundles"] == []
    assert Client().get(f"{CATALOG}/products/books").status_code == 404

    assert _edit(staff, books.id, listing="coming_soon").status_code == 200
    cache.clear()
    books_row = next(p for p in _catalog()["products"] if p["code"] == "books")
    # Announced, but nothing on sale.
    assert (books_row["availability"], books_row["plans"]) == ("coming_soon", [])


def test_a_draft_or_withdrawn_product_is_indistinguishable_from_none(
    catalog, staff_login
):
    staff, _ = staff_login("platform.staff_catalog.manage")
    retired = staff.post(
        f"/api/v1/admin/catalog/products/{catalog['products']['books'].id}/status",
        {"status": "deprecated"},
        content_type="application/json",
    )
    assert retired.status_code == 200, retired.content
    cache.clear()
    assert [p["code"] for p in _catalog()["products"]] == ["crm"]
    assert Client().get(f"{CATALOG}/products/books").status_code == 404
    assert Client().get(f"{CATALOG}/products/never-existed").status_code == 404


def test_the_product_page_includes_the_bundles_that_sell_it(catalog):
    response = Client().get(f"{CATALOG}/products/crm")
    assert response.status_code == 200
    body = response.json()
    assert body["product"]["code"] == "crm"
    assert [b["bundle_code"] for b in body["bundles"]] == ["sales_suite"]


def test_the_catalog_is_rate_limited(catalog, settings):
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "public_catalog_ip": (2, 60),
    }
    client = Client(REMOTE_ADDR="10.9.9.9")
    assert [client.get(CATALOG).status_code for _ in range(3)] == [200, 200, 429]


# --- product manager --------------------------------------------------------------


def test_editing_a_product_validates_audits_and_never_touches_its_code(
    catalog, staff_login
):
    from django.utils import timezone

    from workeasy360.commercial.catalog.models import Product
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    staff, _ = staff_login("platform.staff_catalog.manage")
    crm = catalog["products"]["crm"]
    # ADR-051: a slug is fixed once the product has been on sale, so the slug
    # edit is made while CRM has never been activated.
    Product.objects.filter(id=crm.id).update(activated_at=None)
    ok = _edit(
        staff,
        crm.id,
        name="Workeasy CRM",
        tagline="Pipelines that close",
        slug="workeasy-crm",
        marketing_url="https://www.example.com/crm",
        sort_order=5,
    )
    assert ok.status_code == 200, ok.content
    assert ok.json()["code"] == "crm" and ok.json()["slug"] == "workeasy-crm"
    Product.objects.filter(id=crm.id).update(activated_at=timezone.now())
    refused = {
        "code": _edit(staff, crm.id, code="other"),
        "licensing_mode": _edit(staff, crm.id, licensing_mode="usage"),
        "javascript": _edit(staff, crm.id, marketing_url="javascript:alert(1)"),
        "http": _edit(staff, crm.id, marketing_url="http://example.com"),
        "internal host": _edit(staff, crm.id, marketing_url="https://169.254.169.254/"),
        "slug": _edit(staff, crm.id, slug="Not A Slug"),
        "slug after launch": _edit(staff, crm.id, slug="crm-renamed"),
        "listing": _edit(staff, crm.id, listing="everywhere"),
        "duplicate slug": _edit(
            staff, catalog["products"]["books"].id, slug="workeasy-crm"
        ),
    }
    for label, response in refused.items():
        assert response.status_code == 400, label
    with (
        unscoped_access(reason="test reads the platform log", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        entry = (
            AuditEvent.unscoped.filter(
                organization_id=PLATFORM_ORGANIZATION_ID,
                action="commercial.product.updated",
            )
            .order_by("-occurred_at")
            .first()
        )
    assert entry is not None and "slug" in entry.changes["fields"]
    cache.clear()
    assert Client().get(f"{CATALOG}/products/workeasy-crm").status_code == 200


def test_editing_products_needs_the_catalog_grant(catalog, staff_login):
    crm = catalog["products"]["crm"]
    viewer, _ = staff_login("platform.staff_catalog.view")
    assert _edit(viewer, crm.id, tagline="x").status_code == 403
    manager, _ = staff_login("platform.staff_catalog.manage")
    assert _edit(manager, crm.id, tagline="x").status_code == 200


def test_a_customer_cannot_reach_the_product_manager(catalog, console_org, login):
    org = console_org()
    owner = login(org["owner"])
    response = _edit(owner, catalog["products"]["crm"].id, tagline="owned")
    assert response.status_code == 403


def test_explore_hides_what_the_public_site_hides(
    catalog, console_org, login, staff_login
):
    staff, _ = staff_login("platform.staff_catalog.manage")
    _edit(staff, catalog["products"]["books"].id, listing="hidden")
    org = console_org()
    member = login(org["add"]("member")["user"])
    offers = member.get(f"/api/v1/orgs/{org['slug']}/marketplace").json()
    assert {p["product_code"] for p in offers["plans"]} == {"crm"}
    assert offers["bundles"] == []


# --- platform settings ---------------------------------------------------------------


def test_platform_settings_are_permissioned_versioned_and_public_in_part(staff_login):
    viewer, _ = staff_login("platform.staff_settings.view")
    current = viewer.get("/api/v1/admin/settings").json()
    assert current["version"] == 0 and current["signup_enabled"] is True
    body = {
        "signup_enabled": False,
        "support_email": "help@example.com",
        "sales_email": "sales@example.com",
        "public_catalog_currency": "INR",
        "version": 0,
    }
    assert (
        viewer.put(
            "/api/v1/admin/settings", body, content_type="application/json"
        ).status_code
        == 403
    )
    manager, _ = staff_login("platform.staff_settings.manage")
    saved = manager.put("/api/v1/admin/settings", body, content_type="application/json")
    assert saved.status_code == 200, saved.content
    assert saved.json()["version"] == 1
    stale = manager.put("/api/v1/admin/settings", body, content_type="application/json")
    assert stale.status_code == 409
    invalid = manager.put(
        "/api/v1/admin/settings",
        {**body, "support_email": "nope", "version": 1},
        content_type="application/json",
    )
    assert invalid.status_code == 400
    public = Client().get("/api/v1/public/site").json()
    assert public == {
        "signup_enabled": False,
        "support_email": "help@example.com",
        "sales_email": "sales@example.com",
        "catalog_currency": "INR",
    }


def test_a_customer_owner_cannot_change_platform_settings(console_org, login):
    org = console_org()
    owner = login(org["owner"])
    assert owner.get("/api/v1/admin/settings").status_code == 403
    assert PASSWORD
