"""Publishing to the outbox, and the relay that drains it."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.enums import ActorKind, OutboxStatus, PrincipalKind
from workeasy360.contracts.events.envelope import Actor, DomainEvent
from workeasy360.contracts.events.registry import registry
from workeasy360.contracts.values import backoff
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.observability.correlation import current_correlation_id
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import (
    OUTBOX_DEAD_LETTERED,
    OUTBOX_PUBLISHED,
    metrics,
)

logger = get_logger(__name__)


def publish(event: DomainEvent) -> Any:
    """Write an event to the outbox. An INSERT, nothing more.

    No network call, no broker connection, no latency added to the request.
    If the enclosing transaction rolls back, the event vanishes with it.

    Must be called inside a transaction. Publishing outside one would commit
    the event independently of the business change it describes, which
    reintroduces exactly the dual-write problem the outbox exists to solve.
    """
    from django.db import connection

    from workeasy360.integration.events.models import OutboxEvent

    if not connection.in_atomic_block:
        raise RuntimeError(
            f"events.publish({event.event_type}) called outside a transaction. "
            f"The event must be written in the same transaction as the "
            f"business change, or the two can diverge (ADR-008)."
        )

    # R-13: validate at publish time, in the publisher's transaction, so a
    # malformed payload never reaches the outbox and the failure lands on the
    # code that caused it rather than on a consumer days later.
    registry.validate(event.event_type, event.event_version, event.payload)

    row = OutboxEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        event_version=event.event_version,
        organization_id=event.organization_id,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        envelope=event.to_envelope(),
        correlation_id=event.correlation_id or current_correlation_id(),
        status=OutboxStatus.PENDING.value,
    )
    row.save()

    logger.info(
        "event.published_to_outbox",
        event_type=event.event_type,
        event_id=str(event.event_id),
        outbox_id=row.id,
    )
    return row


def _actor_kind(ctx: TenantContext) -> ActorKind:
    if ctx.is_break_glass:
        return ActorKind.STAFF_BREAK_GLASS
    if ctx.principal is PrincipalKind.STAFF:
        return ActorKind.STAFF
    if ctx.principal is PrincipalKind.PARTNER:
        return ActorKind.PARTNER
    return ActorKind.USER


def build_event(
    ctx: TenantContext,
    event_type: str,
    version: int,
    *,
    aggregate_type: str,
    aggregate_id: str | UUID,
    payload: dict[str, Any],
    causation: DomainEvent | None = None,
) -> DomainEvent:
    """Construct an event from the active tenant context.

    Correlation is taken from the ambient context rather than passed in, so
    the chain cannot be broken by a caller forgetting to thread it through.
    """
    actor = Actor(
        kind=_actor_kind(ctx),
        user_id=ctx.user_id,
        # A control-plane principal holds no membership; the nil id would be a
        # fabricated reference in every consumer's copy of the event.
        membership_id=None if ctx.is_control_plane else ctx.membership_id,
    )
    if causation is not None:
        return causation.caused(
            event_type=event_type,
            event_version=version,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            payload=payload,
            actor=actor,
        )
    return DomainEvent(
        event_type=event_type,
        event_version=version,
        organization_id=ctx.organization_id,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload=payload,
        actor=actor,
        correlation_id=current_correlation_id(),
    )


# --- relay ---------------------------------------------------------------


def claim_batch(batch_size: int | None = None) -> list[Any]:
    """Claim a batch of pending events for publication.

    ``SELECT ... FOR UPDATE SKIP LOCKED`` lets many relay workers drain one
    table without blocking each other, avoiding both the single-relay
    bottleneck and lock contention.

    The claim is committed before publishing begins, so a worker crash leaves
    rows in ``publishing`` rather than holding a lock forever; a sweeper
    returns stale claims to ``pending``.
    """
    from workeasy360.integration.events.models import OutboxEvent

    size = batch_size or getattr(settings, "OUTBOX_RELAY_BATCH_SIZE", 200)

    with transaction.atomic():
        rows = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .claimable()
            .order_by("id")[:size]
        )
        if rows:
            OutboxEvent.objects.filter(id__in=[r.id for r in rows]).update(
                status=OutboxStatus.PUBLISHING.value
            )
    return rows


def mark_published(row: Any) -> None:
    from workeasy360.integration.events.models import OutboxEvent

    OutboxEvent.objects.filter(id=row.id).update(
        status=OutboxStatus.PUBLISHED.value,
        published_at=timezone.now(),
        last_error="",
    )
    metrics.increment(OUTBOX_PUBLISHED, event_type=row.event_type)


def schedule_retry(row: Any, error: Exception) -> None:
    """Back off, or dead-letter once attempts are exhausted.

    Bounded at ``OUTBOX_MAX_ATTEMPTS``. Never infinite: an event that cannot
    be processed will not become processable by being retried forever, and
    infinite retries turn one poison event into a permanent queue clog.
    """
    from workeasy360.integration.events.models import DeadLetterEvent, OutboxEvent

    max_attempts = getattr(settings, "OUTBOX_MAX_ATTEMPTS", 5)
    attempts = row.attempts + 1

    if attempts >= max_attempts:
        with transaction.atomic():
            DeadLetterEvent.objects.create(
                event_id=row.event_id,
                event_type=row.event_type,
                organization_id=row.organization_id,
                envelope=row.envelope,
                attempts=attempts,
                error=f"{type(error).__name__}: {error}"[:2000],
            )
            OutboxEvent.objects.filter(id=row.id).update(
                status=OutboxStatus.DEAD_LETTERED.value,
                attempts=attempts,
                last_error=f"{type(error).__name__}: {error}"[:2000],
            )
        metrics.increment(OUTBOX_DEAD_LETTERED, event_type=row.event_type)
        logger.error(
            "event.dead_lettered",
            event_type=row.event_type,
            event_id=str(row.event_id),
            attempts=attempts,
        )
        return

    delay = _backoff_seconds(attempts)
    OutboxEvent.objects.filter(id=row.id).update(
        status=OutboxStatus.FAILED.value,
        attempts=attempts,
        available_at=timezone.now() + timezone.timedelta(seconds=delay),
        last_error=f"{type(error).__name__}: {error}"[:2000],
    )
    logger.warning(
        "event.publish.retry_scheduled",
        event_type=row.event_type,
        attempts=attempts,
        delay_s=round(delay, 1),
    )


def _backoff_seconds(attempt: int) -> float:
    """Exponential with **full jitter**, capped.

    ``random(0, min(cap, base * 2^n))``. Without jitter every failed event
    retries at the same instant, producing a synchronised herd against a
    dependency that is trying to recover.
    """
    return backoff.full_jitter(
        attempt,
        base=float(getattr(settings, "TASK_RETRY_BACKOFF_BASE_S", 2.0)),
        cap=float(getattr(settings, "TASK_RETRY_BACKOFF_CAP_S", 3600.0)),
    )


def release_stale_claims(older_than_seconds: int = 300) -> int:
    """Return abandoned ``publishing`` rows to ``pending``.

    A relay worker that is OOM-killed between claiming and publishing leaves
    rows claimed forever. This sweeper is what makes a worker crash a delay
    rather than permanent event loss.
    """
    from workeasy360.integration.events.models import OutboxEvent

    cutoff = timezone.now() - timezone.timedelta(seconds=older_than_seconds)
    return OutboxEvent.objects.filter(
        status=OutboxStatus.PUBLISHING.value, created_at__lte=cutoff
    ).update(status=OutboxStatus.PENDING.value, available_at=timezone.now())


# --- consumer side -------------------------------------------------------


class AlreadyProcessed(Exception):
    """Raised by ``mark_processed`` when this consumer has seen the event."""


def mark_processed(consumer: str, event_id: UUID) -> None:
    """Claim an event for a consumer, idempotently.

    Call this **inside** the handler's transaction, before the effect. The
    unique constraint does the deduplication, not application logic, so a
    genuine race between two workers resolves at the database.
    """
    from django.db import IntegrityError

    from workeasy360.integration.events.models import ProcessedEvent

    try:
        # The savepoint is load-bearing, not tidiness. PostgreSQL aborts the
        # whole transaction on a constraint violation, so catching
        # IntegrityError without one leaves the connection unusable and the
        # handler's own work unrollbackable - turning a routine duplicate
        # delivery into a failed transaction.
        with transaction.atomic():
            ProcessedEvent.objects.create(consumer=consumer, event_id=event_id)
    except IntegrityError:
        raise AlreadyProcessed(f"{consumer} has already processed {event_id}") from None


def pending_age_seconds() -> float:
    """Oldest pending event age - the primary pipeline SLI.

    Depth alone is misleading: a large draining backlog is healthy while a
    small stuck one is not.
    """
    from workeasy360.integration.events.models import OutboxEvent

    oldest = (
        OutboxEvent.objects.claimable()
        .order_by("id")
        .values_list("created_at", flat=True)
        .first()
    )
    return (timezone.now() - oldest).total_seconds() if oldest else 0.0


# --- routable events (Phase 6) ----------------------------------------------
#
# The integration platform's event log. Writers are ingestion (product events)
# and the platform itself (test pings, schedule firings); readers are routing,
# the delivery pipeline and the automation engine, all through
# ``integration.events.selectors``.


class RoutableEventConflict(Exception):
    """The event id was already used for a *different* event.

    A retry replays the same event and gets the original answer. The same id
    with a different body is either a producer bug or an attempt to have one
    idempotency key stand for two facts, and is refused (Task 10).
    """


def record_routable_event(
    *,
    event_id: UUID,
    source: str,
    event_type: str,
    event_version: int,
    occurred_at: Any,
    payload: dict[str, Any],
    payload_bytes: int,
    correlation_id: UUID | None = None,
    causation_id: UUID | None = None,
    routing_status: str | None = None,
) -> tuple[Any, bool]:
    """Insert the event, or return the existing one for a duplicate.

    Returns ``(dto, created)``. Must run inside the organization's tenant
    context, database scope and a transaction - the caller's, so that the
    event and whatever the caller schedules for it commit together.

    Deduplication is the unique constraint, inside a savepoint: two
    concurrent submissions of one event genuinely race, and only the
    database can pick the winner (Task 10).
    """
    import hashlib
    import json
    import uuid

    from django.db import IntegrityError, connection

    from workeasy360.integration.events.models import RoutedEvent, RoutingStatus
    from workeasy360.integration.events.selectors import to_routed_dto

    if not connection.in_atomic_block:
        raise RuntimeError("record_routable_event must run inside a transaction.")

    fingerprint = hashlib.sha256(
        json.dumps(
            [event_type, int(event_version), occurred_at.isoformat(), payload],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    depth = 0
    correlation = correlation_id
    if causation_id is not None:
        parent = (
            RoutedEvent.objects.filter(event_id=causation_id)
            .order_by("-created_at")
            .values("chain_depth", "correlation_id")
            .first()
        )
        if parent is not None:
            # Derived from the platform's own record of the causing event,
            # never from a producer-supplied depth: a product cannot reset a
            # loop's count by omitting it or lie it upward to evade nothing.
            depth = int(parent["chain_depth"]) + 1
            correlation = correlation or parent["correlation_id"]

    row = RoutedEvent(
        event_id=event_id,
        source=source,
        event_type=event_type,
        event_version=int(event_version),
        occurred_at=occurred_at,
        correlation_id=correlation or uuid.uuid4(),
        causation_id=causation_id,
        chain_depth=min(depth, 32767),
        payload=payload,
        payload_bytes=int(payload_bytes),
        fingerprint=fingerprint,
        routing_status=routing_status or RoutingStatus.PENDING.value,
    )
    try:
        with transaction.atomic():
            row.save()
        return to_routed_dto(row), True
    except IntegrityError:
        pass

    existing = RoutedEvent.objects.filter(source=source, event_id=event_id).first()
    if existing is None:  # pragma: no cover - lost between insert and read
        raise RuntimeError(f"Event {event_id} could not be recorded or read.")
    if existing.fingerprint != fingerprint:
        raise RoutableEventConflict(
            f"Event id {event_id} was already used for a different event."
        )
    return to_routed_dto(existing), False


def record_platform_event(
    event_type: str,
    version: int,
    payload: dict[str, Any],
    *,
    event_id: UUID | None = None,
    correlation_id: UUID | None = None,
    direct: bool = False,
) -> tuple[Any, bool]:
    """Record a Workeasy360-owned integration event (``integration.*``).

    Validated against the contracts registry exactly as an outbox event is
    (R-13): a malformed platform event fails in the code that built it.
    ``direct`` marks an event created for exactly one consumer (a test ping,
    a schedule firing) that routing must never fan out.
    """
    import json
    import uuid

    from workeasy360.contracts.events.namespaces import is_platform_owned
    from workeasy360.integration.events.models import RoutingStatus

    if not is_platform_owned(event_type):
        raise ValueError(f"{event_type} is not a Workeasy360-owned event type.")
    registry.validate(event_type, version, payload)
    return record_routable_event(
        event_id=event_id or uuid.uuid4(),
        source="platform",
        event_type=event_type,
        event_version=version,
        occurred_at=timezone.now(),
        payload=payload,
        payload_bytes=len(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        correlation_id=correlation_id or current_correlation_id(),
        routing_status=RoutingStatus.DIRECT.value if direct else None,
    )


def mark_routed(event_pk: UUID, *, deliveries: int, rules: int) -> bool:
    """Close a pending event's routing. Conditional, so exactly one router
    wins even if two are dispatched for the same event."""
    from workeasy360.integration.events.models import RoutedEvent, RoutingStatus

    return bool(
        RoutedEvent.objects.filter(
            id=event_pk, routing_status=RoutingStatus.PENDING.value
        ).update(
            routing_status=RoutingStatus.ROUTED.value,
            routed_at=timezone.now(),
            matched_deliveries=deliveries,
            matched_rules=rules,
        )
    )


def prune_routed_events(*, older_than: Any, keep: set[UUID], limit: int = 500) -> int:
    """Delete terminal events older than ``older_than``, except ``keep``.

    ``keep`` is the set the caller (``integration/routing``, which can see
    deliveries and executions) found still referenced. A foreign key with
    ``ON DELETE NO ACTION`` backs that up at commit, so a reference missed
    here fails the prune rather than deleting history out from under it.
    """
    from workeasy360.integration.events.models import RoutedEvent, RoutingStatus

    ids = list(
        RoutedEvent.objects.filter(created_at__lt=older_than)
        .exclude(routing_status=RoutingStatus.PENDING.value)
        .exclude(id__in=keep)
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )
    if not ids:
        return 0
    deleted, _ = RoutedEvent.objects.filter(id__in=ids).delete()
    return deleted
