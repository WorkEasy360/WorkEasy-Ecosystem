"""Notification services: persist intent, queue delivery, return."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.enums import (
    DeliveryStatus,
    NotificationChannel,
    OrganizationStatus,
)
from workeasy360.contracts.ports.notifications import (
    NotificationRequest,
    RenderedMessage,
)
from workeasy360.contracts.values import backoff
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import (
    NOTIFICATION_FAILED,
    NOTIFICATION_SENT,
    metrics,
)
from workeasy360.platform.tenancy.context import current_tenant_or_none

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
    import uuid

    from workeasy360.platform.notifications import sealing
    from workeasy360.platform.notifications.models import (
        Notification,
        NotificationDelivery,
    )
    from workeasy360.platform.notifications.tasks import deliver_notification

    notification_id = uuid.uuid4()
    sealed = sealing.seal(
        dict(request.sealed_context),
        organization_id=request.organization_id,
        notification_id=notification_id,
    )
    with transaction.atomic():
        try:
            # Savepoint: a unique-constraint violation aborts the whole
            # PostgreSQL transaction, so catching IntegrityError without one
            # would leave the connection unusable and take the caller's
            # business write down with it.
            with transaction.atomic():
                notification = Notification.objects.create(
                    id=notification_id,
                    organization_id=request.organization_id,
                    template_key=request.template_key,
                    recipient_membership_id=request.recipient_membership_id,
                    recipient_email=request.recipient_email or "",
                    context=dict(request.context),
                    idempotency_key=request.idempotency_key or "",
                    sealed_context=sealed,
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
        # Dispatched under the context the notification was created in: the
        # caller's may have ended before its transaction commits (an account
        # email is queued under a routing context its caller leaves before
        # committing), and a tenant task refuses to be sent without one.
        queued_under = current_tenant_or_none()
        transaction.on_commit(
            lambda: _enqueue(deliver_notification, delivery_ids, ctx=queued_under)
        )

    logger.info(
        "notification.queued",
        notification_id=str(notification.id),
        template=request.template_key,
        channels=[str(c) for c in request.channels],
    )
    return notification.id


def notify_account(
    *,
    template_key: str,
    recipient_email: str,
    idempotency_key: str,
    context: dict[str, Any] | None = None,
    sealed_context: dict[str, str] | None = None,
) -> UUID:
    """Queue an email addressed to a *person* rather than an organization
    (Phase 9: verification, password reset).

    A notification is tenant-scoped, and a person signing up belongs to no
    organization yet, so these are anchored on the **platform organization** -
    the control plane's own tenant, which has no members and appears in no
    customer view. The context is a routing context with a nil actor, exactly
    as the delivery sweeper builds one: nobody is acting, and none is claimed.
    Delivery, retries and the sealed-link wipe are the ordinary path.
    """
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.authz.control_plane import ensure_platform_organization
    from workeasy360.platform.notifications.tasks import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    ensure_platform_organization()
    ctx = routing_context(PLATFORM_ORGANIZATION_ID, OrganizationStatus.ACTIVE)
    with tenant_context(ctx), organization_db_scope(PLATFORM_ORGANIZATION_ID):
        return notify(
            NotificationRequest(
                organization_id=PLATFORM_ORGANIZATION_ID,
                template_key=template_key,
                recipient_email=recipient_email,
                context=dict(context or {}),
                sealed_context=dict(sealed_context or {}),
                idempotency_key=idempotency_key,
            )
        )


def _enqueue(task: Any, delivery_ids: list[str], *, ctx: Any = None) -> None:
    """Dispatch delivery tasks, tolerating a broker outage.

    If the broker is down, the deliveries stay ``pending`` and the sweeper
    picks them up. The user's action has already succeeded; failing it here
    because a Tier-3 notification could not be *queued* would invert the
    dependency tiers (09-failure-resilience.md section 2).
    """
    from workeasy360.platform.tenancy.context import tenant_context

    queued_under = ctx or current_tenant_or_none()
    for delivery_id in delivery_ids:
        try:
            with tenant_context(queued_under):
                task.delay(delivery_id)
        except Exception as exc:
            logger.warning(
                "notification.enqueue_failed",
                delivery_id=delivery_id,
                error_type=type(exc).__name__,
            )


#: How long a claimed delivery stays reserved for the worker that claimed it.
#: Longer than the delivery task's hard limit (30 s), so a live attempt is never
#: claimed twice; short enough that an attempt whose worker died is retried
#: promptly (the sweeper re-dispatches a SENDING row once its lease is over).
CLAIM_LEASE_S = 120


def attempt_delivery(
    delivery_id: UUID | str, *, organization_id: UUID | None = None
) -> str:
    """Attempt one delivery. Returns the resulting status.

    Three steps, and the provider call is in none of the transactions (Phase
    12). Before, the row lock and an open transaction were held across the
    SMTP send; with ``idle_in_transaction_session_timeout`` (15 s) a slow
    provider ended the session, the outcome and the attempt count rolled
    back, and the sweeper sent the email again, without end.

    1. **Claim** (short transaction): lock the row ``SKIP LOCKED``, check it is
       still due, render the message, then mark it SENDING with
       ``attempts + 1`` and a lease (``available_at = now + CLAIM_LEASE_S``).
       Committed, so a concurrent copy or the sweeper sees a live claim and
       leaves it alone, and the attempt counts even if the worker dies.
    2. **Send**, outside any transaction.
    3. **Record** (short transaction): SENT, or a retry/dead-letter.

    A worker that dies between 2 and 3 leaves a SENDING row whose lease
    expires; it is sent again then - at-least-once across a crash, never a
    loop, because the attempt was already counted.

    ``organization_id`` makes each step enter that organization's database
    scope itself (the delivery task). Without it the caller must already be in
    one, and the steps nest in the caller's transaction (tests, fixtures).
    Records the outcome rather than raising, so that a permanently
    undeliverable address is a recorded failure, not a worker exception.
    """
    claimed = _claim(delivery_id, organization_id)
    if isinstance(claimed, str):
        return claimed
    adapter, request, rendered, attempts = claimed
    result = adapter.send(request, rendered)
    return _record(delivery_id, organization_id, attempts, result)


def _scope(organization_id: UUID | None) -> Any:
    from contextlib import nullcontext

    from workeasy360.platform.tenancy.db import organization_db_scope

    return organization_db_scope(organization_id) if organization_id else nullcontext()


def _claim(delivery_id: UUID | str, organization_id: UUID | None) -> Any:
    """Step 1. The claim's pieces, or the status to return without sending."""
    from workeasy360.platform.notifications import sealing
    from workeasy360.platform.notifications.channels import get_channel
    from workeasy360.platform.notifications.communication import render_for_delivery
    from workeasy360.platform.notifications.models import NotificationDelivery

    with _scope(organization_id), transaction.atomic():
        delivery = (
            NotificationDelivery.objects.select_related("notification")
            .select_for_update(skip_locked=True, of=("self",))
            .filter(id=delivery_id)
            .first()
        )
        if delivery is None:
            exists = NotificationDelivery.objects.filter(id=delivery_id).exists()
            return "in_progress" if exists else "missing"
        if delivery.is_terminal:
            return delivery.status
        now = timezone.now()
        if (
            delivery.status == DeliveryStatus.SENDING.value
            and delivery.available_at > now
        ):
            return "in_progress"  # another worker holds a live claim
        if (
            delivery.status == DeliveryStatus.FAILED.value
            and delivery.available_at is not None
            and delivery.available_at > now
        ):
            # A duplicate dispatched before the last failure: its backoff has
            # not elapsed, and the sweeper will dispatch it when it has.
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
            secrets_in_context = sealing.open_sealed(
                notification.sealed_context,
                organization_id=notification.organization_id,
                notification_id=notification.id,
            )
            rendered: RenderedMessage = render_for_delivery(
                notification.template_key,
                {**notification.context, **secrets_in_context},
            )
        except Exception as exc:
            # The exception *type* only: a template error's message can quote
            # the context, and the context may now include an opened secret.
            _fail(delivery, f"Template error: {type(exc).__name__}", retryable=False)
            return delivery.status

        delivery.status = DeliveryStatus.SENDING.value
        delivery.attempts += 1
        delivery.available_at = now + timezone.timedelta(seconds=CLAIM_LEASE_S)
        delivery.save(update_fields=["status", "attempts", "available_at", "updated_at"])
        return adapter, request, rendered, delivery.attempts


