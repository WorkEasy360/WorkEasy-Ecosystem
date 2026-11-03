"""The Store in every portal (2026-10-31): a customer organization, an
affiliate, a reseller and a white-label partner each get the Super Admin
Store's marketplace page over **exactly the list they already had** - a
customer's Explore, a partner's Store Console Explore - with the same filters,
facet counts and sorting, refused with a 400 for a filter not on offer."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.affiliate_products.conftest import sign_in
from tests.extension.conftest import url
from tests.phase7.support import CAPS
from tests.store_console.conftest import ask
from workeasy360.commercial.controlplane import partner_workspaces, portal_store

pytestmark = pytest.mark.django_db

WHITE_LABEL = (*CAPS, "branding.manage", "domain.manage")


def _codes(page) -> list[str]:
    return [p["code"] for p in page["products"]]


def _facet(page, name) -> dict[str, int]:
    return {f["value"]: f["count"] for f in page["facets"][name]}


# --- customer ---------------------------------------------------------------------------------


def test_a_customer_store_is_its_explore_list_as_a_marketplace(shop):
    explore = shop["client"].get(url(shop["slug"], "explore")).json()
    response = shop["client"].get(url(shop["slug"], "store"))
    assert response.status_code == 200, response.content
    page = response.json()

    # The same products in the same order: the Store never widens Explore.
    assert _codes(page) == [p["code"] for p in explore["products"]]
    assert page["currency"] == "GBP" and page["currencies"] == ["GBP"]
    assert page["billing_account"] is True
    crm = next(p for p in page["products"] if p["code"] == "crm")
    assert crm["status"] == "available"
    assert crm["price"]["amount_minor"] == 1499
    assert crm["price"]["interval"] == "monthly"
    # Explore's own facts ride along, so the card's buttons are Explore's.
    assert crm["trial_state"] == explore["products"][0]["trial_state"]
    assert {p["plan_code"] for p in crm["plans"]} >= {"crm_starter", "crm_professional"}
    assert _facet(page, "statuses")["available"] == len(page["products"])
    assert page["facets"]["price"] == {"min_minor": 1499, "max_minor": 2999}


def test_a_customer_store_filters_and_sorts_on_the_server(shop):
    def get(query: str):
        response = shop["client"].get(url(shop["slug"], f"store?{query}"))
        assert response.status_code == 200, response.content
        return response.json()

    assert _codes(get("q=book")) == ["books"]
    assert _codes(get("sort=price_desc"))[:2] == ["books", "crm"]
    assert _codes(get("price_max=20")) == ["crm"]
    assert get("status=owned")["count"] == 0
    # A facet counts what it would show with the other filters applied.
    narrowed = get("q=book")
    assert _facet(narrowed, "statuses")["available"] == 1
    assert _facet(narrowed, "statuses")["coming_soon"] == 0


@pytest.mark.parametrize(
    "query",
    [
        "status=live",
        "plan_type=weekly",
        "sort=cheapest",
        "price_min=abc",
        "price_min=9&price_max=1",
    ],
)
def test_a_customer_store_refuses_a_filter_it_does_not_offer(shop, query):
    response = shop["client"].get(url(shop["slug"], f"store?{query}"))
    assert response.status_code == 400, response.content


def test_a_customer_store_is_its_own_organizations_only(shop, console_org, login):
    other = console_org()
    assert login(other["owner"]).get(url(shop["slug"], "store")).status_code in (403, 404)
    assert Client().get(url(shop["slug"], "store")).status_code in (401, 403, 404)


# --- affiliate --------------------------------------------------------------------------------


def test_an_affiliate_store_is_its_store_console_explore(console, login):
    actor = console["affiliate"]["actor"]
    explore = partner_workspaces.store_console(actor())
    page = portal_store.partner_store(actor(), kind="promote")
    assert sorted(_codes(page)) == sorted(p["code"] for p in explore["products"])
    assert page["kind"] == "promote" and page["can_request"] is True
    assert page["channels"] == explore["channels"]
    crm = next(p for p in page["products"] if p["code"] == "crm")
    assert crm["status"] == "not_requested"
    assert crm["commission"] == {"rate_bp": 1000, "recurring": False}
    assert crm["price"]["amount_minor"] == 1499

    ask(console)  # asking shows at once, and grants nothing
    client = sign_in(login, console["affiliate"])
    base = f"/api/v1/partner/{console['affiliate']['partner'].id}/affiliate/marketplace"
    pending = client.get(f"{base}?status=pending")
    assert pending.status_code == 200, pending.content
    assert _codes(pending.json()) == ["crm"]
    assert _facet(pending.json(), "statuses")["not_requested"] == 1
    assert client.get(f"{base}?status=live").status_code == 400


def test_a_partner_cannot_read_another_partners_store(console, partner_factory, login):
    other = partner_factory("referral.create", "referral.view")
    client = sign_in(login, other)
    theirs = client.get(
        f"/api/v1/partner/{console['affiliate']['partner'].id}/affiliate/marketplace"
    )
    assert theirs.status_code in (403, 404)
    assert Client().get(
        f"/api/v1/partner/{other['partner'].id}/affiliate/marketplace"
    ).status_code in (401, 403, 404)


# --- reseller and white-label -----------------------------------------------------------------


def test_a_reseller_store_lists_what_resellers_may_sell(console, partner_factory, login):
    from workeasy360.commercial.catalog.models import Product

    reseller = partner_factory(*CAPS)
    page = portal_store.partner_store(reseller["actor"](), kind="sell")
    assert sorted(_codes(page)) == ["books", "crm"]
    assert page["kind"] == "sell"
    assert all("margin" in p for p in page["products"])

    Product.objects.filter(code="books").update(reseller_enabled=False)
    client = sign_in(login, reseller)
    response = client.get(
        f"/api/v1/partner/{reseller['partner'].id}/reseller/marketplace"
    )
    assert response.status_code == 200, response.content
    assert _codes(response.json()) == ["crm"]


def test_a_white_label_store_follows_the_white_label_channel(console, partner_factory):
    from workeasy360.commercial.catalog.models import Product

    branded = partner_factory(*WHITE_LABEL)
    Product.objects.filter(code="books").update(
        reseller_enabled=True, white_label_enabled=False
    )
    Product.objects.filter(code="crm").update(
        reseller_enabled=False, white_label_enabled=True
    )
    page = portal_store.partner_store(branded["actor"](), kind="sell")
    assert _codes(page) == ["crm"]
    assert (
        _codes(portal_store.partner_store(branded["actor"](), kind="sell", q="books"))
        == []
    )


# --- the public Store (/store, 2026-11-02) ---------------------------------------------------

PUBLIC_STORE = "/api/v1/public/store"


@pytest.fixture
def public(shop):
    from django.core.cache import cache

    # The public catalog is cached per currency; start every test from the database.
    cache.clear()
    yield shop
    cache.clear()


def _public(query: str = "") -> dict:
    response = Client().get(f"{PUBLIC_STORE}?{query}")
    assert response.status_code == 200, response.content
    return response.json()


def test_the_public_store_is_the_public_catalog_as_a_marketplace(public):
    catalog = Client().get("/api/v1/public/catalog").json()
    page = _public()

    # Anonymous, and exactly the public catalog - in its order, its currency.
    assert _codes(page) == [p["code"] for p in catalog["products"]]
    assert page["currency"] == catalog["currency"] == "GBP"
    assert page["currencies"] == catalog["currencies"]
    crm = next(p for p in page["products"] if p["code"] == "crm")
    assert crm["status"] == "available"
    assert crm["price"]["amount_minor"] == 1499
    assert crm["price"]["interval"] == "monthly"
    # The plans ride along, so "Buy now" names a plan the checkout re-prices.
    assert {p["plan_code"] for p in crm["plans"]} >= {"crm_starter", "crm_professional"}
    assert {f["value"] for f in page["facets"]["statuses"]} == {
        "available",
        "coming_soon",
    }
    assert page["facets"]["price"] == {"min_minor": 1499, "max_minor": 2999}


def test_the_public_store_says_nothing_about_any_organization(public):
    page = _public()
    assert "billing_account" not in page
    for card in page["products"]:
        assert not {"trial_state", "holding", "trial_days_left"} & set(card)
        assert card["ribbon"] is None or card["ribbon"]["kind"] in {
            "coming_soon",
            "new",
            "trial",
        }


def test_the_public_store_filters_and_sorts_on_the_server(public):
    assert _codes(_public("q=book")) == ["books"]
    assert _codes(_public("sort=price_desc"))[:2] == ["books", "crm"]
    assert _codes(_public("price_max=20")) == ["crm"]
    assert _public("status=coming_soon")["count"] == 0
    narrowed = _public("q=book")
    assert _facet(narrowed, "statuses") == {"available": 1, "coming_soon": 0}


@pytest.mark.parametrize(
    "query",
    ["status=owned", "plan_type=weekly", "sort=cheapest", "price_min=abc"],
)
def test_the_public_store_refuses_a_filter_it_does_not_offer(public, query):
    response = Client().get(f"{PUBLIC_STORE}?{query}")
    assert response.status_code == 400, response.content


def test_a_product_off_the_customer_store_leaves_the_public_store(public):
    from workeasy360.commercial.catalog.models import Product

    Product.objects.filter(code="books").update(customer_store_enabled=False)
    from django.core.cache import cache

    cache.clear()
    assert "books" not in _codes(_public())


def test_a_public_product_page_has_its_full_listing_and_plans(public):
    response = Client().get(f"{PUBLIC_STORE}/products/crm")
    assert response.status_code == 200, response.content
    page = response.json()
    product = page["product"]
    assert (product["code"], product["status"]) == ("crm", "available")
    assert page["currency"] == "GBP"
    for key in ("description", "highlights", "benefits", "use_cases", "screenshots"):
        assert key in product
    assert product["price"]["amount_minor"] == 1499
    assert {p["plan_code"] for p in page["plans"]} >= {"crm_starter", "crm_professional"}
    assert page["comparison"][0]["key"] == "seats"
    # Nothing about any organization.
    assert "billing_account" not in page
    assert product["held_plan_code"] is None
    assert not {"trial_state", "holding"} & set(product)


def test_a_public_product_page_is_only_for_the_public_catalog(public):
    from django.core.cache import cache

    from workeasy360.commercial.catalog.models import Product

    assert Client().get(f"{PUBLIC_STORE}/products/no-such").status_code == 404
    Product.objects.filter(code="books").update(customer_store_enabled=False)
    cache.clear()
    assert Client().get(f"{PUBLIC_STORE}/products/books").status_code == 404


def test_what_the_admin_adds_to_a_product_shows_in_the_public_store_at_once(
    public, django_capture_on_commit_callbacks
):
    """Each admin step shows in ``/store`` on the next request - never a cache
    lifetime later: a new plan and its price (Buy now, Add to cart), a trial,
    then withdrawing the price. (Publishing a product already did.)"""
    from datetime import timedelta

    from django.utils import timezone

    from tests.extension.conftest import staff_scope
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.contracts.enums import BillingInterval, CatalogStatus

    staff = ("platform.staff_catalog.manage", "platform.staff_pricing.manage")

    def admin():
        # A test never commits; run the commit hooks (the cache refresh) as a
        # real request's commit would.
        return django_capture_on_commit_callbacks(execute=True)

    def crm_page() -> dict:
        response = Client().get(f"{PUBLIC_STORE}/products/crm")
        assert response.status_code == 200, response.content
        return response.json()

    # Both pages are now cached.
    assert "crm_enterprise" not in {p["plan_code"] for p in crm_page()["plans"]}
    _public()

    product = Product.objects.get(code="crm")
    with admin(), staff_scope(*staff) as ctx:
        plan = catalog_services.create_plan(
            ctx, product_id=product.id, code="crm_enterprise", name="Enterprise"
        )
        catalog_services.set_plan_status(
            ctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE
        )
        version = catalog_services.draft_plan_version(
            ctx,
            plan_id=plan.id,
            features={},
            included_seats=10,
            trial_days=0,
            effective_from=timezone.now() - timedelta(days=1),
        )
        catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
        price = pricing_services.publish_price(
            ctx,
            price_book_id=public["catalog"]["price_book"].id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=999,
            valid_from=timezone.now() - timedelta(minutes=1),
        )
    enterprise = next(
        p for p in crm_page()["plans"] if p["plan_code"] == "crm_enterprise"
    )
    assert enterprise["plan_version_id"] == str(version.id)
    crm = next(p for p in _public()["products"] if p["code"] == "crm")
    assert crm["price"]["amount_minor"] == 999  # now the cheapest plan

    with admin(), staff_scope(*staff) as ctx:
        catalog_services.configure_product_trial(
            ctx,
            product_id=product.id,
            enabled=True,
            duration_days=7,
            eligible_plan_codes=["crm_enterprise"],
        )
    crm = next(p for p in _public()["products"] if p["code"] == "crm")
    assert crm["trial"]["plan_codes"] == ["crm_enterprise"]
    assert "free_trial" in crm["plan_types"]

    with admin(), staff_scope(*staff) as ctx:
        pricing_services.close_price(ctx, price_id=price.id)
    crm = next(p for p in _public()["products"] if p["code"] == "crm")
    assert crm["price"]["amount_minor"] == 1499
