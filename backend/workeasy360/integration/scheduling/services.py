"""Scheduling, claiming and dispatching due work.

Consumers call :func:`schedule` (create or move a timer) and :func:`complete`
(remove it) from inside their own tenant transactions - the timer table has
no RLS, so the writes land in the same transaction as the tenant work they
describe and commit or roll back with it. The sweeper calls
:func:`claim_due` and :func:`dispatch` across tenants.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerUnavailable

from workeasy360.contracts.enums import OrganizationStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: What a failed enqueue raises when the broker cannot be reached. Each costs
#: ``CELERY_BROKER_CONNECTION_TIMEOUT`` (1 s), so a loop that kept trying
#: through an outage would hold its worker for one second per item - 200 s
#: for a dispatch batch, far past its time limit. The first one ends the
#: loop instead: every item already has a durable timer, so the rest are
#: delayed until the broker returns, never lost (Task 68).
BROKER_ERRORS: Final = (BrokerUnavailable, ConnectionError, TimeoutError)

#: Actor ids for work no person performed. Nil rather than borrowed, for the
#: reason ``audit.record_system`` gives: a fabricated actor puts a name
#: against something they did not do (same convention as the notification
#: sweeper).
NO_ACTOR: Final = UUID(int=0)

#: The claim is a single statement: pick due rows nobody else holds, push
#: their ``due_at`` out by a lease that doubles with every unacknowledged
#: claim (capped), and return them. ``SKIP LOCKED`` lets any number of
#: sweepers run concurrently without blocking or double-claiming.
#: Parameterised throughout; no identifier is interpolated (G7).
_CLAIM_SQL: Final = """
    WITH due AS (
        SELECT id FROM scheduling_due_work
        WHERE due_at <= %s
        ORDER BY due_at, id
        LIMIT %s
        FOR UPDATE SKIP LOCKED
    )
    UPDATE scheduling_due_work AS w
    SET due_at = %s + make_interval(
            secs => LEAST(%s * power(2, LEAST(w.claims, 12)), %s)
        ),
        claims = w.claims + 1
    FROM due
    WHERE w.id = due.id
    RETURNING w.id, w.kind, w.organization_id, w.target_id, w.claims
"""


@dataclass(frozen=True, slots=True)
class DueItem:
    kind: str
    organization_id: UUID
    target_id: UUID
    #: How many times this timer has now been dispatched without the
    #: consumer acknowledging it. Above ``SCHEDULING_STUCK_CLAIMS`` it is
    #: reported as stuck.
    claims: int


#: kind -> function that enqueues the consumer task for one item. Registered
#: by each consumer module at app-ready, so this module - the lowest layer of
#: integration/ - never imports the modules that use it.
Dispatcher = Callable[[DueItem], None]
_dispatchers: dict[str, Dispatcher] = {}


def register_dispatcher(kind: str, dispatcher: Dispatcher) -> None:
    _dispatchers[kind] = dispatcher


# --- consumer side --------------------------------------------------------


def schedule(
    kind: str, *, organization_id: UUID, target_id: UUID, due_at: datetime
) -> None:
    """Create the timer, or move it to ``due_at`` and reset its claim count."""
    schedule_many(kind, [(organization_id, target_id)], due_at=due_at)


#: One statement for a whole fan-out. A timer's organization is fixed when it
#: is created: moving a timer never re-targets it, so a conflicting row that
#: names a *different* organization is left untouched rather than handed to
#: the caller's (D-017 - the sweeper dispatches into whichever organization
#: the row names, so that identity must be immutable).
_UPSERT_SQL: Final = """
    INSERT INTO scheduling_due_work
        (kind, organization_id, target_id, due_at, claims, created_at)
    VALUES {values}
    ON CONFLICT (kind, target_id) DO UPDATE
        SET due_at = EXCLUDED.due_at, claims = 0
        WHERE scheduling_due_work.organization_id = EXCLUDED.organization_id
