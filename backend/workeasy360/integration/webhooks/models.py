"""Subscriptions, deliveries and attempts.

All three tenant-owned (RLS, fail-closed manager, composite foreign keys).
The foreign keys into ``connectors`` and ``events`` are intra-``integration``
references permitted by ADR-024; each is also a composite ``(id,
organization_id)`` key, so a delivery for organization A pointing at
organization B's connection or event is unrepresentable, not merely
unexpected (Task 57/58).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from workeasy360.contracts.enums import StrEnum
from workeasy360.platform.tenancy.models import TenantScopedModel


class DeliveryStatus(StrEnum):
    """The delivery state machine (Task 22).

    ::

        pending ──claim──> in_progress ──2xx──────────────> succeeded
           ^                   │  │ ──4xx/3xx/blocked/────> dead
           │                   │  │   exhausted/expired
           └──── retry <───────┘  │ ──408/429/5xx/network─> retry
                                  └─ lease expired ──────> in_progress
                                     (re-claimed; the abandoned attempt
                                      is recorded, attempts + 1)

    ``succeeded`` and ``dead`` are terminal. A dead delivery is never
    revived: a manual replay creates a *new* delivery (``replay_of``) so the
    history of the original is never rewritten (Task 26).
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RETRY = "retry"
    SUCCEEDED = "succeeded"
    DEAD = "dead"

    @property
    def is_terminal(self) -> bool:
        return self in (DeliveryStatus.SUCCEEDED, DeliveryStatus.DEAD)


#: Statuses that still need work. Also the partial-index predicate.
OPEN_STATUSES = (
    DeliveryStatus.PENDING.value,
    DeliveryStatus.IN_PROGRESS.value,
    DeliveryStatus.RETRY.value,
)


class DeadReason(StrEnum):
    #: The receiver answered with a status retrying cannot fix (most 4xx),
    #: or a redirect (never followed), or the destination was refused by
    #: the egress policy.
    REJECTED = "rejected"
    #: ``max_attempts`` retryable failures.
    RETRIES_EXHAUSTED = "retries_exhausted"
    #: Not delivered within ``WEBHOOK_DELIVERY_MAX_AGE_S`` of being created -
    #: e.g. held throughout a long suspension.
    EXPIRED = "expired"
    #: The connection was disabled before the delivery could be attempted.
    CONNECTION_DISABLED = "connection_disabled"
    #: The connection already had its maximum of undelivered deliveries
    #: (Task 50). Recorded, visible and replayable - never silently dropped.
    BACKLOG_LIMIT = "backlog_limit"
    #: The connection's secret could not be opened, or its connector is no
    #: longer registered. An operator problem, never sent unsigned.
    UNSENDABLE = "unsendable"


class DeliveryOrigin(StrEnum):
    #: Fanned out from an event matching a subscription.
    SUBSCRIPTION = "subscription"
    #: Created by an automation action.
    AUTOMATION = "automation"
    #: A connection's test event.
    PING = "ping"
    #: An administrator's manual replay of a dead delivery.
    REPLAY = "replay"


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRY = "retry"
    DEAD = "dead"
    #: The worker holding the claim vanished (killed, OOM, lease expired);
    #: whether the request reached the receiver is unknown.
    ABANDONED = "abandoned"


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(m.value, m.name.title()) for m in enum_cls]


