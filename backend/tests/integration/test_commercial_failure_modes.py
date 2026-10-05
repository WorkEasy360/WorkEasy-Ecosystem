"""Commercial failure paths: what happens when something breaks halfway.

``transaction=True`` throughout. These tests depend on real commits and, in
several cases, on the **absence** of an ambient database tenant variable -
which a non-transactional test silently provides, because everything shares
one outer transaction and a ``set_config(..., true)`` from any earlier setup
survives in it.

That difference is not incidental. It is what hid the defect in
``test_reconciliation_works_with_no_ambient_tenant_variable`` below.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection, transaction
from django.utils import timezone

from workeasy360.commercial.provisioning.services import compute_entitlements, reconcile
from workeasy360.commercial.subscriptions import services
from workeasy360.commercial.subscriptions.models import Subscription, SubscriptionItem
from workeasy360.contracts.errors import AuditWriteFailed, PriceNotAvailable
from workeasy360.integration.events.models import OutboxEvent
from workeasy360.integration.idempotency.models import IdempotencyRecord
from workeasy360.platform.entitlements.services import read as entitlements

pytestmark = [pytest.mark.django_db(transaction=True)]


def key() -> str:
    return uuid.uuid4().hex


def _counts(in_tenant, ctx) -> tuple[int, int, int]:
    """(items, subscriptions, commercial events) inside the tenant scope.

    Tenant tables are unreadable without a scope - that is the isolation
    working - so even an assertion has to enter one.
    """
    with in_tenant(ctx):
        return (
            SubscriptionItem.objects.count(),
            Subscription.objects.count(),
            OutboxEvent.objects.filter(
                organization_id=ctx.organization_id,
                event_type__startswith="commercial.",
            ).count(),
        )


def _tenant_variable() -> str | None:
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('app.organization_id', true)")
        value = cur.fetchone()[0]
    return value or None


@pytest.fixture
def purchased(catalog, customer, billing_account, in_tenant):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
    return {"ctx": ctx, "item": item}


# --- the cross-tenant read gap -------------------------------------------


def test_reconciliation_works_with_no_ambient_tenant_variable(purchased, in_tenant):
    """Regression for the most dangerous defect this phase found.

    ``unscoped_access()`` relaxes the ORM manager. It does **not** relax RLS,
    and a query with no tenant variable set matches **zero rows** rather than
    failing. So a reconciliation running from a Celery worker - where no
    variable is set - read no subscriptions, computed an empty desired state,
    and ``apply_entitlements`` dutifully expired everything the customer had.

    Every customer, on a schedule. The fix is that each cross-tenant selector
    enters ``organization_db_scope`` as well; this asserts the outcome rather
    than the mechanism, so it keeps working if the mechanism changes.
    """
    ctx = purchased["ctx"]
    assert _tenant_variable() is None, "the test itself is not scoped"

    computed = compute_entitlements(ctx.organization_id)
    assert [p.product_code for p in computed.products] == ["crm"], (
        "computed an empty state outside a tenant scope - entitlements would "
        "have been revoked for every organization the job visited"
    )

    reconcile(ctx.organization_id)

    with in_tenant(ctx):
        assert entitlements.check(ctx.organization_id, "crm").allowed
        assert entitlements.quota(ctx.organization_id, "crm.pipelines.max") == 25


def test_the_scheduled_pass_finds_work_without_a_tenant_variable(purchased):
    """The enumeration half of the same defect.

    A work list built from a tenant table would be empty outside a scope, and
    the job would report success having visited nobody.
    """
    from workeasy360.commercial.provisioning.tasks import reconcile_all

    assert _tenant_variable() is None
    result = reconcile_all()
    assert result["visited"] >= 1
    assert result["failed"] == 0


def test_the_commercial_clock_finds_work_without_a_tenant_variable(
    catalog, customer, billing_account, in_tenant
):
    from datetime import timedelta

    from workeasy360.commercial.subscriptions import system

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )

    assert _tenant_variable() is None
    counts = system.process_due_subscriptions(at=timezone.now() + timedelta(days=30))
    assert counts["trials_expired"] == 1
    assert counts["failed"] == 0


# --- transaction boundaries ----------------------------------------------


def test_a_failed_command_leaves_no_state_and_no_event(
    catalog, customer, billing_account, in_tenant
):
    """Half-applied commercial state is the failure this whole shape exists
    to prevent: the event describes work that never happened, or the work
    happens with nobody told."""
    ctx = customer["ctx"]
    billing_account(ctx)

    before = _counts(in_tenant, ctx)

    with in_tenant(ctx), pytest.raises(PriceNotAvailable):
        # No annual price exists for this plan, so resolution raises *after*
        # the subscription row and the idempotency claim have been written.
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            billing_interval=__import__(
                "workeasy360.contracts.enums", fromlist=["BillingInterval"]
            ).BillingInterval.ANNUAL,
        )

    assert _counts(in_tenant, ctx) == before
    assert before == (0, 0, 0)


def test_a_failed_command_does_not_burn_its_idempotency_key(
    catalog, customer, billing_account, in_tenant
):
    """The claim must roll back with the work it guards.

    Otherwise a transient failure permanently burns the key and the retry
    returns "already done" for work that never happened.
    """
    from workeasy360.contracts.enums import BillingInterval

    ctx = customer["ctx"]
    billing_account(ctx)
    command = key()

    with in_tenant(ctx), pytest.raises(PriceNotAvailable):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=command,
            billing_interval=BillingInterval.ANNUAL,
        )

    with in_tenant(ctx):
        assert not IdempotencyRecord.objects.filter(key=command).exists()

    # The retry genuinely retries.
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=command,
        )
    assert item is not None


def test_events_are_written_in_the_business_transaction(purchased):
    """Not before commit, not after. The outbox row and the state change
    share one transaction, so neither can exist without the other."""
    ctx = purchased["ctx"]
    # OutboxEvent is infrastructure rather than a tenant model, so it reads
    # without a scope; the events it holds are still tenant-tagged.
    rows = OutboxEvent.objects.filter(
        organization_id=ctx.organization_id,
        event_type="commercial.subscription.item_added",
    )
    assert rows.count() == 1
    assert rows.first().envelope["payload"]["subscription_item_id"] == str(
        purchased["item"].id
    )


def test_no_external_call_happens_inside_a_commercial_transaction(purchased):
    """Asserted structurally: no HTTP client is imported anywhere in
    ``commercial/``.

    A network call inside a transaction holds locks across a round trip, and
    the failure is invisible until the dependency is slow.
    """
    import ast
    import pathlib

    package = pathlib.Path(__file__).resolve().parents[2] / "workeasy360" / "commercial"
    banned = {"requests", "httpx", "urllib.request", "http.client", "socket"}
    offenders = []
    for path in package.rglob("*.py"):
        if "/tests/" in path.as_posix():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                offenders += [a.name for a in node.names if a.name in banned]
            elif isinstance(node, ast.ImportFrom) and node.module in banned:
                offenders.append(node.module)
    assert not offenders, offenders


# --- provisioning failures do not corrupt commerce ------------------------


def test_a_provisioning_failure_does_not_roll_back_the_purchase(
    catalog, customer, billing_account, in_tenant, monkeypatch
):
    """The customer has paid; the commercial record must stand.

    Entitlements are derived and rebuildable, so the right behaviour is to
    keep the durable commercial fact and let reconciliation repair access.
    Rolling back the purchase because a *derived* write failed would lose the
    money and the record.
    """
    from workeasy360.commercial.subscriptions import hooks

    def explode(organization_id):
        raise RuntimeError("entitlement store unavailable")

    saved_listeners = list(
        hooks._listeners
    )  # restored below: other listeners must survive
    hooks.reset_listeners()
    hooks.on_commercial_change("exploding", explode)

    ctx = customer["ctx"]
    billing_account(ctx)
    try:
        with in_tenant(ctx):
            item = services.purchase_plan(
                ctx,
                plan_version_id=catalog["versions"]["crm_professional"].id,
                idempotency_key=key(),
            )
            assert item is not None
            # Access is not granted yet - stale, not wrong.
            assert not entitlements.check(ctx.organization_id, "crm").allowed
    finally:
        hooks._listeners[:] = saved_listeners

    # Reconciliation repairs it, with no commercial state lost.
    reconcile(ctx.organization_id)
    with in_tenant(ctx):
        assert entitlements.check(ctx.organization_id, "crm").allowed
        assert SubscriptionItem.objects.count() == 1
    assert item.total_amount_minor == 4999


def test_provisioning_failure_is_counted(catalog, customer, billing_account, in_tenant):
    from workeasy360.commercial.subscriptions import hooks
    from workeasy360.platform.observability.metrics import metrics

    def explode(organization_id):
        raise RuntimeError("boom")

    saved_listeners = list(
        hooks._listeners
    )  # restored below: other listeners must survive
    hooks.reset_listeners()
    hooks.on_commercial_change("exploding", explode)
    ctx = customer["ctx"]
    billing_account(ctx)
    try:
        with in_tenant(ctx):
            services.purchase_plan(
                ctx,
                plan_version_id=catalog["versions"]["crm_starter"].id,
                idempotency_key=key(),
            )
        assert metrics.value(hooks.PROVISIONING_FAILURES, listener="exploding") == 1
    finally:
        hooks._listeners[:] = saved_listeners


def test_an_interrupted_bundle_expansion_leaves_nothing(
    catalog, customer, billing_account, in_tenant, monkeypatch
):
    """A bundle is never half provisioned.

    The customer either holds every component or none; a partial expansion
    would bill for a suite and deliver part of it.
    """
    ctx = customer["ctx"]
    billing_account(ctx)

    original = services._create_plan_item
    calls = {"n": 0}

    def fail_on_second(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("interrupted mid-expansion")
        return original(**kwargs)

    monkeypatch.setattr(services, "_create_plan_item", fail_on_second)

    with in_tenant(ctx), pytest.raises(RuntimeError):
        services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=key()
        )

    assert _counts(in_tenant, ctx) == (0, 0, 0)


def test_an_audit_failure_fails_the_operation(
    catalog, customer, billing_account, in_tenant, monkeypatch
):
    """The one place a telemetry failure is allowed to fail the request.

    An unaudited change to what a customer is paying for is worse than a
    failed one.
    """
    from workeasy360.platform.audit import services as audit

    def explode(*args, **kwargs):
        raise AuditWriteFailed("audit unavailable")

    ctx = customer["ctx"]
    billing_account(ctx)
    monkeypatch.setattr(audit, "record", explode)

    with in_tenant(ctx), pytest.raises(AuditWriteFailed):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    assert _counts(in_tenant, ctx) == (0, 0, 0)


# --- redelivery and replay ------------------------------------------------


def in_tenant_scope(ctx):
    """The ``in_tenant`` fixture as a plain callable, for tests that do not
    take it."""
    from contextlib import contextmanager

    from django.db import transaction as _transaction

    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import set_tenant_variable

    @contextmanager
    def enter(bound):
        with tenant_context(bound), _transaction.atomic():
            set_tenant_variable(bound.organization_id)
            yield bound

    return enter


def test_replaying_the_relay_does_not_change_commercial_state(purchased):
    """The outbox delivers at least once. Redelivery must be inert."""
    from workeasy360.integration.events.tasks import relay_outbox

    ctx = purchased["ctx"]
    before = _counts(in_tenant_scope(ctx), ctx)
    relay_outbox()
    relay_outbox()
    assert _counts(in_tenant_scope(ctx), ctx) == before


def test_reconciling_after_every_kind_of_change_converges(
    catalog, customer, billing_account, in_tenant
):
    """Whatever order things happened in, one pass lands on the same answer.

    This is the property that makes duplicated and out-of-order events
    non-corrupting: the answer is a function of durable state, not of the
    sequence of notifications that announced it.
    """
    from workeasy360.platform.entitlements.selectors import stored_snapshot

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )

    reconcile(ctx.organization_id)
    first = stored_snapshot(ctx.organization_id)
    for _ in range(3):
        reconcile(ctx.organization_id)
    last = stored_snapshot(ctx.organization_id)

    assert first is not None and last is not None
    assert first.products == last.products
    assert first.features == last.features


def test_a_database_rollback_discards_the_whole_command(
    catalog, customer, billing_account, in_tenant
):
    """An explicit rollback around a command leaves nothing behind."""
    ctx = customer["ctx"]
    billing_account(ctx)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        with in_tenant(ctx), transaction.atomic():
            services.purchase_plan(
                ctx,
                plan_version_id=catalog["versions"]["crm_starter"].id,
                idempotency_key=key(),
            )
            raise Rollback

    assert _counts(in_tenant, ctx) == (0, 0, 0)
    with in_tenant(ctx):
        assert IdempotencyRecord.objects.count() == 0


# --- scope leakage between organizations ---------------------------------


def test_reading_another_organization_does_not_rescope_the_caller(
    catalog,
    customer,
    billing_account,
    in_tenant,
    organization_factory,
    tenant_ctx_factory,
):
    """A request that reads one tenant must not end up reading another.

    The cross-tenant selectors open a database scope of their own, and
    ``set_config(..., true)`` survives the release of a savepoint. Called from
    inside another tenant's transaction they therefore used to leave *their*
    organization bound to every statement that followed - with the caller's
    ORM filter still naming the caller's tenant, so the two disagreed and the
    database won.

    Reachable through the listener registry, which swallows what a listener
    raises: a provisioning run for a foreign organization fails at the write
    chokepoint (below), the failure is logged and discarded, and the request
    carries on reading the wrong tenant. Phase 2 has no caller that reconciles
    a foreign organization, which is the only reason this was latent; Phase
    3's Super Admin and payment webhooks are exactly that shape.
    """
    from workeasy360.commercial.provisioning.services import compute_entitlements
    from workeasy360.platform.tenancy.db import current_tenant_variable

    caller = customer["ctx"]
    billing_account(caller)
    with in_tenant(caller):
        services.purchase_plan(
            caller,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )

    other_org, other_membership, other_user = organization_factory()
    other = tenant_ctx_factory(other_org, other_membership, other_user)

    with in_tenant(caller):
        assert current_tenant_variable() == str(caller.organization_id)
        compute_entitlements(other.organization_id)
        assert current_tenant_variable() == str(caller.organization_id), (
            "reading another organization left it bound to this request"
        )
        # The outcome, not the mechanism: the caller still reads its own rows.
        assert [s.organization_id for s in Subscription.objects.all()] == [
            caller.organization_id
        ]


def test_provisioning_a_foreign_organization_is_refused_not_misapplied(
    customer, in_tenant, organization_factory, tenant_ctx_factory
):
    """Layer 2 refuses the write, rather than writing it under the wrong scope.

    Recorded because it is the reason the leak above stayed latent: a
    reconciliation for a foreign organization never completes while another
    tenant's context is active, so no entitlement row was ever written to the
    wrong tenant. The refusal is the persistence chokepoint doing its job and
    is worth pinning - if it ever softened, the leak would become a write.
    """
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.contracts.errors import TenantContextMismatch

    caller = customer["ctx"]
    other_org, other_membership, other_user = organization_factory()
    other = tenant_ctx_factory(other_org, other_membership, other_user)

    with in_tenant(caller), pytest.raises(TenantContextMismatch):
        reconcile(other.organization_id)


def test_a_cross_tenant_selector_verifies_its_own_scope(purchased):
    """The selectors assert the scope they opened actually took effect.

    A guard against the refactor, not against today's code: the emptiness of
    this read is what makes provisioning revoke, so "the scope was
    established" is asserted rather than assumed.
    """
    from workeasy360.commercial.subscriptions.selectors import commercial_state

    ctx = purchased["ctx"]
    assert _tenant_variable() is None
    state = commercial_state(ctx.organization_id)
    assert len(state) == 1
    # And the scope it opened is gone again afterwards.
    assert _tenant_variable() is None