"""


def schedule_many(
    kind: str, items: Iterable[tuple[UUID, UUID]], *, due_at: datetime
) -> None:
    """Create or move a timer per item (``INSERT ... ON CONFLICT``)."""
    rows = list(dict.fromkeys(items))
    if not rows:
        return
    now = timezone.now()
    params: list[Any] = []
    for organization_id, target_id in rows:
        params.extend([kind, organization_id, target_id, due_at, now])
    sql = _UPSERT_SQL.format(values=", ".join(["(%s, %s, %s, %s, 0, %s)"] * len(rows)))
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        written = cursor.rowcount
    if written < len(rows):
        # Only possible if a caller named the wrong organization for a
        # target - a bug, and one that must not move another tenant's work.
        metrics.increment("integration_due_work_scope_conflicts_total", kind=kind)
        logger.error(
            "scheduling.due_work.scope_conflict", kind=kind, refused=len(rows) - written
        )


def complete(kind: str, target_id: UUID, *, organization_id: UUID | None = None) -> None:
    """The work is finished (or will never be done); drop its timer.

    Only the timer of the organization in scope - ``organization_id``, or
    else the current tenant context, which every consumer runs in. A task
    that somehow ran in the wrong organization finds no row of its own and
    deletes nothing, instead of cancelling another tenant's pending work.
    """
    from workeasy360.integration.scheduling.models import DueWork
    from workeasy360.platform.tenancy.context import current_tenant_or_none

    if organization_id is None:
        ctx = current_tenant_or_none()
        organization_id = ctx.organization_id if ctx is not None else None
    rows = DueWork.objects.filter(kind=kind, target_id=target_id)
    if organization_id is not None:
        rows = rows.filter(organization_id=organization_id)
    rows.delete()


# --- sweeper side ---------------------------------------------------------


def claim_due(*, limit: int | None = None) -> list[DueItem]:
    """Claim a batch of due timers, committing the claim before returning.

    The claim commits *before* anything is dispatched, so a broker call never
    happens inside a database transaction (E2), and a sweeper that dies after
    claiming leaves only a lease that expires.
    """
    batch = int(limit or settings.SCHEDULING_CLAIM_BATCH)
    lease = float(settings.SCHEDULING_LEASE_S)
    lease_cap = float(settings.SCHEDULING_LEASE_CAP_S)
    now = timezone.now()

    with transaction.atomic(), connection.cursor() as cur:
        cur.execute(_CLAIM_SQL, [now, batch, now, lease, lease_cap])
        rows = cur.fetchall()

    return [
        DueItem(kind=kind, organization_id=org, target_id=target, claims=claims)
        for _id, kind, org, target, claims in rows
    ]


def dispatch(items: list[DueItem]) -> dict[str, int]:
    """Enqueue each claimed item's consumer task, inside its tenant context.

    Per-item isolation: an item that fails stays claimed (its lease expires
    and it is retried) without affecting the rest of the batch - except a
    broker outage, which is not per-item: the first one stops the batch and
    the remaining items wait out their leases (``BROKER_ERRORS``). A timer
    for an organization that no longer exists is dropped.
    """
    from workeasy360.platform.tenancy.context import tenant_context

    stuck_after = int(settings.SCHEDULING_STUCK_CLAIMS)
    contexts: dict[UUID, TenantContext | None] = {}
    dispatched = failed = dropped = deferred = 0

    for position, item in enumerate(items):
        dispatcher = _dispatchers.get(item.kind)
        if dispatcher is None:
            # A kind nobody registered is a deploy-order problem (a worker
            # running older code); leave it for a worker that knows it.
            failed += 1
            logger.error("scheduling.dispatcher.unknown_kind", kind=item.kind)
            continue

        if item.organization_id not in contexts:
            contexts[item.organization_id] = routing_context(item.organization_id)
        ctx = contexts[item.organization_id]
        if ctx is None:
            complete(item.kind, item.target_id, organization_id=item.organization_id)
            dropped += 1
            continue

        if item.claims > stuck_after:
            metrics.increment("integration_due_work_stuck_total", kind=item.kind)
            logger.error(
                "scheduling.due_work.stuck",
                kind=item.kind,
                target_id=str(item.target_id),
                claims=item.claims,
            )

        try:
            with tenant_context(ctx):
                dispatcher(item)
            dispatched += 1
            metrics.increment("integration_due_work_dispatched_total", kind=item.kind)
        except BROKER_ERRORS as exc:
            failed += 1
            deferred = len(items) - position - 1
            metrics.increment(
                "integration_due_work_dispatch_failed_total", kind=item.kind
            )
            logger.warning(
                "scheduling.broker_unavailable",
                error_type=type(exc).__name__,
                deferred=deferred,
            )
            break
        except Exception as exc:
            failed += 1
            metrics.increment(
                "integration_due_work_dispatch_failed_total", kind=item.kind
            )
            logger.warning(
                "scheduling.dispatch_failed",
                kind=item.kind,
                target_id=str(item.target_id),
                error_type=type(exc).__name__,
            )

    return {
        "dispatched": dispatched,
        "failed": failed,
        "dropped": dropped,
        "deferred": deferred,
    }


def routing_context(organization_id: UUID) -> TenantContext | None:
    """A tenant context that routes and claims to be nobody.

    The organization's status is **read, never assumed** - it drives the
    R-10 write block, and a background path that defaulted to ``active``
    would write to a suspended organization the request path refuses. The
    actor is nil for the reason ``NO_ACTOR`` gives. ``None`` when the
    organization no longer exists.
    """
    from workeasy360.platform.organizations.selectors import organization_status

    status = organization_status(organization_id)
    if status is None:
        return None
    return TenantContext(
        organization_id=organization_id,
        membership_id=NO_ACTOR,
        user_id=NO_ACTOR,
        organization_status=OrganizationStatus(status),
    )


def overdue_seconds() -> float:
    """How late the oldest due timer is - the integration pipeline's lag SLI.

    Depth alone misleads for the same reason it does for the outbox: a large
    draining backlog is healthy, a small stuck one is not.
    """
    from workeasy360.integration.scheduling.models import DueWork

    oldest = (
        DueWork.objects.filter(due_at__lte=timezone.now())
        .order_by("due_at")
        .values_list("due_at", flat=True)
        .first()
    )
    return (timezone.now() - oldest).total_seconds() if oldest else 0.0


def enqueue_after_commit(
    task: Any, target_ids: Iterable[UUID], *, ctx: TenantContext
) -> None:
    """Send ``task`` for each id once the current transaction commits.

    The low-latency path: the work starts as soon as the row it needs is
    visible, instead of waiting for the next sweep. Losing it costs latency,
    never the work - every caller has already scheduled a timer for each id
    in the same transaction, so a broker that is down now is a delivery made
    by the sweeper later (Task 68). That is what makes ``on_commit`` safe
    here: it is an optimisation over a durable record, never the record.

    The context is captured now and re-entered in the callback, so the
    enqueue carries the right tenant whichever code commits the transaction.
    """
    from workeasy360.platform.tenancy.context import tenant_context

    ids = [str(i) for i in target_ids]
    if not ids:
        return

    def _send() -> None:
        with tenant_context(ctx):
            for position, target in enumerate(ids):
                try:
                    task.apply_async(args=[target])
                except BROKER_ERRORS as exc:
                    logger.warning(
                        "scheduling.enqueue_failed",
                        task=getattr(task, "name", "?"),
                        target_id=target,
                        error_type=type(exc).__name__,
                        left_to_timers=len(ids) - position - 1,
                    )
                    return
                except Exception as exc:
                    logger.warning(
                        "scheduling.enqueue_failed",
                        task=getattr(task, "name", "?"),
                        target_id=target,
                        error_type=type(exc).__name__,
                    )

    transaction.on_commit(_send)