class EventSubscription(TenantScopedModel):
    """ "Send events matching ``event_filter`` to ``connection``."

    A filter is one exact versioned type or one product's whole namespace
    (``contracts.events.namespaces``) - which is what makes matching a single
    indexed ``event_filter IN (<= 2 values)`` query at any scale (Task 73).
    """

    connection = models.ForeignKey(
        "connectors.Connection",
        on_delete=models.CASCADE,
        related_name="+",
        db_index=False,
    )
    event_filter = models.CharField(max_length=160)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=False,
    )

    class Meta:
        db_table = "webhooks_subscription"
        constraints = [
            # Idempotent subscription: the same filter twice on one connection
            # is one row, however many concurrent requests ask for it.
            models.UniqueConstraint(
                fields=["connection", "event_filter"], name="uq_subscription_filter"
            ),
        ]
        indexes = [
            # The routing query: which connections want this event?
            models.Index(
                fields=["organization", "event_filter"], name="ix_subscription_match"
            ),
            models.Index(
                fields=["organization", "connection"], name="ix_subscription_connection"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.event_filter} -> {self.connection_id}"


class Delivery(TenantScopedModel):
    """One logical delivery of one event to one connection."""

    connection = models.ForeignKey(
        "connectors.Connection",
        on_delete=models.CASCADE,
        related_name="+",
        db_index=False,
    )
    #: PROTECT here and ``ON DELETE NO ACTION`` in the database: an event is
    #: never pruned out from under a delivery that still references it.
    event = models.ForeignKey(
        "events.RoutedEvent", on_delete=models.PROTECT, related_name="+"
    )
    origin = models.CharField(max_length=16, choices=_choices(DeliveryOrigin))
    #: Which connector capability to invoke (``deliver_event`` today).
    capability = models.CharField(max_length=48)

    #: Set when an automation action created this delivery. A soft reference
    #: (automation sits above webhooks), made idempotent by the unique
    #: constraint below: one delivery per (execution, action), however many
    #: times the execution is retried (Task 37).
    automation_execution_id = models.UUIDField(null=True, blank=True)
    automation_rule_id = models.UUIDField(null=True, blank=True)
    action_key = models.CharField(max_length=16, blank=True, default="")

    #: The dead delivery this one replays (Task 26).
    replay_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=False,
    )
    #: Who asked, for a ping or a replay. Deliveries the platform creates by
    #: itself have no requester.
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=False,
    )

    status = models.CharField(
        max_length=16,
        choices=_choices(DeliveryStatus),
        default=DeliveryStatus.PENDING.value,
    )
    #: Attempts *claimed* so far. Also the fencing token: a worker records a
    #: result only while the delivery is still at the attempt it claimed.
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField()
    next_attempt_at = models.DateTimeField()
    #: While ``in_progress``: when the claiming worker's lease ends. After it,
    #: the delivery may be re-claimed and the attempt is recorded abandoned.
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    last_category = models.CharField(max_length=48, blank=True, default="")
    succeeded_at = models.DateTimeField(null=True, blank=True)
    dead_at = models.DateTimeField(null=True, blank=True)
    #: Why a dead delivery is dead - one of ``DeadReason``. What an operator
    #: reads before deciding whether a replay can possibly help.
    dead_reason = models.CharField(max_length=32, blank=True, default="")
    correlation_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "webhooks_delivery"
        constraints = [
            # Fan-out is idempotent: one subscription delivery per (event,
            # connection), however many times routing runs for the event.
            models.UniqueConstraint(
                fields=["event", "connection"],
                condition=models.Q(origin="subscription"),
                name="uq_delivery_fanout",
            ),
            models.UniqueConstraint(
                fields=["automation_execution_id", "action_key"],
                condition=models.Q(automation_execution_id__isnull=False),
                name="uq_delivery_automation_action",
            ),
            # At most one replay of a dead delivery in flight at a time.
            models.UniqueConstraint(
                fields=["replay_of"],
                condition=models.Q(status__in=list(OPEN_STATUSES)),
                name="uq_delivery_one_open_replay",
            ),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_delivery_id_org"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-created_at", "id"], name="ix_delivery_list"
            ),
            models.Index(
                fields=["organization", "connection", "-created_at", "id"],
                name="ix_delivery_by_connection",
            ),
            # Only the open ones: the backlog count and the in-flight count
            # per connection read this, and it stays small however much
            # history accumulates.
            models.Index(
                fields=["organization", "connection", "status"],
                condition=models.Q(status__in=list(OPEN_STATUSES)),
                name="ix_delivery_open",
            ),
            # Retention pruning and the status filter.
            models.Index(
                fields=["organization", "status", "created_at"],
                name="ix_delivery_status_age",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"delivery {self.id} [{self.status}, attempt {self.attempts}]"

    @property
    def status_enum(self) -> DeliveryStatus:
        return DeliveryStatus(self.status)


class DeliveryAttempt(TenantScopedModel):
    """One attempt, recorded once and never changed (a trigger refuses
    updates). Pruned with its delivery."""

    delivery = models.ForeignKey(
        Delivery, on_delete=models.CASCADE, related_name="+", db_index=False
    )
    attempt_number = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=16, choices=_choices(AttemptOutcome))
    #: Bounded code: ``http_2xx``, ``http_5xx``, ``http_429``, ``read_timeout``,
    #: ``dns_failure``, ``tls_error``, ``destination_blocked``, ...
    category = models.CharField(max_length=48)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField()
    #: At most ``contract.EXCERPT_CHARS`` printable characters of the
    #: response, for diagnosis. Never the request, never a header.
    response_excerpt = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "webhooks_attempt"
        constraints = [
            # Exactly one record per attempt number: a late worker whose
            # attempt was already recorded as abandoned cannot add a second.
            models.UniqueConstraint(
                fields=["delivery", "attempt_number"], name="uq_attempt_number"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"attempt {self.attempt_number} of {self.delivery_id}: {self.outcome}"
