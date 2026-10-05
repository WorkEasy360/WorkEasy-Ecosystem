"""Subscriptions, delivery creation, test pings and manual replay.

Customer-facing functions authorize first, validate everything second and
write last, inside one ``transaction.atomic()`` (D-016). System functions -
the fan-out used by routing and the action delivery used by automation -
run inside the caller's tenant transaction and are idempotent by unique
constraint, so a routing or execution retry never produces a second
delivery (Tasks 10, 37).
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.errors import (
    IntegrationConflict,
    IntegrationNotFound,
    IntegrationValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.connectors import selectors as connections
from workeasy360.integration.connectors.permissions import CONNECTION_MANAGE
from workeasy360.integration.events.selectors import RoutedEventDTO
from workeasy360.integration.scheduling import services as scheduling
from workeasy360.integration.webhooks.models import (
    DeadReason,
    Delivery,
    DeliveryOrigin,
    DeliveryStatus,
    EventSubscription,
)
from workeasy360.integration.webhooks.permissions import WEBHOOK_REPLAY
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: The timer kind every delivery is scheduled under.
TIMER_KIND: Final = "webhooks.delivery"
#: The one capability events are delivered through today.
DELIVER_EVENT: Final = "deliver_event"


# --- subscriptions ------------------------------------------------------------


def set_subscriptions(
    ctx: TenantContext, connection_id: Any, *, event_filters: Any
) -> list[str]:
    """Replace a connection's subscriptions with ``event_filters``.

    Replace-all rather than add/remove calls, so the request is idempotent
    and a retried PUT converges on the same set. Every filter is validated
    against the organization's catalogue (entitled products, declared
    events) before anything is written.
    """
    from workeasy360.integration.events.catalog import validate_filter

    authz.require(ctx, CONNECTION_MANAGE)
    connection = _connection(connection_id)
    if connection.is_disabled:
        raise IntegrationConflict("A disabled connection cannot be subscribed.")

    if not isinstance(event_filters, list):
        raise IntegrationValidationError(
            "event_filters must be a list.", field="event_filters"
        )
    limit = int(settings.WEBHOOK_MAX_SUBSCRIPTIONS_PER_CONNECTION)
    if len(event_filters) > limit:
        raise IntegrationValidationError(
            f"A connection may subscribe to at most {limit} filters.",
            field="event_filters",
        )
    wanted: set[str] = set()
    for index, raw in enumerate(event_filters):
        wanted.add(
            validate_filter(
                ctx.organization_id, raw, field=f"event_filters[{index}]"
            ).value
        )

    current = set(
        EventSubscription.objects.filter(connection_id=connection.id).values_list(
            "event_filter", flat=True
        )
    )
    added, removed = sorted(wanted - current), sorted(current - wanted)
    if not added and not removed:
        return sorted(wanted)

    with transaction.atomic():
        EventSubscription.objects.filter(
            connection_id=connection.id, event_filter__in=removed
        ).delete()
        # ignore_conflicts: a concurrent request adding the same filter is
        # the same intent, and the unique constraint makes it one row.
        EventSubscription.objects.bulk_create(
            [
                EventSubscription(
                    connection_id=connection.id,
                    event_filter=value,
                    created_by_id=ctx.user_id,
                )
                for value in added
            ],
            ignore_conflicts=True,
        )
        audit.record(
            ctx,
            "integration.subscription.changed",
            resource_type="integration.connection",
            resource_id=connection.id,
            changes={"added": added, "removed": removed},
        )
    return sorted(wanted)


# --- delivery creation (system) ------------------------------------------------


def create_event_deliveries(
    event: RoutedEventDTO, connection_ids: set[UUID]
) -> list[UUID]:
    """Fan one event out to ``connection_ids``; return the ids to send now.

    A fixed number of queries whatever the fan-out (Task 73): one backlog
    count, one bulk insert, one read-back, one timer upsert. A connection at
    its backlog bound still gets a delivery - created dead with reason
    ``backlog_limit``, visible and replayable - so nothing is dropped
    silently and nothing grows without bound (Task 50).
    """
    from workeasy360.integration.webhooks.selectors import open_delivery_counts

    if not connection_ids:
        return []
    now = timezone.now()
    backlog_limit = int(settings.WEBHOOK_MAX_OPEN_PER_CONNECTION)
    counts = open_delivery_counts(connection_ids)
    rows = []
    for connection_id in sorted(connection_ids):
        over = counts.get(connection_id, 0) >= backlog_limit
        rows.append(
            _new_delivery(
                event=event,
                connection_id=connection_id,
                origin=DeliveryOrigin.SUBSCRIPTION,
                now=now,
                dead_reason=DeadReason.BACKLOG_LIMIT if over else None,
            )
        )
        if over:
            metrics.increment("webhook_backlog_rejected_total")
    Delivery.objects.bulk_create(rows, ignore_conflicts=True)

    pending = list(
        Delivery.objects.filter(
            event_id=event.id,
            origin=DeliveryOrigin.SUBSCRIPTION.value,
            status=DeliveryStatus.PENDING.value,
        ).values_list("id", flat=True)
    )
    _schedule(event.organization_id, pending, now=now)
    return pending


def create_action_delivery(
    event: RoutedEventDTO,
    *,
    connection_id: UUID,
    capability: str,
    execution_id: UUID,
    rule_id: UUID,
    action_key: str,
) -> tuple[UUID, bool]:
    """The delivery an automation action asked for - once per (execution,
    action), however many times the execution is retried (Task 37)."""
    from workeasy360.integration.webhooks.selectors import open_delivery_counts

    now = timezone.now()
    existing = (
        Delivery.objects.filter(
            automation_execution_id=execution_id, action_key=action_key
        )
        .values_list("id", flat=True)
        .first()
    )
    if existing is not None:
        return existing, False

    over = open_delivery_counts([connection_id]).get(connection_id, 0) >= int(
        settings.WEBHOOK_MAX_OPEN_PER_CONNECTION
    )
    row = _new_delivery(
        event=event,
        connection_id=connection_id,
        origin=DeliveryOrigin.AUTOMATION,
        now=now,
        capability=capability,
        dead_reason=DeadReason.BACKLOG_LIMIT if over else None,
    )
    row.automation_execution_id = execution_id
    row.automation_rule_id = rule_id
    row.action_key = action_key
    try:
        with transaction.atomic():
            row.save()
    except IntegrityError:
        found = (
            Delivery.objects.filter(
                automation_execution_id=execution_id, action_key=action_key
            )
            .values_list("id", flat=True)
            .first()
        )
        if found is None:  # pragma: no cover - lost between insert and read
            raise
        return found, False
    if not over:
        _schedule(event.organization_id, [row.id], now=now)
    return row.id, True


# --- customer actions: ping and replay ------------------------------------------


def ping(ctx: TenantContext, connection_id: Any) -> UUID:
    """Send ``integration.webhook.pinged`` to one connection - even a draft.

    The end-to-end check an administrator runs before activating: the
    endpoint is reachable, passes the SSRF policy at send time, and their
    receiver verifies the signature (Task 93, steps 3 and 11).
    """
    from workeasy360.contracts.events.integration_events import WEBHOOK_PINGED_V1
    from workeasy360.integration.events.services import record_platform_event
    from workeasy360.integration.webhooks import tasks

    authz.require(ctx, CONNECTION_MANAGE)
    connection = _connection(connection_id)
    if connection.is_disabled:
        raise IntegrationConflict("A disabled connection cannot be tested.")
    _throttle_ping(connection.id)

    now = timezone.now()
    with transaction.atomic():
        event, _ = record_platform_event(
            WEBHOOK_PINGED_V1.event_type,
            WEBHOOK_PINGED_V1.version,
            {
                "connection_id": str(connection.id),
                "message": "Test event from Workeasy360.",
            },
            direct=True,
        )
        row = _new_delivery(
            event=event,
            connection_id=connection.id,
            origin=DeliveryOrigin.PING,
            now=now,
        )
        row.requested_by_id = ctx.user_id
        row.save()
        _schedule(ctx.organization_id, [row.id], now=now)
    scheduling.enqueue_after_commit(tasks.deliver, [row.id], ctx=ctx)
    return row.id


def replay(ctx: TenantContext, delivery_id: Any) -> UUID:
    """Replay a dead delivery as a new delivery of the same event (Task 26).

    The original is locked, checked and left exactly as it was - dead, with
    its attempts. The replay is a separate row pointing at it, sent to the
    same connection with the same event, and audited with who asked. Only a
    ``dead`` delivery is eligible: one still retrying is already being
    delivered, and replaying it would race the automatic retry (Task 94).
    """
    from workeasy360.integration.events.selectors import routed_event
    from workeasy360.integration.webhooks import tasks

    authz.require(ctx, WEBHOOK_REPLAY)
    try:
        key = UUID(str(delivery_id))
    except ValueError:
        raise IntegrationNotFound(resource="delivery") from None

    with transaction.atomic():
        original = Delivery.objects.select_for_update().filter(id=key).first()
        if original is None:
            raise IntegrationNotFound(resource="delivery")
        if original.status != DeliveryStatus.DEAD.value:
            raise IntegrationConflict(
                "Only a dead delivery can be replayed; this one is still "
                f"{original.status}."
            )
        connection = connections.runtime_connection(original.connection_id)
        if connection is None or connection.is_disabled:
            raise IntegrationConflict("The connection for this delivery is disabled.")
        if not connection.is_active and original.origin != DeliveryOrigin.PING.value:
            raise IntegrationConflict("Activate the connection before replaying.")
        event = routed_event(original.event_id)
        if event is None:  # pragma: no cover - PROTECT keeps it while referenced
            raise IntegrationConflict("The original event is no longer retained.")

        now = timezone.now()
        row = _new_delivery(
            event=event,
            connection_id=original.connection_id,
            origin=DeliveryOrigin.REPLAY,
            now=now,
            capability=original.capability,
        )
        row.replay_of_id = original.id
        row.requested_by_id = ctx.user_id
        row.automation_rule_id = original.automation_rule_id
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            raise IntegrationConflict(
                "A replay of this delivery is already in progress."
            ) from None
        _schedule(ctx.organization_id, [row.id], now=now)
        audit.record(
            ctx,
            "integration.delivery.replayed",
            resource_type="integration.delivery",
            resource_id=original.id,
            changes={
                "replay_delivery_id": str(row.id),
                "event_id": str(event.event_id),
                "connection_id": str(original.connection_id),
            },
        )
    metrics.increment("webhook_replays_total")
    scheduling.enqueue_after_commit(tasks.deliver, [row.id], ctx=ctx)
    return row.id


# --- retention (system) -----------------------------------------------------------


def prune(*, succeeded_before: Any, dead_before: Any, limit: int = 1000) -> int:
    """Delete old terminal deliveries (attempts cascade) - bounded batches.

    Succeeded deliveries are kept briefly for diagnosis; dead ones longer,
    because they are what an administrator replays (Tasks 25, 76).
    """
    total = 0
    for status, before in (
        (DeliveryStatus.SUCCEEDED.value, succeeded_before),
        (DeliveryStatus.DEAD.value, dead_before),
    ):
        while True:
            ids = list(
                Delivery.objects.filter(status=status, created_at__lt=before)
                .order_by("created_at")
                .values_list("id", flat=True)[:limit]
            )
            if not ids:
                break
            # A replay points at its original with SET NULL; clearing it
            # first keeps the delete a single, predictable statement.
            Delivery.objects.filter(replay_of_id__in=ids).update(replay_of=None)
            deleted, _ = Delivery.objects.filter(id__in=ids).delete()
            total += deleted
            if len(ids) < limit:
                break
    return total


# --- internals ---------------------------------------------------------------------


def _new_delivery(
    *,
    event: RoutedEventDTO,
    connection_id: UUID,
    origin: DeliveryOrigin,
    now: Any,
    capability: str = DELIVER_EVENT,
    dead_reason: DeadReason | None = None,
) -> Delivery:
    delivery = Delivery(
        organization_id=event.organization_id,
        connection_id=connection_id,
        event_id=event.id,
        origin=origin.value,
        capability=capability,
        max_attempts=int(settings.WEBHOOK_MAX_ATTEMPTS),
        next_attempt_at=now,
        correlation_id=event.correlation_id,
    )
    if dead_reason is not None:
        delivery.status = DeliveryStatus.DEAD.value
        delivery.dead_reason = dead_reason.value
        delivery.dead_at = now
    return delivery


def _schedule(organization_id: UUID, delivery_ids: list[UUID], *, now: Any) -> None:
    """A timer per delivery, due after the grace period.

    The grace period is how long the after-commit enqueue has to pick the
    delivery up before the sweeper does. Both racing is harmless (the claim
    is exclusive); the grace just keeps the common case to one dispatch.
    """
    grace = timezone.timedelta(seconds=float(settings.WEBHOOK_DISPATCH_GRACE_S))
    scheduling.schedule_many(
        TIMER_KIND,
        [(organization_id, delivery_id) for delivery_id in delivery_ids],
        due_at=now + grace,
    )


def _connection(connection_id: Any) -> Any:
    try:
        key = UUID(str(connection_id))
    except ValueError:
        raise IntegrationNotFound(resource="connection") from None
    connection = connections.runtime_connection(key)
    if connection is None:
        raise IntegrationNotFound(resource="connection")
    return connection


def _throttle_ping(connection_id: UUID) -> None:
    """A test ping is an outbound request a user can trigger at will, so it
    is bounded per connection like any other externally driven action."""
    from workeasy360.contracts.errors import IntegrationRateLimited
    from workeasy360.platform.observability import throttle

    limit, window = settings.WEBHOOK_PING_RATE_LIMIT
    key = f"w360:webhook:ping:{connection_id}:{throttle.window_slot(window)}"
    if throttle.hit(key, window) > limit:
        raise IntegrationRateLimited(
            "Too many test events for this connection. Try again in a minute."
        )
