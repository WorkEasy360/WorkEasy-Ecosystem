"""What a white-label partner's own switches do on its customers' pages:
order and visibility only - never an entitlement, never a held product."""

from __future__ import annotations

import pytest

from workeasy360.commercial.partners import branding, product_settings, services
from workeasy360.commercial.partners import selectors as partner_selectors

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(console_org, login, partner_factory, catalog, configure_trial_fixture):
    fx = partner_factory("branding.manage", "domain.manage", "subscription.sell")
    for code in ("crm", "books"):
        services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code=code)
    branding.update_brand(
        fx["staff_ctx"], partner_id=fx["partner"].id, brand_name="Acme Cloud"
    )
    org = console_org()
    services.link_customer(
        fx["staff_ctx"], fx["partner"].id, org["org"].id, reason="White-label customer"
    )
    return {**org, "client": login(org["owner"]), "fx": fx}


@pytest.fixture
def configure_trial_fixture(catalog):
    from tests.partner_panel.conftest import configure_trial

    configure_trial(catalog)


def explore(shop):
    response = shop["client"].get(f"/api/v1/orgs/{shop['slug']}/explore")
    assert response.status_code == 200
    return [p["code"] for p in response.json()["products"]]


def test_the_partners_order_is_the_order_customers_see(shop):
    ctx = shop["fx"]["actor"]()
    product_settings.set_display_order(ctx, product_codes=["books", "crm"])
    codes = explore(shop)
    assert codes.index("books") < codes.index("crm")
    product_settings.set_display_order(ctx, product_codes=["crm", "books"])
    codes = explore(shop)
    assert codes.index("crm") < codes.index("books")


def test_a_product_off_for_sale_or_explore_is_not_offered_for_discovery(shop):
    ctx = shop["fx"]["actor"]()
    product_settings.update_product_setting(
        ctx, product_code="books", show_in_explore=False
    )
    assert "books" not in explore(shop) and "crm" in explore(shop)
    product_settings.update_product_setting(
        ctx, product_code="books", show_in_explore=True
    )
    product_settings.update_product_setting(
        ctx, product_code="books", enabled_for_sale=False
    )
    assert "books" not in explore(shop)


def test_a_direct_customer_and_a_non_white_label_partner_are_never_touched(
    console_org, login, partner_factory, catalog
):
    fx = partner_factory("subscription.sell")
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    org = console_org()
    services.link_customer(
        fx["staff_ctx"], fx["partner"].id, org["org"].id, reason="Plain reseller customer"
    )
    product_settings.update_product_setting(
        fx["actor"](), product_code="crm", show_in_explore=False, enabled_for_sale=False
    )
    assert partner_selectors.portal_presentation_for_organization(org["org"].id) == {}
    direct = console_org()
    assert partner_selectors.portal_presentation_for_organization(direct["org"].id) == {}
    assert "crm" in [
        p["code"]
        for p in login(org["owner"])
        .get(f"/api/v1/orgs/{org['slug']}/explore")
        .json()["products"]
    ]


def test_my_products_hides_only_what_the_customer_does_not_hold(shop):
    ctx = shop["fx"]["actor"]()
    product_settings.update_product_setting(
        ctx, product_code="books", show_in_my_products=False
    )
    apps = shop["client"].get(f"/api/v1/orgs/{shop['slug']}/apps").json()["apps"]
    assert "books" not in [a["product_code"] for a in apps]
    assert apps, "the rest of the directory is untouched"
