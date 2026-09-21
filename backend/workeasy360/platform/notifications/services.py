"""Notification services: persist intent, queue delivery, return."""

from __future__ import annotations

import random
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.enums import DeliveryStatus, NotificationChannel
from workeasy360.contracts.ports.notifications import (
    NotificationRequest,
    RenderedMessage,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import (
    NOTIFICATION_FAILED,
    NOTIFICATION_SENT,
    metrics,
)

logger = get_logger(__name__)


def notify(request: NotificationRequest) -> UUID:
    """Persist the notification and queue delivery. Returns its id.

    Never sends inline. The transaction commits the *intent*; delivery happens
    on a worker. This is what keeps a provider outage off the request path
    (09-failure-resilience.md section 3.3).

    Queuing happens after commit via ``on_commit`` - safe here specifically
    because the Notification row is already durable, so a lost callback
    delays a send rather than losing it, and a sweeper re-queues pending
    deliveries. (Contrast the outbox, where ``on_commit`` would be wrong
    because there is no durable record to recover from.)
    """
    from workeasy360.platform.notifications.models import (
        Notification,
        NotificationDelivery,
    )
    from workeasy360.platform.notifications.tasks import deliver_notification

    with transaction.atomic():
        try:
            # Savepoint: a unique-constraint violation aborts the whole
            # PostgreSQL transaction, so catching IntegrityError without one
            # would leave the connection unusable and take the caller's
            # business write down with it.
            with transaction.atomic():
                notification = Notification.objects.create(
                    organization_id=request.organization_id,
                    template_key=request.template_key,
                    recipient_membership_id=request.recipient_membership_id,
                    recipient_email=request.recipient_email or "",
                    context=dict(request.context),
                    idempotency_key=request.idempotency_key or "",
                )
        except IntegrityError:
            # The unique constraint on (organization, idempotency_key) makes
            # a duplicate request a no-op rather than a second email.
            existing = Notification.objects.filter(
                organization_id=request.organization_id,
                idempotency_key=request.idempotency_key,
            ).first()
            if existing is not None:
                logger.info(
                    "notification.duplicate_suppressed",
                    template=request.template_key,
                )
                return existing.id
            raise

        deliveries = [
            NotificationDelivery(
                organization_id=request.organization_id,
                notification=notification,
                channel=str(channel),
                status=DeliveryStatus.PENDING.value,
            )
            for channel in request.channels
        ]
        NotificationDelivery.objects.bulk_create(deliveries)

        delivery_ids = [str(d.id) for d in deliveries]
        transaction.on_commit(lambda: _enqueue(deliver_notification, delivery_ids))

    logger.info(
        "notification.queued",
        notification_id=str(notification.id),
        template=request.template_key,
        channels=[str(c) for c in request.channels],
    )
    return notification.id


def _enqueue(task: Any, delivery_ids: list[str]) -> None:
    """Dispatch delivery tasks, tolerating a broker outage.

    If the broker is down, the deliveries stay ``pending`` and the sweeper
    picks them up. The user's action has already succeeded; failing it here
    because a Tier-3 notification could not be *queued* would invert the
    dependency tiers (09-failure-resilience.md section 2).
    """
    for delivery_id in delivery_ids:
        try:
            task.delay(delivery_id)
        except Exception as exc:
            logger.warning(
                "notification.enqueue_failed",
                delivery_id=delivery_id,
                error_type=type(exc).__name__,
            )


def attempt_delivery(delivery_id: UUID | str) -> str:
    """Attempt one delivery. Returns the resulting status.

    Records the outcome rather than raising, so that a permanently
    undeliverable address is a recorded failure rather than an exception
    bubbling through a worker.
    """
    from workeasy360.platform.notifications.channels import get_channel
    from workeasy360.platform.notifications.models import NotificationDelivery
    from workeasy360.platform.notifications.templates import render

    delivery = (
        NotificationDelivery.objects.select_related("notification")
        .filter(id=delivery_id)
        .first()
    )
    if delivery is None:
        return "missing"
    if delivery.is_terminal:
        return delivery.status

    adapter = get_channel(delivery.channel)
    if adapter is None:
        _fail(delivery, f"No adapter for channel {delivery.channel}", retryable=False)
        return delivery.status

    notification = delivery.notification
    request = NotificationRequest(
        organization_id=notification.organization_id,
        template_key=notification.template_key,
        recipient_membership_id=notification.recipient_membership_id,
        recipient_email=_resolve_email(notification),
        channels=(NotificationChannel(delivery.channel),),
        context=notification.context,
    )

    try:
        rendered: RenderedMessage = render(
            notification.template_key, notification.context
        )
    except Exception as exc:
        _fail(delivery, f"Template error: {exc}", retryable=False)
        return delivery.status

    delivery.status = DeliveryStatus.SENDING.value
    delivery.attempts += 1
    delivery.save(update_fields=["status", "attempts", "updated_at"])

    result = adapter.send(request, rendered)

    if result.success:
        delivery.status = DeliveryStatus.SENT.value
        delivery.sent_at = timezone.now()
        delivery.provider_message_id = result.provider_message_id or ""
        delivery.last_error = ""
        delivery.save(
            update_fields=[
                "status",
                "sent_at",
                "provider_message_id",
                "last_error",
                "updated_at",
            ]
        )
        metrics.increment(NOTIFICATION_SENT, channel=delivery.channel)
        return delivery.status

    _fail(delivery, result.error or "unknown error", retryable=result.retryable)
    return delivery.status


def _fail(delivery: Any, error: str, *, retryable: bool) -> None:
    """Record a failure and either schedule a retry or dead-letter.

    Bounded attempts. A permanently undeliverable address must not be retried
    forever: it wastes provider quota and keeps a dead row in the working set.
    """
    max_attempts = getattr(settings, "NOTIFICATION_MAX_ATTEMPTS", 5)

    if not retryable or delivery.attempts >= max_attempts:
        delivery.status = DeliveryStatus.DEAD_LETTERED.value
        delivery.last_error = error[:2000]
        delivery.save(update_fields=["status", "last_error", "updated_at"])
        metrics.increment(NOTIFICATION_FAILED, channel=delivery.channel)
        logger.error(
            "notification.dead_lettered",
            delivery_id=str(delivery.id),
            channel=delivery.channel,
            attempts=delivery.attempts,
        )
        return

    base = float(getattr(settings, "TASK_RETRY_BACKOFF_BASE_S", 2.0))
    cap = float(getattr(settings, "TASK_RETRY_BACKOFF_CAP_S", 3600.0))
    delay = random.uniform(0, min(cap, base * (2**delivery.attempts)))  # noqa: S311 - retry jitter, not cryptography

    delivery.status = DeliveryStatus.FAILED.value
    delivery.last_error = error[:2000]
    delivery.available_at = timezone.now() + timezone.timedelta(seconds=delay)
    delivery.save(update_fields=["status", "last_error", "available_at", "updated_at"])
    logger.warning(
        "notification.retry_scheduled",
        delivery_id=str(delivery.id),
        attempts=delivery.attempts,
        delay_s=round(delay, 1),
    )


def _resolve_email(notification: Any) -> str:
    """Address for this notification, preferring the explicit one.

    Reads the User through the membership rather than querying User directly,
    keeping to the rule that identity is reached via organizations (F-6).
    """
    if notification.recipient_email:
        return notification.recipient_email
    membership = notification.recipient_membership
    if membership is None:
        return ""
    return getattr(membership.user, "email", "")
