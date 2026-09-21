"""Publishing to the outbox, and the relay that drains it."""

from __future__ import annotations

import random
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.enums import ActorKind, OutboxStatus
from workeasy360.contracts.events.envelope import Actor, DomainEvent
from workeasy360.contracts.events.registry import registry
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
        kind=ActorKind.STAFF_BREAK_GLASS if ctx.is_break_glass else ActorKind.USER,
        user_id=ctx.user_id,
        membership_id=ctx.membership_id,
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
    base = float(getattr(settings, "TASK_RETRY_BACKOFF_BASE_S", 2.0))
    cap = float(getattr(settings, "TASK_RETRY_BACKOFF_CAP_S", 3600.0))
    return random.uniform(0, min(cap, base * (2**attempt)))  # noqa: S311 - retry jitter, not cryptography


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
