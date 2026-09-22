"""Query-count assertions for the commercial hot paths.

Query counts are **pinned, not observed**. An N+1 is invisible in code
review, does not fail any functional test, and only appears when a customer
has enough rows for it to matter - by which point it is in production. A
pinned count fails on the pull request that introduces one.

The counts here are upper bounds with a little headroom, so an unrelated
change does not fail the suite spuriously. They are not aspirational: each
one was measured, and the comment says what the queries are.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.provisioning.services import compute_entitlements, reconcile
from workeasy360.commercial.subscriptions import selectors as subscription_selectors
from workeasy360.commercial.subscriptions import services
from workeasy360.commercial.subscriptions.models import Subscription

pytestmark = pytest.mark.django_db


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def full_customer(catalog, customer, billing_account, in_tenant):
    """A customer holding a bundle: three lines, two products."""
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=key()
        )
        subscription = Subscription.objects.get()
    return {"ctx": ctx, "subscription": subscription}


def _count(callable_):
    with CaptureQueriesContext(connection) as captured:
        result = callable_()
    return result, len(captured)


# --- catalog reads --------------------------------------------------------


def test_plan_version_lookup_is_bounded(catalog, platform_admin, in_tenant):
    """One query for the version, one for its features.

    Without the prefetch this is one query per feature, inside the
    computation that every entitlement decision depends on.
    """
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        dto, queries = _count(
            lambda: catalog_selectors.plan_version(
                catalog["versions"]["crm_professional"].id
            )
        )
    assert dto is not None
    assert queries <= 2, f"{queries} queries for one plan version"


def test_batch_plan_version_lookup_does_not_scale_with_count(
    catalog, platform_admin, in_tenant
):
    """Three plan versions cost the same as one.

    This is the read provisioning makes per reconciliation, so a per-id
    lookup here is an N+1 in the least-watched place in the system.
    """
    ctx = platform_admin["ctx"]
    ids = [version.id for version in catalog["versions"].values()]
    with in_tenant(ctx):
        _, one = _count(lambda: catalog_selectors.plan_versions(ids[:1]))
        result, three = _count(lambda: catalog_selectors.plan_versions(ids))

    assert len(result) == 3
    assert three == one, f"{one} query/queries for one, {three} for three"


def test_bundle_resolution_is_bounded(catalog, platform_admin, in_tenant):
    """One query for the version, one for its items with their plans."""
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        dto, queries = _count(
            lambda: catalog_selectors.bundle_version(catalog["bundle_version"].id)
        )
    assert dto is not None and len(dto.items) == 2
    assert queries <= 2, f"{queries} queries to resolve a bundle"


# --- subscription reads ---------------------------------------------------


def test_subscription_detail_is_bounded(full_customer, in_tenant):
    """One query for the subscription, one for its lines."""
    ctx = full_customer["ctx"]
    with in_tenant(ctx):
        dto, queries = _count(
            lambda: subscription_selectors.subscription_detail(
                full_customer["subscription"].id
            )
        )
    assert dto is not None and len(dto.items) == 3
    assert queries <= 2, f"{queries} queries for a subscription detail"


def test_subscription_list_does_not_scale_with_item_count(
    catalog, customer, billing_account, in_tenant
):
    """Adding lines must not add queries.

    The failure this pins is the one that looks fine with two items and
    collapses with twenty.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        _, with_one = _count(subscription_selectors.subscriptions_for_organization)

        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["books_professional"].id,
            idempotency_key=key(),
        )
        result, with_two = _count(subscription_selectors.subscriptions_for_organization)

    assert len(result[0].items) == 2
    assert with_two == with_one, f"{with_one} -> {with_two} queries for one more line"


def test_commercial_state_read_is_bounded(full_customer):
    """The provisioning input, in a fixed number of round trips.

    Six statements: SAVEPOINT, bind, the subscription query, the item
    prefetch, restore, RELEASE. Only two of them are data queries; the other
    four are the price of the selector guaranteeing its own tenant scope
    rather than trusting a caller to have entered one, and then putting back
    the scope it found.

    Both halves earn their round trip. The bind is what stops a cross-tenant
    read returning silently empty. The restore is what stops this selector
    leaving *its* organization bound to the caller's transaction afterwards -
    ``set_config(..., true)`` survives the release of a savepoint, so without
    it a caller that reconciled one organization and then read another would
    have read the first one's rows and never known.
    """
    ctx = full_customer["ctx"]
    state, queries = _count(
        lambda: subscription_selectors.commercial_state(ctx.organization_id)
    )
    assert len(state) == 1 and len(state[0].items) == 3
    assert queries <= 6, f"{queries} statements to read commercial state"


# --- provisioning ---------------------------------------------------------


