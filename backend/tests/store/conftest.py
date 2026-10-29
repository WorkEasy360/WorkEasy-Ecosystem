"""Store fixtures (ADR-052): an organization ready to buy, and a third product
so a cart can join a subscription that is already running."""

from __future__ import annotations

import pytest

from tests.extension import conftest as _extension
from tests.extension.conftest import staff_scope, url

console_org = _extension.console_org
fake_payments = _extension.fake_payments
login = _extension.login
staff_login = _extension.staff_login

CATALOG_STAFF = ("platform.staff_catalog.manage", "platform.staff_pricing.manage")


@pytest.fixture
def shop(console_org, login, catalog, fake_payments):
    """An organization with a GBP billing account and its owner signed in."""
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "billing_email": "billing@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    return {**org, "client": owner, "catalog": catalog, "id": org["org"].id}


@pytest.fixture
def third_product(catalog):
    """``notes`` with one public plan, ``notes_team``, at 999 GBP a month."""
    from django.utils import timezone

    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.contracts.enums import BillingInterval, CatalogStatus, ProductStatus

    with staff_scope(*CATALOG_STAFF) as ctx:
        product = catalog_services.create_product(ctx, code="notes", name="Notes")
        catalog_services.set_product_status(
            ctx, product_id=product.id, status=ProductStatus.AVAILABLE
        )
        plan = catalog_services.create_plan(
            ctx, product_id=product.id, code="notes_team", name="Notes Team"
        )
        catalog_services.set_plan_status(
            ctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE
        )
        version = catalog_services.draft_plan_version(
            ctx,
            plan_id=plan.id,
            features={},
            included_seats=3,
            trial_days=0,
            effective_from=timezone.now() - timezone.timedelta(days=1),
        )
        catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
        pricing_services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=999,
        )
    return version
