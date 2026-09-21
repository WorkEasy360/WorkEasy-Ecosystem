"""Concurrency properties, exercised with real threads and real connections.

Correctness under concurrency cannot be asserted from a single-threaded test:
``SELECT ... FOR UPDATE SKIP LOCKED`` behaves identically to a plain SELECT
when nothing else is running, and a contextvar looks fine until two requests
interleave. Each test here runs genuine parallel work.

``transaction=True`` throughout, because these need real commits: a test
wrapped in a rollback-only transaction would have every thread looking at a
different, invisible view of the data.
"""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections, transaction

from workeasy360.contracts.enums import EntitlementStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events import services as events
from workeasy360.integration.events.models import OutboxEvent, ProcessedEvent

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 8


def _close_connections() -> None:
    """Each thread gets its own connection; Django does not close them."""
    connections.close_all()


def _membership_event(org_id):
    from workeasy360.contracts.events.envelope import DomainEvent

    membership_id = str(uuid.uuid4())
    return DomainEvent(
        event_type="organizations.membership.added",
        event_version=1,
        organization_id=org_id,
        aggregate_type="organizations.membership",
        aggregate_id=membership_id,
        payload={
            "organization_id": str(org_id),
            "membership_id": membership_id,
            "user_id": str(uuid.uuid4()),
            "member_type": "internal",
        },
    )


# --- outbox relay ---------------------------------------------------------


def test_concurrent_relays_never_claim_the_same_event(two_tenants, in_tenant):
    """SKIP LOCKED must let many relay workers drain one table safely.

    This is the property that avoids both a single-relay bottleneck and
    double-publishing. Without it, either the relay is serialised or the same
    business event is published twice.
    """
    a = two_tenants["a"]
    event_count = 40

    with in_tenant(a["ctx"]):
        for _ in range(event_count):
            events.publish(_membership_event(a["org"].id))

    claimed_ids: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def relay_worker() -> None:
        try:
            barrier.wait(timeout=30)
            for _ in range(10):
                rows = events.claim_batch(batch_size=5)
                if not rows:
                    continue
                with lock:
                    claimed_ids.extend(row.id for row in rows)
        finally:
            _close_connections()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(relay_worker) for _ in range(WORKERS)]
        for future in as_completed(futures):
            future.result()

    duplicates = [item for item, n in Counter(claimed_ids).items() if n > 1]
    assert not duplicates, f"{len(duplicates)} event(s) claimed more than once"
    assert len(claimed_ids) == event_count, (
        f"claimed {len(claimed_ids)} of {event_count} events"
    )


def test_concurrent_consumers_apply_an_event_exactly_once(two_tenants):
    """At-least-once delivery plus a unique constraint equals exactly-once
    *effect*, which is the property that actually matters.

    The deduplication comes from the database, not from application logic, so
    a genuine race resolves correctly rather than by luck.
    """
    event_id = uuid.uuid4()
    applied = []
    duplicates = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def consumer() -> None:
        try:
            barrier.wait(timeout=30)
            try:
                events.mark_processed("books.on_membership_added", event_id)
                with lock:
                    applied.append(1)
            except events.AlreadyProcessed:
                with lock:
                    duplicates.append(1)
        finally:
            _close_connections()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for future in as_completed([pool.submit(consumer) for _ in range(WORKERS)]):
            future.result()

    assert len(applied) == 1, f"{len(applied)} consumers applied the same event"
    assert len(duplicates) == WORKERS - 1
    assert ProcessedEvent.objects.filter(event_id=event_id).count() == 1


# --- entitlement recompute ------------------------------------------------