def test_entitlement_computation_is_bounded(full_customer):
    """A bundle of two products costs the same as one.

    ``compute_entitlements`` reads commercial state, batches the catalog
    lookup and reads grants. A per-item catalog lookup would make this scale
    with the customer's line count.
    """
    ctx = full_customer["ctx"]
    computed, queries = _count(lambda: compute_entitlements(ctx.organization_id))
    assert len(computed.products) == 2
    # Three scoped reads (commercial state, plan versions, support grants),
    # each carrying its own SAVEPOINT / bind / restore / RELEASE. Pinned so
    # the count cannot grow quietly; the growth test below is the one that
    # guards against an N+1.
    assert queries <= 13, f"{queries} statements to compute entitlements"


def test_entitlement_computation_does_not_scale_with_line_count(
    catalog, customer, billing_account, in_tenant
):
    """One product or two, the same number of queries.

    The assertion that actually matters: the catalog lookup is batched, so a
    customer on a five-product suite costs no more round trips than one on a
    single plan.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
    _, with_one = _count(lambda: compute_entitlements(ctx.organization_id))

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["books_professional"].id,
            idempotency_key=key(),
        )
    computed, with_two = _count(lambda: compute_entitlements(ctx.organization_id))

    assert len(computed.products) == 2
    assert with_two == with_one, (
        f"{with_one} statements for one product, {with_two} for two"
    )


def test_entitlement_provisioning_query_count_is_bounded(full_customer):
    """A full reconciliation, end to end.

    Pinned because this runs for every organization on a schedule: a single
    extra query here is multiplied by the tenant count on every pass.
    """
    ctx = full_customer["ctx"]
    _, queries = _count(lambda: reconcile(ctx.organization_id))
    assert queries <= 40, f"{queries} queries for one reconciliation"


def test_reconciliation_does_not_scale_with_line_count(
    catalog, customer, billing_account, in_tenant
):
    """Two lines must not cost meaningfully more than one.

    Compares the growth rather than the absolute count, so the assertion
    survives an unrelated change to the fixed cost.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
    _, with_one = _count(lambda: reconcile(ctx.organization_id))

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["books_professional"].id,
            idempotency_key=key(),
        )
    _, with_two = _count(lambda: reconcile(ctx.organization_id))

    # Each additional product adds its own entitlement, feature and seat
    # rows; what must not happen is a per-item *lookup*.
    growth = with_two - with_one
    assert growth <= 12, f"one extra line cost {growth} extra queries"


# --- entitlement reads (the product-facing path) --------------------------


def test_repeat_entitlement_checks_issue_no_further_queries(full_customer, in_tenant):
    """The Phase 1 budget is per request, and Phase 2 must not break it.

    The first check in a request may touch the database; every subsequent
    check is a dictionary lookup.
    """
    from workeasy360.platform.entitlements.services import read as entitlements
    from workeasy360.platform.entitlements.services.read import (
        entitlement_request_scope,
    )

    ctx = full_customer["ctx"]
    with in_tenant(ctx), entitlement_request_scope():
        entitlements.check(ctx.organization_id, "crm")

        with CaptureQueriesContext(connection) as captured:
            for _ in range(25):
                entitlements.check(ctx.organization_id, "crm")
                entitlements.check(ctx.organization_id, "books")
                entitlements.quota(ctx.organization_id, "crm.pipelines.max")
        assert len(captured) == 0, f"{len(captured)} queries for 75 repeat checks"


def test_price_resolution_is_bounded(catalog, customer, in_tenant):
    """Resolution is on the purchase path and on every renewal quote."""
    from django.utils import timezone

    from workeasy360.commercial.pricing.resolution import resolve_price
    from workeasy360.contracts.enums import BillingInterval
    from workeasy360.contracts.values.money import CurrencyCode

    ctx = customer["ctx"]
    with in_tenant(ctx):
        quote, queries = _count(
            lambda: resolve_price(
                plan_version_id=catalog["versions"]["crm_professional"].id,
                organization_id=ctx.organization_id,
                currency=CurrencyCode.GBP,
                interval=BillingInterval.MONTHLY,
                at=timezone.now(),
            )
        )
    assert quote.total.minor_units == 4999
    # Contract price, then the price book join, then the tier prefetch.
    assert queries <= 4, f"{queries} queries to resolve one price"


# --- indexes --------------------------------------------------------------


def test_every_commercial_index_has_a_query_it_serves():
    """No speculative indexes.

    Each one costs write throughput and storage forever. This asserts the
    weaker but checkable property that every index on a tenant commercial
    table leads with ``organization``, which is what makes the RLS predicate
    index-served rather than a scan.
    """
    from workeasy360.commercial.billing.models import BillingAccount
    from workeasy360.commercial.pricing.models import ContractPrice
    from workeasy360.commercial.subscriptions.models import Subscription, SubscriptionItem
    from workeasy360.integration.idempotency.models import IdempotencyRecord

    tenant_models = (
        BillingAccount,
        ContractPrice,
        Subscription,
        SubscriptionItem,
        IdempotencyRecord,
    )
    offenders = []
    for model in tenant_models:
        for index in model._meta.indexes:
            if index.fields and index.fields[0].lstrip("-") not in {
                "organization",
                "organization_id",
            }:
                offenders.append(f"{model.__name__}.{index.name}")
    assert not offenders, f"Indexes not leading with organization: {offenders}"
