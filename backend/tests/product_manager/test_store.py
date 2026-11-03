"""The Super Admin Store (2026-10-31): the central catalog as a marketplace page.

Every product is a card with the price a customer would see it from - read from
the same offers and direct books as the public catalog, so a product that is not
on sale never shows a price - and the page's filters, facet counts and sorting
are the server's. Read only, and staff only.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.utils import timezone

from tests.extension.conftest import staff_scope
from tests.product_manager.conftest import API

pytestmark = pytest.mark.django_db

STORE = f"{API}/store"
VIEW = "platform.staff_catalog.view"


@pytest.fixture
def viewer(staff_login, catalog):
    client, _user = staff_login(VIEW)
    return client


def _get(client, query=""):
    response = client.get(f"{STORE}{query}")
    assert response.status_code == 200, response.content
    return response.json()


def _codes(data):
    return [c["code"] for c in data["products"]]


def _set(code, **fields):
    from workeasy360.commercial.catalog.models import Product

    Product.objects.filter(code=code).update(**fields)


def test_every_product_is_a_card_priced_from_the_direct_books(viewer):
    data = _get(viewer)
    assert data["currency"] == "GBP" and data["currencies"] == ["GBP"]
    assert data["count"] == data["total"] == 2
    cards = {c["code"]: c for c in data["products"]}
    # The public catalog's "from" price: the lowest monthly standard plan.
    assert cards["crm"]["price"] == {
        "amount_minor": 1499,
        "currency": "GBP",
        "interval": "monthly",
        "plan_name": "CRM Starter",
        # ADR-056: no promotional discount on this price.
        "list_amount_minor": None,
        "discount_bp": 0,
    }
    assert cards["books"]["price"]["amount_minor"] == 2999
    assert cards["crm"]["store_status"] == "live"
    assert cards["crm"]["plans_on_sale"] == 2
    assert cards["crm"]["plan_types"] == ["monthly"]
    assert cards["crm"]["yearly_deal"] is None
    assert set(cards["crm"]["programs"]) >= {"customer_store"}
    assert data["facets"]["price"] == {"min_minor": 1499, "max_minor": 2999}


def test_a_product_not_in_the_store_shows_no_price_and_says_why(viewer):
    _set("books", listing="hidden")
    cards = {c["code"]: c for c in _get(viewer)["products"]}
    assert cards["books"]["store_status"] == "not_listed"
    assert cards["books"]["price"] is None and cards["books"]["plans_on_sale"] == 0
    assert cards["books"]["ribbon"] == {"kind": "hidden", "label": "Not in the store"}

    _set("books", listing="coming_soon")
    cards = {c["code"]: c for c in _get(viewer)["products"]}
    assert cards["books"]["ribbon"]["kind"] == "coming_soon"
    assert cards["books"]["price"] is None


def test_a_recently_activated_product_wears_the_new_ribbon(viewer):
    _set("crm", activated_at=timezone.now() - timezone.timedelta(days=2))
    _set("books", activated_at=timezone.now() - timezone.timedelta(days=90))
    cards = {c["code"]: c for c in _get(viewer)["products"]}
    assert cards["crm"]["ribbon"] == {"kind": "new", "label": "New!"}
    assert cards["books"]["ribbon"] is None


def test_a_yearly_price_below_twelve_months_is_shown_against_that_real_total(
    viewer, catalog
):
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.contracts.enums import BillingInterval

    with staff_scope("platform.staff_pricing.manage") as ctx:
        pricing_services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            interval=BillingInterval.ANNUAL,
            amount_minor=14990,
            valid_from=timezone.now() - timezone.timedelta(days=1),
        )
    crm = next(c for c in _get(viewer)["products"] if c["code"] == "crm")
    assert crm["price"]["amount_minor"] == 1499  # still "from" the monthly price
    assert crm["yearly_deal"] == {
        "amount_minor": 14990,
        "compare_at_minor": 17988,
        "currency": "GBP",
        "save_percent": 17,
    }
    assert crm["plan_types"] == ["monthly", "annual"]


def test_filters_narrow_the_cards_and_each_facet_counts_the_other_filters(viewer):
    _set("crm", category="Sales", reseller_enabled=True)
    _set("books", category="Finance", reseller_enabled=False)

    data = _get(viewer, "?category=Sales")
    assert _codes(data) == ["crm"]
    # The category facet ignores the category filter itself.
    assert {f["value"]: f["count"] for f in data["facets"]["categories"]} == {
        "Finance": 1,
        "Sales": 1,
    }
    assert _codes(_get(viewer, "?program=reseller")) == ["crm"]
    assert _codes(_get(viewer, "?q=book")) == ["books"]
    assert _codes(_get(viewer, "?price_min=20&price_max=40")) == ["books"]
    assert _codes(_get(viewer, "?price_max=10")) == []
    assert _codes(_get(viewer, "?status=not_listed")) == []
    both = _get(viewer, "?status=live,coming_soon&plan_type=monthly")
    assert sorted(_codes(both)) == ["books", "crm"]


def test_sorting(viewer):
    _set("crm", sort_order=2)
    _set("books", sort_order=1, listing="hidden")
    assert _codes(_get(viewer)) == ["books", "crm"]  # recommended: the catalog order
    assert _codes(_get(viewer, "?sort=price_desc")) == ["crm", "books"]  # unpriced last
    assert _codes(_get(viewer, "?sort=name")) == ["books", "crm"]


@pytest.mark.parametrize(
    "query",
    [
        "?sort=cheapest",
        "?program=everyone",
        "?status=live,sold_out",
        "?plan_type=weekly",
        "?price_min=abc",
        "?price_min=50&price_max=10",
        "?currency=EUR",
        f"?q={'x' * 101}",
    ],
)
def test_an_unknown_or_impossible_filter_is_refused_not_ignored(viewer, query):
    assert viewer.get(f"{STORE}{query}").status_code == 400


def test_only_staff_with_the_catalog_view_may_open_the_store(
    staff_login, console_org, login, catalog
):
    other, _user = staff_login("platform.staff_customer.view")
    assert other.get(STORE).status_code == 403
    owner = login(console_org()["owner"])
    assert owner.get(STORE).status_code in (401, 403, 404)
    assert Client().get(STORE).status_code in (401, 403)
    manager, _user = staff_login("platform.staff_catalog.manage")
    assert manager.post(STORE, {}).status_code == 405  # read only