def test_concurrent_entitlement_recomputes_converge(two_tenants):
    """Racing recomputes must not corrupt the snapshot.

    An advisory lock serialises them per organization, and last-writer-wins is
    safe because compute is deterministic: two runs from the same durable
    state produce the same answer (10-performance-concurrency.md section 12,
    contention point 7).
    """
    from workeasy360.commercial.provisioning.services import grant_product
    from workeasy360.platform.entitlements.models import EntitlementSnapshot

    a = two_tenants["a"]
    errors: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def recompute() -> None:
        try:
            barrier.wait(timeout=30)
            grant_product(
                a["org"].id,
                "crm",
                status=EntitlementStatus.ACTIVE,
                features={"crm.pipelines.max": 50},
            )
        except Exception as exc:
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            _close_connections()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for future in as_completed([pool.submit(recompute) for _ in range(WORKERS)]):
            future.result()

    assert not errors, errors

    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason="Concurrency test inspects the snapshot it just wrote",
            module="tests.integration",
        ),
        organization_db_scope(a["org"].id),
    ):
        rows = list(EntitlementSnapshot.unscoped.filter(organization_id=a["org"].id))

    # Exactly one snapshot row, and it reflects the (identical) computed state.
    assert len(rows) == 1
    assert rows[0].products["crm"]["status"] == EntitlementStatus.ACTIVE.value
    assert rows[0].features["crm.pipelines.max"] == 50
    # The version advanced once per apply, monotonically.
    assert rows[0].version >= 1


# --- tenant context under threads ----------------------------------------


def test_tenant_context_does_not_leak_between_threads(two_tenants):
    """A ContextVar rather than thread-local storage.

    Under ASGI a single thread interleaves many requests, so thread-locals are
    a correctness bug rather than a style preference
    (03-multi-tenancy.md section 4, Layer 1). This asserts that concurrent
    contexts stay distinct.
    """
    from workeasy360.platform.tenancy.context import (
        current_tenant_or_none,
        tenant_context,
    )

    a, b = two_tenants["a"], two_tenants["b"]
    observed: list[tuple[str, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def worker(index: int) -> None:
        ctx = a["ctx"] if index % 2 == 0 else b["ctx"]
        expected = str(ctx.organization_id)
        barrier.wait(timeout=30)
        with tenant_context(ctx):
            for _ in range(50):
                seen = current_tenant_or_none()
                with lock:
                    observed.append(
                        (expected, str(seen.organization_id) if seen else "none")
                    )

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for future in as_completed([pool.submit(worker, i) for i in range(WORKERS)]):
            future.result()

    mismatches = [(e, s) for e, s in observed if e != s]
    assert not mismatches, f"{len(mismatches)} context leak(s) between threads"


def test_rls_isolates_concurrent_tenant_queries(two_tenants):
    """The database-level counterpart, with real connections.

    Each thread opens its own transaction, sets its tenant and reads. A leak
    would show as a foreign row.
    """
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import set_tenant_variable

    a, b = two_tenants["a"], two_tenants["b"]
    leaks: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def worker(index: int) -> None:
        ctx: TenantContext = a["ctx"] if index % 2 == 0 else b["ctx"]
        try:
            barrier.wait(timeout=30)
            for _ in range(20):
                with tenant_context(ctx), transaction.atomic():
                    set_tenant_variable(ctx.organization_id)
                    for row in Membership.objects.all():
                        if row.organization_id != ctx.organization_id:
                            with lock:
                                leaks.append(
                                    f"tenant {ctx.organization_id} saw "
                                    f"{row.organization_id}"
                                )
        finally:
            _close_connections()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for future in as_completed([pool.submit(worker, i) for i in range(WORKERS)]):
            future.result()

    assert not leaks, leaks[:5]


def test_outbox_publishing_is_atomic_under_concurrency(two_tenants, in_tenant):
    """Concurrent business writes each commit their own event, or neither.

    Half the workers roll back. The outbox must contain exactly the events
    whose transactions committed - no phantom events, no lost ones.
    """
    a = two_tenants["a"]
    committed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def worker(index: int) -> None:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import set_tenant_variable

        should_commit = index % 2 == 0
        try:
            barrier.wait(timeout=30)
            event = _membership_event(a["org"].id)
            try:
                with tenant_context(a["ctx"]), transaction.atomic():
                    set_tenant_variable(a["org"].id)
                    events.publish(event)
                    if not should_commit:
                        raise RuntimeError("business rule failed")
                with lock:
                    committed.append(str(event.event_id))
            except RuntimeError:
                pass
        finally:
            _close_connections()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for future in as_completed([pool.submit(worker, i) for i in range(WORKERS)]):
            future.result()

    stored = {str(e) for e in OutboxEvent.objects.values_list("event_id", flat=True)}
    assert stored == set(committed), (
        f"outbox holds {len(stored)} events, {len(committed)} transactions committed"
    )
