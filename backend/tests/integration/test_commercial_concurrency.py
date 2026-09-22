"""Commercial concurrency, with real threads, real connections, real commits.

Nothing here is mocked. ``SELECT ... FOR UPDATE`` behaves exactly like a
plain ``SELECT`` when nothing else is running, and a uniqueness constraint
never fires in a single-threaded test - so a mocked "concurrency" test proves
that the code runs, not that the invariant holds.

``transaction=True`` throughout, because these need genuine commits: a test
wrapped in a rollback-only transaction would have every thread looking at a
different, invisible view of the data.
"""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections

from workeasy360.commercial.provisioning.services import reconcile
from workeasy360.commercial.subscriptions import services
from workeasy360.commercial.subscriptions.models import Subscription, SubscriptionItem
from workeasy360.contracts.errors import IdempotencyConflict, SeatLimitReached
from workeasy360.platform.entitlements.models import SeatAssignment
from workeasy360.platform.entitlements.services import seats

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 8


def _close() -> None:
    """Each thread gets its own connection; Django does not close them."""
    connections.close_all()


def key() -> str:
    return uuid.uuid4().hex


def _run(worker, count=WORKERS):
    """Fire ``count`` workers simultaneously and collect their outcomes."""
    results: list = []
    errors: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(count)

    def wrapped(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            outcome = worker(index)
            with lock:
                results.append(outcome)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            _close()

    with ThreadPoolExecutor(max_workers=count) as pool:
        for future in as_completed([pool.submit(wrapped, i) for i in range(count)]):
            future.result()
    return results, errors


def _members(ctx, count):
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.models import Membership

    created = []
    for _ in range(count):
        user = User.objects.create_user(email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        membership = Membership(
            organization_id=ctx.organization_id,
            user_id=user.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        )
        membership.save()
        created.append(membership.id)
    return created


# --- seat allocation ------------------------------------------------------


def test_concurrent_seat_assignment_cannot_overallocate(
    catalog, customer, billing_account, in_tenant
):
    """The canonical case from the brief: limit 10, nine taken, two requests.

    Both must not become seat ten and eleven. Over-allocating paid seats is a
    revenue leak that is embarrassing to explain, and a counter cache with
    optimistic checking cannot prevent it.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        assert seats.capacity(ctx.organization_id, pool_key="crm") == 10
        existing = _members(ctx, 9)
        for member in existing:
            seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=member)
        contenders = _members(ctx, WORKERS)

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        try:
            with tenant_context(ctx), tenant_db_scope(ctx):
                seats.assign_seat(
                    ctx.organization_id,
                    pool_key="crm",
                    membership_id=contenders[index],
                )
            return "assigned"
        except SeatLimitReached:
            return "refused"

    results, errors = _run(worker)
    assert not errors, errors

    counts = Counter(results)
    assert counts["assigned"] == 1, f"{counts['assigned']} threads took the last seat"
    assert counts["refused"] == WORKERS - 1

    with in_tenant(ctx):
        assigned = SeatAssignment.objects.filter(
            seat_grant__pool_key="crm", released_at__isnull=True
        ).count()
    assert assigned == 10, f"{assigned} seats allocated from a pool of 10"


def test_concurrent_assignment_of_the_same_member_takes_one_seat(
    catalog, customer, billing_account, in_tenant
):
    """A double-submitted "add Alice" must not consume two seats.

    The row lock does not prevent this one - the race is between two
    different assignment rows - so the partial unique constraint is what
    resolves it, at the database.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        member = _members(ctx, 1)[0]

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        with tenant_context(ctx), tenant_db_scope(ctx):
            seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=member)
        return "ok"

    results, errors = _run(worker)
    assert not errors, errors
    assert len(results) == WORKERS

    with in_tenant(ctx):
        assert (
            SeatAssignment.objects.filter(
                membership_id=member, released_at__isnull=True
            ).count()
            == 1
        )
        assert seats.assigned_count(ctx.organization_id, pool_key="crm") == 1


# --- duplicate commands ---------------------------------------------------


def test_concurrent_identical_purchases_buy_once(
    catalog, customer, billing_account, in_tenant
):
    """Duplicate delivery of one command, arriving simultaneously.

    The idempotency ledger's unique constraint resolves it at the database,
    not by a read-then-write that two threads would both pass.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    command = key()
    plan_version_id = catalog["versions"]["crm_professional"].id

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        with tenant_context(ctx), tenant_db_scope(ctx):
            item = services.purchase_plan(
                ctx,
                plan_version_id=plan_version_id,
                idempotency_key=command,
            )
        return str(item.id)

    results, errors = _run(worker)
    assert not errors, errors
    assert len(set(results)) == 1, "the same command produced different items"

    with in_tenant(ctx):
        assert SubscriptionItem.objects.count() == 1
        assert Subscription.objects.count() == 1


def test_concurrent_different_purchases_do_not_create_two_subscriptions(
    catalog, customer, billing_account, in_tenant
):
    """One subscription per organization, under a race.

    Two different products bought at the same instant must land on one
    renewal cycle, not two.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    plans = [
        catalog["versions"]["crm_professional"].id,
        catalog["versions"]["books_professional"].id,
    ]

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        try:
            with tenant_context(ctx), tenant_db_scope(ctx):
                services.purchase_plan(
                    ctx,
                    plan_version_id=plans[index % 2],
                    idempotency_key=key(),
                )
            return "bought"
        except Exception as exc:
            return type(exc).__name__

    results, errors = _run(worker, count=2)
    assert not errors, errors

    with in_tenant(ctx):
        assert Subscription.objects.count() == 1
        open_items = SubscriptionItem.objects.filter(removed_at__isnull=True)
        products = [item.product_code for item in open_items]
    assert sorted(products) == ["books", "crm"], results
    assert len(products) == len(set(products)), "a product was duplicated"


def test_concurrent_bundle_expansion_does_not_duplicate_products(
    catalog, customer, billing_account, in_tenant
):
    """The question from the final audit, answered by a race rather than by
    reading the expansion code."""
    ctx = customer["ctx"]
    billing_account(ctx)
    command = key()
    bundle_version_id = catalog["bundle_version"].id

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        try:
            with tenant_context(ctx), tenant_db_scope(ctx):
                line = services.purchase_bundle(
                    ctx,
                    bundle_version_id=bundle_version_id,
                    idempotency_key=command,
                )
            return str(line.id)
        except Exception as exc:
            return type(exc).__name__

    results, errors = _run(worker)
    assert not errors, errors

    with in_tenant(ctx):
        items = list(SubscriptionItem.objects.filter(removed_at__isnull=True))
    products = [item.product_code for item in items if item.product_code]
    assert sorted(products) == ["books", "crm"], results
    assert len(items) == 3, f"{len(items)} lines from one bundle purchase"


def test_concurrent_activations_apply_once(catalog, customer, billing_account, in_tenant):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )
        subscription = Subscription.objects.get()
    command = key()

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        with tenant_context(ctx), tenant_db_scope(ctx):
            result = services.activate_subscription(
                ctx, subscription_id=subscription.id, idempotency_key=command
            )
        return result.status

    results, errors = _run(worker)
    assert not errors, errors
    assert set(results) == {"active"}

    with in_tenant(ctx):
        subscription.refresh_from_db()
    assert subscription.status == "active"


