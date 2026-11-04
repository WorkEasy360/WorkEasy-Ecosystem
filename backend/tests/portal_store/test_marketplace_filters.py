"""The Store's marketplace filters (2026-11-04, the owner's marketplace
mock-up): a product's type (SaaS / AI tool / business suite), who it is best
for, the apps it connects to and a "Best Seller" badge - set by the Super
Admin on the product, shown on its Store card, filtered and counted on the
server in every portal's Store, and refused with a 400 when a filter is not
on offer. Presentation only: none of it decides who may buy what."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.extension.conftest import staff_scope
from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.catalog.models import Product
from workeasy360.contracts.errors import CatalogValidationError

pytestmark = pytest.mark.django_db

PUBLIC_STORE = "/api/v1/public/store"


@pytest.fixture
def public(shop):
    from django.core.cache import cache

    cache.clear()
    yield shop
    cache.clear()


def _public(query: str = "") -> dict:
    response = Client().get(f"{PUBLIC_STORE}?{query}")
    assert response.status_code == 200, response.content
    return response.json()


def _codes(page) -> list[str]:
    return sorted(p["code"] for p in page["products"])


def _facet(page, name) -> dict[str, int]:
    return {f["value"]: f["count"] for f in page["facets"][name]}


def _set(django_capture_on_commit_callbacks, code: str, **changes):
    product = Product.objects.get(code=code)
    with (
        django_capture_on_commit_callbacks(execute=True),
        staff_scope("platform.staff_catalog.manage") as ctx,
    ):
        catalog_services.update_product(ctx, product_id=product.id, changes=changes)


@pytest.fixture
def marked(public, django_capture_on_commit_callbacks):
    """CRM: an AI tool for agencies and startups, connecting WhatsApp and
    Gmail, a Best Seller. Books: left as it was (a SaaS product)."""
    _set(
        django_capture_on_commit_callbacks,
        "crm",
        product_type="ai_tool",
        best_for=["startups", "agencies", "startups"],
        integrations=["WhatsApp", "Gmail", "whatsapp"],
        best_seller=True,
    )
    return public


def test_a_new_product_is_a_saas_product_for_no_one_in_particular(public):
    books = next(p for p in _public()["products"] if p["code"] == "books")
    assert books["product_type"] == "saas"
    assert books["best_for"] == [] and books["integrations"] == []
    assert "best_seller" not in books["badges"]


def test_what_the_admin_sets_shows_on_the_card_cleaned(marked):
    crm = next(p for p in _public()["products"] if p["code"] == "crm")
    assert crm["product_type"] == "ai_tool"
    # Kept in the fixed list's order, duplicates dropped (ignoring case).
    assert crm["best_for"] == ["startups", "agencies"]
    assert crm["integrations"] == ["WhatsApp", "Gmail"]
    assert crm["badges"][0] == "best_seller"


def test_the_public_store_filters_and_counts_by_the_new_facets(marked):
    page = _public()
    assert _facet(page, "product_types") == {
        "all": 2,
        "saas": 1,
        "ai_tool": 1,
        "business_suite": 0,
    }
    assert _facet(page, "best_for")["agencies"] == 1
    assert _facet(page, "best_for")["enterprises"] == 0
    assert _facet(page, "integrations") == {"Gmail": 1, "WhatsApp": 1}
    assert _facet(page, "collections")["best_seller"] == 1

    assert _codes(_public("product_type=ai_tool")) == ["crm"]
    assert _codes(_public("product_type=saas")) == ["books"]
    assert _codes(_public("best_for=agencies")) == ["crm"]
    assert _codes(_public("best_for=enterprises")) == []
    # An integration is matched ignoring case.
    assert _codes(_public("integration=whatsapp")) == ["crm"]
    assert _codes(_public("collection=best_seller")) == ["crm"]
    # Each facet counts what the *other* filters leave.
    narrowed = _public("product_type=saas")
    assert _facet(narrowed, "product_types")["ai_tool"] == 1
    assert _facet(narrowed, "best_for")["agencies"] == 0
    assert narrowed["applied"]["product_type"] == "saas"


@pytest.mark.parametrize(
    "query",
    [
        "product_type=hardware",
        "best_for=aliens",
        "integration=" + "x" * 33,
        "integration=" + ",".join(f"app{i}" for i in range(13)),
        "collection=bestseller",
    ],
)
def test_a_filter_not_on_offer_is_refused(public, query):
    response = Client().get(f"{PUBLIC_STORE}?{query}")
    assert response.status_code == 400, response.content


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"product_type": "hardware"}, "product_type"),
        ({"product_type": ""}, "product_type"),
        ({"best_for": ["aliens"]}, "best_for"),
        ({"best_for": "startups"}, "best_for"),
        ({"integrations": [f"app{i}" for i in range(13)]}, "integrations"),
        ({"integrations": ["x" * 33]}, "integrations"),
        ({"integrations": ["<b>\x07</b>"]}, "integrations"),
        ({"best_seller": "yes"}, "best_seller"),
    ],
)
def test_the_admin_cannot_store_a_value_outside_the_rules(public, changes, field):
    product = Product.objects.get(code="crm")
    with (
        staff_scope("platform.staff_catalog.manage") as ctx,
        pytest.raises(CatalogValidationError) as exc,
    ):
        catalog_services.update_product(ctx, product_id=product.id, changes=changes)
    assert exc.value.context["field"] == field
    product.refresh_from_db()
    assert (product.product_type, product.best_for, product.integrations) == (
        "saas",
        [],
        [],
    )


def test_a_customer_store_offers_the_same_filters(marked, shop):
    from tests.extension.conftest import url

    response = shop["client"].get(url(shop["slug"], "store?product_type=ai_tool"))
    assert response.status_code == 200, response.content
    assert _codes(response.json()) == ["crm"]
    refused = shop["client"].get(url(shop["slug"], "store?best_for=aliens"))
    assert refused.status_code == 400


def test_the_super_admin_store_filters_by_the_new_facets(marked):
    from workeasy360.commercial.controlplane import store

    with staff_scope("platform.staff_catalog.view") as ctx:
        page = store.store(ctx, best_for=["agencies"], integration=["GMAIL"])
    assert _codes(page) == ["crm"]
    assert _facet(page, "product_types")["ai_tool"] == 1
