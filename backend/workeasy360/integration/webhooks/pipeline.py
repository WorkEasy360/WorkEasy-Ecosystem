"""The delivery pipeline: claim, send, record - never a transaction across
the network.

::

    tx 1 (org scope)   lock delivery -> gate checks -> claim attempt N
                       (status in_progress, lease, timer pushed past lease)
                       -> read event + this connection's secrets -> commit
    no transaction     connector builds request -> egress sends it
    tx 2 (org scope)   insert attempt N (unique) -> fenced update
                       "WHERE status='in_progress' AND attempts=N"
                       -> timer -> connection health -> commit

**Concurrency (Task 23).**

- *Two workers claim one delivery*: the first holds the row lock and moves
  it to ``in_progress`` with a live lease; the second finds that and returns
  ``in_flight`` without sending.
- *Worker dies after claiming*: the lease expires; the timer (pushed past
  the lease at claim time) fires; the next claim records attempt N as
  ``abandoned`` and claims N+1. Whether N reached the receiver is unknown -
  at-least-once.
- *Late acknowledgement*: a worker that outlived its lease finds either its
  attempt number already recorded as abandoned (unique violation) or the
  delivery on a later attempt (the fenced update matches no row). Either
  way its result is discarded, counted, and the delivery's state is the
  newer worker's.
- *Retry racing the original*: impossible by construction - a retry is a
  claim of attempt N+1, which requires attempt N to have been recorded or
  its lease to have expired.

**Suspension and circuit (Tasks 49, 54).** A delivery for a suspended
organization, a suspended connection or an open circuit is *held*: its
timer moves forward and no attempt is charged. Held deliveries resume when
the condition clears and expire (dead, ``expired``) if it lasts longer than
``WEBHOOK_DELIVERY_MAX_AGE_S`` - fail closed for new side effects, nothing
lost silently.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.values.backoff import equal_jitter
from workeasy360.integration.connectors import registry
from workeasy360.integration.connectors import selectors as connections
from workeasy360.integration.connectors import services as connection_services
from workeasy360.integration.connectors.contract import (
    DEAD,
    RETRY,
    SUCCEEDED,
    Connector,
    ConnectorConfigError,
    Invocation,
    Outcome,
    SecretMaterial,
    outcome_for_failure,
)
from workeasy360.integration.connectors.secrets import SecretUnavailable
from workeasy360.integration.egress import client as egress
from workeasy360.integration.egress.policy import EgressRejected
from workeasy360.integration.events.selectors import routed_event, webhook_body
from workeasy360.integration.scheduling import services as scheduling
from workeasy360.integration.webhooks.models import (
    AttemptOutcome,
    DeadReason,
    Delivery,
    DeliveryAttempt,
    DeliveryOrigin,
    DeliveryStatus,
)
from workeasy360.integration.webhooks.services import TIMER_KIND
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Claimed:
    delivery_id: UUID
    connection_id: UUID
    attempt: int
    max_attempts: int
    created_at: Any
    started_at: Any
    connector: Connector
    config: dict[str, Any]
    invocation: Invocation
    secrets: SecretMaterial


def run_delivery(delivery_id: Any) -> str:
    """Attempt one delivery once. Returns what happened, for logs and tests.

    Runs inside ``TenantTask``'s tenant context (Layer 2); each transaction
    below enters the organization's database scope itself (Layer 3), because
    no scope may stay open across the network call.
    """
    from workeasy360.platform.organizations.selectors import organization_status
    from workeasy360.platform.tenancy.context import current_tenant, tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    dispatched_ctx = current_tenant()
    organization_id = dispatched_ctx.organization_id
    try:
        key = UUID(str(delivery_id))
    except ValueError:
        return "missing"

    # The organization's status is read live, not taken from the dispatch-
    # time context: a delivery queued a second before the organization was
    # suspended must still not go out (fail closed for new side effects,
    # Task 54), and one queued before a reactivation must not stay blocked.
    # The live status also drives the R-10 write block for everything below.
    live = organization_status(organization_id)
    if live is None:
        scheduling.complete(TIMER_KIND, key)
        return "organization_gone"

    with tenant_context(dispatched_ctx.replace_status(live)):
        with organization_db_scope(organization_id):
            claimed = _claim(key, organization_id, writable=live.allows_write)
        if isinstance(claimed, str):
            return claimed

        outcome, duration_ms = _attempt(claimed)

        with organization_db_scope(organization_id):
            return _record(claimed, organization_id, outcome, duration_ms)


# --- transaction 1: claim ---------------------------------------------------


def _claim(delivery_id: UUID, organization_id: UUID, *, writable: bool) -> _Claimed | str:
    now = timezone.now()
    delivery = Delivery.objects.select_for_update().filter(id=delivery_id).first()
    if delivery is None:
        scheduling.complete(TIMER_KIND, delivery_id)
        return "missing"

    status = delivery.status_enum
    if status.is_terminal:
        scheduling.complete(TIMER_KIND, delivery_id)
        return status.value
    if (
        status is DeliveryStatus.IN_PROGRESS
        and delivery.lease_expires_at is not None
        and delivery.lease_expires_at > now
    ):
        # Another worker holds a live claim. Its lease and timer cover the
        # case where it never finishes.
        return "in_flight"
    if status is not DeliveryStatus.IN_PROGRESS and delivery.next_attempt_at > now:
        _hold(delivery, organization_id, until=delivery.next_attempt_at)
        return "not_due"

    if not writable:
        # Held before any tenant write: R-10 refuses writes for a read-only
        # organization, and holding touches only the timer.
        _hold(delivery, organization_id, seconds=float(settings.WEBHOOK_HOLD_RECHECK_S))
        metrics.increment("webhook_deliveries_held_total", reason="organization")
        return "held_organization"

    abandoned = status is DeliveryStatus.IN_PROGRESS
    if abandoned:
        _record_abandoned(delivery, now)

    max_age = timezone.timedelta(seconds=int(settings.WEBHOOK_DELIVERY_MAX_AGE_S))
    if now - delivery.created_at > max_age:
        return _dead_now(delivery, DeadReason.EXPIRED, now)
    if abandoned and delivery.attempts >= delivery.max_attempts:
        return _dead_now(delivery, DeadReason.RETRIES_EXHAUSTED, now)

    connection = connections.runtime_connection(delivery.connection_id, lock=True)
    if connection is None or connection.is_disabled:
        return _dead_now(delivery, DeadReason.CONNECTION_DISABLED, now)
    if not connection.is_active and delivery.origin != DeliveryOrigin.PING.value:
        _hold(delivery, organization_id, seconds=float(settings.WEBHOOK_HOLD_RECHECK_S))
        metrics.increment("webhook_deliveries_held_total", reason="connection")
        return "held_connection"
    if connection.circuit_open_until is not None and connection.circuit_open_until > now:
        _hold(delivery, organization_id, until=connection.circuit_open_until, jitter=5.0)
        metrics.increment("webhook_deliveries_held_total", reason="circuit")
        return "held_circuit"

    # Bulkheads (Task 19). Per connection: one slow endpoint occupies at most
    # this many workers, whatever its backlog; half-open circuit, one. Per
    # organization: one organization's many slow endpoints cannot occupy
    # every delivery worker, which are shared by all organizations.
    if not _slot_available(delivery, connection, organization_id, now):
        # Parked, not re-polled: a finishing attempt pulls the next waiting
        # delivery forward (_pull_next), so this timer is only the fallback.
        # Re-polling a backlog every few seconds would spend the shared
        # due-work sweeper - every organization's retries and schedules -
        # on one organization's queue.
        hold = float(settings.WEBHOOK_THROTTLE_HOLD_S)
        _hold(delivery, organization_id, seconds=hold, jitter=hold)
        metrics.increment("webhook_deliveries_held_total", reason="concurrency")
        return "throttled"

    connector = registry.get_connector(connection.connector_key)
    event = routed_event(delivery.event_id)
    if connector is None or event is None:
        return _dead_now(delivery, DeadReason.UNSENDABLE, now)
    try:
        material = (
            connections.secret_material(connection.id)
            if connector.generates_secret
            else SecretMaterial()
        )
    except SecretUnavailable:
        logger.error(
            "webhook.delivery.secret_unavailable",
            delivery_id=str(delivery.id),
            connection_id=str(connection.id),
        )
        return _dead_now(delivery, DeadReason.UNSENDABLE, now)

    attempt = delivery.attempts + 1
    lease_until = now + timezone.timedelta(seconds=int(settings.WEBHOOK_LEASE_S))
    Delivery.objects.filter(id=delivery.id).update(
        status=DeliveryStatus.IN_PROGRESS.value,
        attempts=attempt,
        lease_expires_at=lease_until,
        last_attempt_at=now,
        updated_at=now,
    )
    # If this worker dies, the timer brings the delivery back after the lease.
    scheduling.schedule(
        TIMER_KIND,
        organization_id=organization_id,
        target_id=delivery.id,
        due_at=lease_until + timezone.timedelta(seconds=5),
    )

    automation = None
    if delivery.automation_rule_id is not None:
        automation = {
            "rule_id": str(delivery.automation_rule_id),
            "execution_id": str(delivery.automation_execution_id or ""),
        }
    body = webhook_body(event, delivery_id=delivery.id, automation=automation)
    return _Claimed(
        delivery_id=delivery.id,
        connection_id=connection.id,
        attempt=attempt,
        max_attempts=delivery.max_attempts,
        created_at=delivery.created_at,
        started_at=now,
        connector=connector,
        config=connection.config,
        invocation=Invocation(
            capability=delivery.capability,
            delivery_id=str(delivery.id),
            attempt=attempt,
            timestamp=int(now.timestamp()),
            event_id=str(event.event_id),
            event_type=event.event_type,
            correlation_id=str(event.correlation_id),
            body=body,
        ),
        secrets=material,
    )


# --- no transaction: the network ---------------------------------------------


def _attempt(claimed: _Claimed) -> tuple[Outcome, int]:
    import time

    started = time.monotonic()
    try:
        request = claimed.connector.build_request(
            claimed.config, claimed.invocation, claimed.secrets
        )
        response = egress.send(request)
        outcome = claimed.connector.interpret(claimed.invocation.capability, response)
    except ConnectorConfigError as exc:
        outcome = Outcome(DEAD, "connector_rejected", excerpt=exc.message[:200])
    except (EgressRejected, egress.EgressFailure) as exc:
        outcome = outcome_for_failure(exc)
        if isinstance(exc, EgressRejected):
            logger.warning(
                "webhook.delivery.destination_blocked",
                delivery_id=str(claimed.delivery_id),
                reason=exc.reason,
            )
    except Exception as exc:  # pragma: no cover - a connector bug
        logger.error(
            "webhook.delivery.connector_error",
            delivery_id=str(claimed.delivery_id),
            error_type=type(exc).__name__,
        )
        outcome = Outcome(RETRY, "internal_error")
    duration_ms = int((time.monotonic() - started) * 1000)
    metrics.observe(
        "webhook_delivery_duration_seconds",
        duration_ms / 1000,
        connector=claimed.connector.key,
    )
    return outcome, duration_ms


# --- transaction 2: record ------------------------------------------------------


def _record(
    claimed: _Claimed, organization_id: UUID, outcome: Outcome, duration_ms: int
) -> str:
    now = timezone.now()
    attempt_outcome = {
        SUCCEEDED: AttemptOutcome.SUCCEEDED,
        DEAD: AttemptOutcome.DEAD,
        RETRY: AttemptOutcome.RETRY,
    }[outcome.result]

    stale = False
    try:
        with transaction.atomic():
            DeliveryAttempt(
                organization_id=organization_id,
                delivery_id=claimed.delivery_id,
                attempt_number=claimed.attempt,
                outcome=attempt_outcome.value,
                category=outcome.category[:48],
                http_status=outcome.http_status,
                duration_ms=duration_ms,
                started_at=claimed.started_at,
                response_excerpt=outcome.excerpt[:200],
            ).save()
    except IntegrityError:
        # This attempt was already recorded - as abandoned, by a worker that
        # found our lease expired. Our result is late and discarded.
        stale = True

    fields: dict[str, Any] = {
        "lease_expires_at": None,
        "last_http_status": outcome.http_status,
        "last_category": outcome.category[:48],
        "updated_at": now,
    }
    next_attempt_at = None
    if outcome.result == SUCCEEDED:
        fields.update(status=DeliveryStatus.SUCCEEDED.value, succeeded_at=now)
    elif outcome.result == DEAD:
        fields.update(
            status=DeliveryStatus.DEAD.value,
            dead_at=now,
            dead_reason=DeadReason.REJECTED.value,
        )
    else:
        next_attempt_at = now + timezone.timedelta(
            seconds=_retry_delay(claimed.attempt, outcome)
        )
        max_age = timezone.timedelta(seconds=int(settings.WEBHOOK_DELIVERY_MAX_AGE_S))
        if claimed.attempt >= claimed.max_attempts:
            fields.update(
                status=DeliveryStatus.DEAD.value,
                dead_at=now,
                dead_reason=DeadReason.RETRIES_EXHAUSTED.value,
            )
            next_attempt_at = None
        elif next_attempt_at - claimed.created_at > max_age:
            fields.update(
                status=DeliveryStatus.DEAD.value,
                dead_at=now,
                dead_reason=DeadReason.EXPIRED.value,
            )
            next_attempt_at = None
        else:
            fields.update(
                status=DeliveryStatus.RETRY.value, next_attempt_at=next_attempt_at
            )

    updated = (
        0
        if stale
        else Delivery.objects.filter(
            id=claimed.delivery_id,
            status=DeliveryStatus.IN_PROGRESS.value,
            attempts=claimed.attempt,
        ).update(**fields)
    )
    if not updated:
        metrics.increment("webhook_late_completions_total")
        logger.warning(
            "webhook.delivery.late_completion",
            delivery_id=str(claimed.delivery_id),
            attempt=claimed.attempt,
        )
        return "stale"

    if next_attempt_at is None:
        scheduling.complete(TIMER_KIND, claimed.delivery_id)
    else:
        scheduling.schedule(
            TIMER_KIND,
            organization_id=organization_id,
            target_id=claimed.delivery_id,
            due_at=next_attempt_at,
        )
    connection_services.record_delivery_result(
        claimed.connection_id, succeeded=outcome.result == SUCCEEDED
    )
    _pull_next(organization_id, now)

    final = fields["status"]
    metrics.increment("webhook_delivery_attempts_total", outcome=attempt_outcome.value)
    metrics.increment("webhook_deliveries_total", result=final)
    if final == DeliveryStatus.DEAD.value:
        metrics.increment("webhook_dead_letter_total", reason=fields["dead_reason"])
    if final == DeliveryStatus.RETRY.value:
        metrics.increment("webhook_retries_total", category=outcome.category[:48])
    logger.info(
        "webhook.delivery.attempted",
        delivery_id=str(claimed.delivery_id),
        attempt=claimed.attempt,
        status=final,
        category=outcome.category,
        http_status=outcome.http_status,
        duration_ms=duration_ms,
    )
    return final


def _retry_delay(attempt: int, outcome: Outcome) -> float:
    """Exponential backoff with equal jitter, floored by a clamped
    ``Retry-After`` (Tasks 20, 21).

    Equal rather than full jitter because the thing being retried is a
    third party's endpoint that just failed: the delay must never collapse
    to near zero. ``Retry-After`` may lengthen the wait, never beyond
    ``WEBHOOK_RETRY_AFTER_MAX_S`` - an endpoint cannot park a delivery for
    days by asking to.
    """
    delay = equal_jitter(
        attempt - 1,
        base=float(settings.WEBHOOK_RETRY_BASE_S),
        cap=float(settings.WEBHOOK_RETRY_CAP_S),
    )
    if outcome.retry_after_s is not None:
        delay = max(
            delay, min(outcome.retry_after_s, float(settings.WEBHOOK_RETRY_AFTER_MAX_S))
        )
    return delay


# --- internals ----------------------------------------------------------------

#: Namespace of the advisory lock that serialises claims within one
#: organization (the first key of the two-key form; the second is the
#: organization). Transaction-scoped, so safe behind PgBouncer.
_CLAIM_LOCK_NAMESPACE: Final = 0x57360D1  # "W360 delivery"


def _connection_cap(connection: Any) -> int:
    if connection.consecutive_failures >= int(
        settings.INTEGRATION_CIRCUIT_FAILURE_THRESHOLD
    ):
        return 1  # half-open: a single probe
    return int(settings.WEBHOOK_MAX_INFLIGHT_PER_CONNECTION)


def _slot_available(
    delivery: Delivery, connection: Any, organization_id: UUID, now: Any
) -> bool:
    """Whether this claim fits under both bulkheads.

    The connection's count is exact because the caller holds the
    connection's row lock. The organization's count spans connections, so
    claims within one organization are serialised on an advisory lock for
    the rest of the claim transaction (milliseconds) - otherwise claims for
    two of its connections could each read a count below the cap and both
    proceed.
    """
    from django.db import connection as db

    live = Delivery.objects.filter(
        status=DeliveryStatus.IN_PROGRESS.value, lease_expires_at__gt=now
    ).exclude(id=delivery.id)
    if live.filter(connection_id=connection.id).count() >= _connection_cap(connection):
        return False
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
            [_CLAIM_LOCK_NAMESPACE, str(organization_id)],
        )
    return live.filter(organization_id=organization_id).count() < int(
        settings.WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION
    )


def _pull_next(organization_id: UUID, now: Any) -> None:
    """Hand the slot this attempt just freed to the next waiting delivery.

    Throttled deliveries are parked for minutes (``_claim``), so without
    this a backlog would drain only as fast as parked timers came due. The
    candidate is the organization's least recently touched due delivery on
    a connection that has room - round-robin across its connections, so one
    busy endpoint cannot keep the organization's slots to itself. Touching
    it (``updated_at``) moves it to the back, so the next pull picks a
    different one; ``SKIP LOCKED`` passes over one a claim holds right now.
    Losing the enqueue loses nothing: the parked timer is still there.
    """
    from django.db.models import Count, Q

    from workeasy360.integration.webhooks import tasks
    from workeasy360.platform.tenancy.context import current_tenant

    full = (
        Delivery.objects.filter(
            status=DeliveryStatus.IN_PROGRESS.value, lease_expires_at__gt=now
        )
        .values("connection_id")
        .annotate(live=Count("id"))
        .filter(live__gte=int(settings.WEBHOOK_MAX_INFLIGHT_PER_CONNECTION))
        .values("connection_id")
    )
    candidate = (
        Delivery.objects.filter(
            status__in=[DeliveryStatus.PENDING.value, DeliveryStatus.RETRY.value],
            next_attempt_at__lte=now,
        )
        .filter(
            Q(connection_id__in=connections.ready_connection_ids(now))
            | Q(origin=DeliveryOrigin.PING.value)
        )
        .exclude(connection_id__in=full)
        .order_by("updated_at", "id")
        .select_for_update(skip_locked=True, of=("self",))
        .values_list("id", flat=True)
        .first()
    )
    if candidate is None:
        return
    Delivery.objects.filter(id=candidate).update(updated_at=now)
    scheduling.schedule(
        TIMER_KIND, organization_id=organization_id, target_id=candidate, due_at=now
    )
    scheduling.enqueue_after_commit(tasks.deliver, [candidate], ctx=current_tenant())


def _hold(
    delivery: Delivery,
    organization_id: UUID,
    *,
    until: Any = None,
    seconds: float = 0.0,
    jitter: float = 0.0,
) -> None:
    """Move the timer forward without charging an attempt."""
    due = until or timezone.now() + timezone.timedelta(seconds=seconds)
    if jitter:
        due = due + timezone.timedelta(seconds=random.uniform(0, jitter))  # noqa: S311 - scheduling jitter
    scheduling.schedule(
        TIMER_KIND, organization_id=organization_id, target_id=delivery.id, due_at=due
    )


def _dead_now(delivery: Delivery, reason: DeadReason, now: Any) -> str:
    Delivery.objects.filter(id=delivery.id).update(
        status=DeliveryStatus.DEAD.value,
        dead_at=now,
        dead_reason=reason.value,
        lease_expires_at=None,
        updated_at=now,
    )
    scheduling.complete(TIMER_KIND, delivery.id)
    metrics.increment("webhook_deliveries_total", result=DeliveryStatus.DEAD.value)
    metrics.increment("webhook_dead_letter_total", reason=reason.value)
    logger.info(
        "webhook.delivery.dead", delivery_id=str(delivery.id), reason=reason.value
    )
    return DeliveryStatus.DEAD.value


def _record_abandoned(delivery: Delivery, now: Any) -> None:
    """Attempt ``delivery.attempts`` never reported back: record it as
    abandoned (once - the unique constraint makes a second record a no-op)."""
    try:
        with transaction.atomic():
            DeliveryAttempt(
                organization_id=delivery.organization_id,
                delivery_id=delivery.id,
                attempt_number=delivery.attempts,
                outcome=AttemptOutcome.ABANDONED.value,
                category="abandoned",
                started_at=delivery.last_attempt_at or now,
            ).save()
        metrics.increment("webhook_delivery_attempts_total", outcome="abandoned")
    except IntegrityError:
        pass
