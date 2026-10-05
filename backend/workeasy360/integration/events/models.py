"""Transactional outbox and consumer dedupe.

The problem (ADR-008): writing to the database and publishing to a broker are
two systems with no distributed transaction between them.

- Commit then publish -> a crash between the two loses the event permanently.
- Publish then commit -> a rollback emits an event for work that never
  happened.

Both failures are silent and both corrupt business state in ways discovered
weeks later. The solution is to write the event **into the same transaction**
as the business change, and relay it asynchronously. Atomicity is free because
it is the database's own atomicity.

``OutboxEvent`` is deliberately **not** a TenantScopedModel. It is written by
the relay across every tenant and must be claimable without a tenant context;
it carries ``organization_id`` as a plain column and is protected by the fact
that only the relay touches it.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from workeasy360.contracts.enums import OutboxStatus, StrEnum
from workeasy360.platform.tenancy.models import TenantScopedModel


class OutboxEventQuerySet(models.QuerySet):
    def claimable(self) -> OutboxEventQuerySet:
        return self.filter(
            status__in=[OutboxStatus.PENDING.value, OutboxStatus.FAILED.value],
            available_at__lte=timezone.now(),
        )


class OutboxEvent(models.Model):
    """A domain event awaiting publication.

    ``id`` is a BigAutoField rather than a UUID because the relay drains in
    insertion order, and a monotonic integer gives per-aggregate ordering for
    free. ``event_id`` is the UUID consumers use as an idempotency key.
    """

    id = models.BigAutoField(primary_key=True)
    event_id = models.UUIDField(unique=True, default=uuid.uuid4)
    event_type = models.CharField(max_length=128, db_index=True)
    event_version = models.IntegerField(default=1)

    organization_id = models.UUIDField(db_index=True)
    aggregate_type = models.CharField(max_length=128)
    #: The broker partition key, which is what gives per-aggregate ordering.
    aggregate_id = models.CharField(max_length=128, db_index=True)

    #: The full envelope, so replay needs no reconstruction and a
    #: dead-lettered event is self-describing.
    envelope = models.JSONField()

    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in OutboxStatus],
        default=OutboxStatus.PENDING.value,
    )
    attempts = models.IntegerField(default=0)
    #: Backoff target. The claim query filters on it, so a failing event stops
    #: being retried immediately without blocking anything behind it.
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    correlation_id = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = OutboxEventQuerySet.as_manager()

    class Meta:
        db_table = "events_outbox"
        indexes = [
            # Partial index on exactly the claim predicate. Without it the
            # relay degrades as published rows accumulate, because the planner
            # would scan them repeatedly looking for the few pending ones.
            models.Index(
                fields=["status", "available_at", "id"],
                name="ix_outbox_claim",
                condition=models.Q(status__in=["pending", "failed"]),
            ),
            models.Index(
                fields=["organization_id", "event_type"], name="ix_outbox_org_type"
            ),
            models.Index(fields=["published_at"], name="ix_outbox_published"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.event_type} #{self.id} [{self.status}]"

    @property
    def age_seconds(self) -> float:
        return (timezone.now() - self.created_at).total_seconds()


class ProcessedEvent(models.Model):
    """Consumer-side dedupe marker.

    Delivery is at-least-once; exactly-once does not exist across a database
    and a broker, and pretending otherwise produces systems that lose messages
    in the gaps. Consumers are idempotent instead.

    The marker is inserted **in the same transaction as the effect**. If the
    handler fails, the marker rolls back too, so the retry genuinely retries.
    Inserting it first in a separate transaction produces the classic bug
    where a crash mid-handler permanently marks an event processed that never
    was (07-cross-product-integration.md section 3.5).
    """

    id = models.BigAutoField(primary_key=True)
    consumer = models.CharField(max_length=128)
    event_id = models.UUIDField()
    processed_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(
        max_length=16,
        choices=[("applied", "Applied"), ("skipped", "Skipped"), ("failed", "Failed")],
        default="applied",
    )

    class Meta:
        db_table = "events_processed"
        constraints = [
            # Each (consumer, event) pair is independent, so three consumers
            # of the same event retry independently.
            models.UniqueConstraint(
                fields=["consumer", "event_id"], name="uq_processed_consumer_event"
            )
        ]
        indexes = [models.Index(fields=["event_id"], name="ix_processed_event")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.consumer} handled {self.event_id}"


class DeadLetterEvent(models.Model):
    """An event that exhausted its retries or failed permanently.

    Keeps the full envelope so replay needs no reconstruction. ``dlq_depth``
    above zero always needs a human: business events are being lost.
    """

    id = models.BigAutoField(primary_key=True)
    event_id = models.UUIDField(db_index=True)
    event_type = models.CharField(max_length=128, db_index=True)
    organization_id = models.UUIDField(db_index=True)
    envelope = models.JSONField()
    consumer = models.CharField(max_length=128, blank=True, default="")
    attempts = models.IntegerField(default=0)
    error = models.TextField(blank=True, default="")
    failed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    replayed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "events_dead_letter"
        indexes = [
            models.Index(fields=["event_type", "failed_at"], name="ix_dlq_type_time"),
            models.Index(fields=["replayed_at"], name="ix_dlq_replayed"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.event_type} #{self.event_id} (dead-lettered)"


# --- routable events (Phase 6) ----------------------------------------------


class RoutingStatus(StrEnum):
    #: Accepted; subscriptions and automation rules not yet matched.
    PENDING = "pending"
    #: Matched and fanned out. Terminal.
    ROUTED = "routed"
    #: Created for exactly one consumer - a connection's test ping, a
    #: schedule firing - and never fanned out to subscribers. Terminal.
    DIRECT = "direct"


class RoutedEvent(TenantScopedModel):
    """One event accepted into the integration platform for routing.

    The shared record every webhook delivery and automation execution points
    at, so a fan-out to 500 subscriptions stores the payload once, not 500
    times. Also the inbound **idempotency ledger** (Task 10): (organization,
    source, event_id) is unique, so a product retrying the same event gets
    the original answer and no second processing chain - decided by the
    database, not by a read-then-write.

    Tenant-scoped, unlike OutboxEvent: this row holds a tenant's business
    payload and customers read about it, so it gets RLS like every other
    tenant table. Cross-tenant work reaches it only through the scheduling
    timer, which carries its id and nothing else.

    **The payload** is the product's own opaque, bounded JSON (at most
    INTEGRATION_EVENT_MAX_BYTES), validated for shape and size but never
    interpreted. It is not field-level encrypted - see
    docs/phase-6/06-security.md section 9 for that decision - and it is
    pruned on a fixed retention schedule.
    """

    #: The producer's id for the event. Its idempotency key.
    event_id = models.UUIDField()
    #: Who published it: product.<code> for a product, platform for
    #: the platform's own integration events. Part of the idempotency key, so
    #: two products that happen to pick the same UUID cannot collide.
    source = models.CharField(max_length=64)
    event_type = models.CharField(max_length=128)
    event_version = models.PositiveSmallIntegerField()
    occurred_at = models.DateTimeField()
    correlation_id = models.UUIDField()
    #: The event that caused this one, when the producer said so and the
    #: platform could confirm it (same organization).
    causation_id = models.UUIDField(null=True, blank=True)
    #: How many automation-driven hops led here. Derived from the platform's
    #: own record of the causing event - never taken from the producer - and
    #: the input to automation loop protection (Task 46).
    chain_depth = models.PositiveSmallIntegerField(default=0)
    payload = models.JSONField(default=dict)
    payload_bytes = models.PositiveIntegerField(default=0)
    #: SHA-256 over the canonical event. A replayed event id with a different
    #: fingerprint is a conflict, not a duplicate.
    fingerprint = models.CharField(max_length=64)
    routing_status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in RoutingStatus],
        default=RoutingStatus.PENDING.value,
    )
    routed_at = models.DateTimeField(null=True, blank=True)
    matched_deliveries = models.PositiveIntegerField(default=0)
    matched_rules = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "events_routed"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "source", "event_id"],
                name="uq_routed_event_source_id",
            ),
            # The target of the composite foreign keys from deliveries and
            # executions.
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_routed_event_id_org"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "created_at"], name="ix_routed_event_age"
            ),
            # Causation lookup: "which event is this one a consequence of?"
            models.Index(
                fields=["organization", "event_id"], name="ix_routed_event_event_id"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.event_type}.v{self.event_version} {self.event_id} [{self.routing_status}]"