def test_a_reused_key_with_different_arguments_loses_the_race_cleanly(
    catalog, customer, billing_account, in_tenant
):
    """Under contention the mismatch must still be refused, not served."""
    ctx = customer["ctx"]
    billing_account(ctx)
    command = key()
    plans = [
        catalog["versions"]["crm_professional"].id,
        catalog["versions"]["books_professional"].id,
    ]

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        try:
            with tenant_context(ctx), tenant_db_scope(ctx):
                services.purchase_plan(
                    ctx,
                    plan_version_id=plans[index % 2],
                    idempotency_key=command,
                )
            return "accepted"
        except IdempotencyConflict:
            return "conflict"
        except Exception as exc:
            return type(exc).__name__

    results, errors = _run(worker)
    assert not errors, errors

    with in_tenant(ctx):
        assert SubscriptionItem.objects.count() == 1
    assert "conflict" in results


# --- plan change ----------------------------------------------------------


def test_concurrent_plan_changes_do_not_both_apply(
    catalog, customer, billing_account, in_tenant
):
    """Two admins clicking upgrade must not produce two upgrades.

    The one-open-line-per-product constraint is the backstop: even if the
    lock were removed, the database would refuse the second line.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        try:
            with tenant_context(ctx), tenant_db_scope(ctx):
                services.change_plan(
                    ctx,
                    subscription_item_id=item.id,
                    to_plan_version_id=catalog["versions"]["crm_professional"].id,
                    idempotency_key=key(),
                )
            return "changed"
        except Exception as exc:
            return type(exc).__name__

    results, errors = _run(worker)
    assert not errors, errors

    counts = Counter(results)
    assert counts["changed"] == 1, f"{counts['changed']} concurrent upgrades applied"

    with in_tenant(ctx):
        open_crm = SubscriptionItem.objects.filter(
            product_code="crm", removed_at__isnull=True
        )
        assert open_crm.count() == 1
        assert open_crm.first().plan_code == "crm_professional"


# --- provisioning ---------------------------------------------------------


def test_concurrent_reconciles_converge(catalog, customer, billing_account, in_tenant):
    """Racing recomputes must not corrupt the snapshot.

    An advisory lock serialises them per organization, and last-writer-wins
    is safe because compute is deterministic: two runs from the same durable
    state produce the same answer.
    """
    from workeasy360.platform.entitlements.selectors import stored_snapshot

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=key()
        )

    def worker(index: int) -> int:
        return reconcile(ctx.organization_id)

    results, errors = _run(worker)
    assert not errors, errors
    assert len(results) == WORKERS

    snapshot = stored_snapshot(ctx.organization_id)
    assert snapshot is not None
    assert set(snapshot.products) == {"crm", "books"}
    assert snapshot.products["crm"]["status"] == "active"
    assert snapshot.features["crm.pipelines.max"] == 25
    # Versions advance monotonically, one per apply.
    assert snapshot.version >= WORKERS


def test_concurrent_reconcile_and_seat_assignment_do_not_deadlock(
    catalog, customer, billing_account, in_tenant
):
    """Provisioning rewrites seat capacity while admins assign seats.

    Both paths take locks on ``SeatGrant`` rows; if they ordered them
    differently this would deadlock under load, which is the kind of failure
    that only appears in production.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        contenders = _members(ctx, WORKERS)

    def worker(index: int) -> str:
        from workeasy360.platform.tenancy.context import tenant_context
        from workeasy360.platform.tenancy.db import tenant_db_scope

        if index % 2 == 0:
            reconcile(ctx.organization_id)
            return "reconciled"
        try:
            with tenant_context(ctx), tenant_db_scope(ctx):
                seats.assign_seat(
                    ctx.organization_id,
                    pool_key="crm",
                    membership_id=contenders[index],
                )
            return "assigned"
        except SeatLimitReached:
            return "refused"

    results, errors = _run(worker)
    assert not errors, errors
    assert len(results) == WORKERS

    with in_tenant(ctx):
        assert seats.capacity(ctx.organization_id, pool_key="crm") == 10
        assert seats.assigned_count(ctx.organization_id, pool_key="crm") <= 10


def test_the_commercial_clock_is_safe_to_run_twice_at_once(
    catalog, customer, billing_account, in_tenant
):
    """A scheduler that double-fires must not double-apply."""
    from datetime import timedelta

    from django.utils import timezone

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
    at = timezone.now() + timedelta(days=30)

    def worker(index: int) -> dict:
        return system.process_due_subscriptions(at=at)

    results, errors = _run(worker, count=4)
    assert not errors, errors

    expired = sum(result["trials_expired"] for result in results)
    assert expired == 1, f"the trial expired {expired} times"
