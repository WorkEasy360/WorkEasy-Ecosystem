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

from workeasy360.contracts.enums import OutboxStatus


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