def _record(
    delivery_id: UUID | str, organization_id: UUID | None, attempts: int, result: Any
) -> str:
    """Step 3. Record what the provider answered for the attempt we claimed."""
    from workeasy360.platform.notifications.models import NotificationDelivery

    with _scope(organization_id), transaction.atomic():
        delivery = (
            NotificationDelivery.objects.select_related("notification")
            .select_for_update(of=("self",))
            .get(id=delivery_id)
        )
        if delivery.is_terminal or delivery.attempts != attempts:
            # Our lease ran out and a later attempt took the row over (or has
            # already finished it): that attempt records its own outcome.
            logger.warning(
                "notification.claim_overtaken",
                delivery_id=str(delivery.id),
                attempt=attempts,
                current_attempts=delivery.attempts,
            )
            return delivery.status
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
            _wipe_sealed_when_final(delivery.notification)
            return delivery.status
        _fail(delivery, result.error or "unknown error", retryable=result.retryable)
        return delivery.status


def _wipe_sealed_when_final(notification: Any) -> None:
    """Drop the sealed variables once no delivery of this notification can
    need them again. Only credential-bearing notifications have any."""
    from workeasy360.platform.notifications.models import NotificationDelivery

    if not notification.sealed_context:
        return
    pending = NotificationDelivery.objects.filter(
        notification_id=notification.id
    ).exclude(
        status__in=[
            DeliveryStatus.SENT.value,
            DeliveryStatus.DEAD_LETTERED.value,
            DeliveryStatus.SUPPRESSED.value,
        ]
    )
    if not pending.exists():
        type(notification).objects.filter(id=notification.id).update(sealed_context="")


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
        _wipe_sealed_when_final(delivery.notification)
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
    delay = backoff.full_jitter(delivery.attempts, base=base, cap=cap)

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
