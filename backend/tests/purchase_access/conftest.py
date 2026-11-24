"""Fixtures for ADR-073: from a verified purchase to using the product.

The earlier suites' fixtures, unchanged: a console organization whose owner signs
in through the real login endpoint, the shared catalog (CRM per seat, Books per
organization, the Sales Suite bundle), the development payment provider - plus
**Desk**, a second per-seat product, so "a second product bought later" is a
per-seat one too, and a registered integration (with a back-channel logout URI)
for each product a test opens.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.extension import conftest as _extension
from tests.extension.conftest import (  # noqa: F401 - helpers, not fixtures
    PASSWORD,
    pay_order,
    place_and_pay,
    place_order,
    url,
)
from tests.factories.manifests import manifest_for

console_org = _extension.console_org
fake_payments = _extension.fake_payments
login = _extension.login
staff_login = _extension.staff_login

#: Each product on its own origin, as in production (ADR-070).
ORIGINS = {
    "crm": "http://localhost:9101",
    "books": "http://localhost:9102",
    "desk": "http://localhost:9103",
}


def post(client, path, body=None, *, key=True, **extra):
    headers = {"HTTP_IDEMPOTENCY_KEY": uuid.uuid4().hex} if key else {}
    return client.post(
        path, json.dumps(body or {}), content_type="application/json", **headers, **extra
    )


@pytest.fixture
def desk(catalog, platform_admin, in_tenant):
    """A second per-seat product, listed and priced in the catalog's GBP book."""
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.contracts.enums import (
        BillingInterval,
        CatalogStatus,
        FeatureKind,
        LicensingMode,
        ProductStatus,
    )

    ctx = platform_admin["ctx"]
    established = timezone.now() - timedelta(days=30)
    with in_tenant(ctx):
        product = catalog_services.create_product(
            ctx, code="desk", name="Desk", licensing_mode=LicensingMode.PER_SEAT
        )
        catalog_services.set_product_status(
            ctx, product_id=product.id, status=ProductStatus.AVAILABLE
        )
        catalog_services.create_feature(
            ctx,
            product_id=product.id,
            key="desk.core.enabled",
            name="Core",
            kind=FeatureKind.BOOLEAN,
            default_value=False,
        )
        plan = catalog_services.create_plan(
            ctx, product_id=product.id, code="desk_team", name="Desk Team"
        )
        catalog_services.set_plan_status(
            ctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE
        )
        version = catalog_services.draft_plan_version(
            ctx,
            plan_id=plan.id,
            features={"desk.core.enabled": True},
            included_seats=3,
            effective_from=established,
        )
        catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
        pricing_services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=1999,
            valid_from=established,
        )
    catalog["versions"]["desk_team"] = version
    catalog["products"]["desk"] = product
    return version


@pytest.fixture
def products(register_product, desk):
    """CRM, Books and Desk registered and active, each taking back-channel logout."""

    def manifest(code: str) -> dict:
        return manifest_for(
            code,
            origin=ORIGINS[code],
            manifest_version="2026-11-15",
            sign_in_mode="sso_only",
            backchannel_logout_uri=f"{ORIGINS[code]}/auth/backchannel-logout",
        )

    return {
        code: register_product(code, origin=ORIGINS[code], manifest=manifest(code))
        for code in ("crm", "books", "desk")
    }


@pytest.fixture
def shop(console_org, login, catalog, fake_payments, products):
    """An organization with a billing account (GBP) and its owner signed in."""
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "billing_email": "billing@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    return {
        **org,
        "client": owner,
        "catalog": catalog,
        "id": org["org"].id,
        "products": products,
    }


def version_of(shop, plan: str) -> str:
    return str(shop["catalog"]["versions"][plan].id)


def buy(shop, plan: str, client=None) -> dict:
    """Place and pay (signed webhook path) an order for one plan."""
    client = client or shop["client"]
    placed = place_order(client, shop["slug"], plan_version_id=version_of(shop, plan))
    assert placed.status_code == 201, placed.content
    order = pay(client, shop["slug"], shop["id"], placed.json())
    assert order["status"] == "fulfilled", order
    return order


def pay(client, slug, organization_id, order) -> dict:
    """``pay_order``, also where commits are real (``transaction=True``): there the
    queued checkout task has already run at commit, so the hosted page exists."""
    from workeasy360.commercial.payments.checkout import create_checkout
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    started = client.post(
        url(slug, f"billing/invoices/{order['invoice_id']}/pay"),
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert started.status_code == 202, started.content
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    assert create_checkout(organization_id, attempt_id) in (
        "awaiting_payment",
        "not_pending",
    )
    with organization_scope(organization_id, reason="Test reads the checkout reference"):
        reference = PaymentAttempt.unscoped.get(id=attempt_id).provider_reference
    done = client.post(
        "/api/v1/public/payments/test-checkout/complete",
        {
            "reference": reference,
            "organization_id": str(organization_id),
            "outcome": "succeeded",
        },
        content_type="application/json",
    )
    assert done.status_code == 200, done.content
    return client.get(url(slug, f"orders/{order['order_id']}")).json()


def in_scope(organization_id):
    """Both isolation layers for a test's own reads of one organization."""
    from contextlib import ExitStack

    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    stack = ExitStack()
    stack.enter_context(tenant_context(routing_context(organization_id)))
    stack.enter_context(unscoped_access(reason="Test reads tenant rows", module="tests"))
    stack.enter_context(organization_db_scope(organization_id))
    return stack


def seat_holders(organization_id, pool_key: str) -> set:
    from workeasy360.platform.entitlements.models import SeatAssignment

    with in_scope(organization_id):
        return set(
            SeatAssignment.unscoped.filter(
                organization_id=organization_id,
                seat_grant__pool_key=pool_key,
                released_at__isnull=True,
            ).values_list("membership_id", flat=True)
        )


def subscription_items(organization_id) -> list:
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    with in_scope(organization_id):
        return list(
            SubscriptionItem.unscoped.filter(organization_id=organization_id).order_by(
                "added_at", "id"
            )
        )


def launch(shop, code: str, client=None):
    return (client or shop["client"]).post(
        url(shop["slug"], f"apps/{code}/launch"), HTTP_ACCEPT="application/json"
    )


def audit_actions(organization_id, action: str) -> list:
    from workeasy360.platform.audit.models import AuditEvent

    with in_scope(organization_id):
        return list(
            AuditEvent.unscoped.filter(organization_id=organization_id, action=action)
        )
