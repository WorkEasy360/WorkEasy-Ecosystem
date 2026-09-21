"""Notification intent and delivery records.

The shape of this module is determined by one rule: **an email provider
outage must never become request latency** (09-failure-resilience.md s3.3).

So the write path is: persist intent, queue delivery, return. Nothing is sent
inline, ever. A user action is never blocked on an email send, and a business
transaction stays committed even when delivery ultimately fails.

Separating ``Notification`` (what we intend to tell someone) from
``NotificationDelivery`` (each attempt on each channel) is what makes that
possible: the intent is durable the moment the transaction commits, and
delivery becomes an independently retryable, independently failable record.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from workeasy360.contracts.enums import DeliveryStatus, NotificationChannel
from workeasy360.platform.tenancy.models import TenantScopedModel


class Notification(TenantScopedModel):
    """The durable intent to notify someone."""

    template_key = models.CharField(max_length=128, db_index=True)

    recipient_membership = models.ForeignKey(
        "organizations.Membership",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    #: For recipients with no membership yet - an invitee, for example.
    recipient_email = models.EmailField(max_length=254, blank=True, default="")

    #: Data the publisher has already resolved. Notifications never calls back
    #: into a product to render a message: that would be an upward dependency
    #: and an N+1 across a module boundary (F-4).
    context = models.JSONField(default=dict)

    #: Derived from durable state, never random. A random key regenerated on
    #: retry is not a retry, it is a second notification.
    idempotency_key = models.CharField(max_length=255, blank=True, default="")

    created_at_utc = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications_notification"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uq_notification_org_idempotency",
            ),
            models.CheckConstraint(
                condition=models.Q(recipient_membership__isnull=False)
                | ~models.Q(recipient_email=""),
                name="ck_notification_has_recipient",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-created_at_utc"], name="ix_notif_org_time"
            ),
            models.Index(
                fields=["organization", "recipient_membership"],
                name="ix_notif_org_recipient",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.template_key} -> {self.recipient_membership_id or self.recipient_email}"


class NotificationDelivery(TenantScopedModel):
    """One delivery attempt record, per channel.

    A failure here is recorded rather than raised into the caller: the
    business transaction that triggered the notification has long since
    committed, and failing it retroactively is not possible or desirable.
    """

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="deliveries"
    )
    channel = models.CharField(
        max_length=16, choices=[(c.value, c.name.title()) for c in NotificationChannel]
    )
    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in DeliveryStatus],
        default=DeliveryStatus.PENDING.value,
        db_index=True,
    )

    attempts = models.IntegerField(default=0)
    #: Backoff target for the next attempt.
    available_at = models.DateTimeField(default=timezone.now, db_index=True)

    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications_delivery"
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "channel"],
                name="uq_delivery_notification_channel",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "status", "available_at"],
                name="ix_delivery_org_status",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.channel} delivery [{self.status}]"

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            DeliveryStatus.SENT.value,
            DeliveryStatus.DEAD_LETTERED.value,
            DeliveryStatus.SUPPRESSED.value,
        }


class NotificationPreference(TenantScopedModel):
    """Per-member channel preferences.

    Absence means "use the default", so a new channel does not require
    backfilling a row for every member before it can be used.
    """

    membership = models.ForeignKey(
        "organizations.Membership",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    template_key = models.CharField(max_length=128)
    channel = models.CharField(
        max_length=16, choices=[(c.value, c.name.title()) for c in NotificationChannel]
    )
    enabled = models.BooleanField(default=True)

    class Meta:
        db_table = "notifications_preference"
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "template_key", "channel"],
                name="uq_preference_member_template_channel",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "membership"], name="ix_pref_org_member")
        ]
