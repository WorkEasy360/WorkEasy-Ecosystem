"""Notification delivery tasks.

Queue: ``default``. Notifications are Tier 3 - deferrable and never on a
critical path - so they must not share a worker pool with the outbox relay.
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask, TenantTask

logger = get_logger(__name__)


@shared_task(
    base=TenantTask,
    queue="default",
    soft_time_limit=30,
    time_limit=60,
    name="platform.notifications.deliver",
    bind=True,
    max_retries=0,  # retry scheduling is owned by the service, not by Celery
)
def deliver_notification(self, delivery_id: str) -> str:
    """Attempt one delivery.

    ``max_retries=0`` deliberately: the service records attempts and computes
    the backoff, and the sweeper re-dispatches. Letting Celery retry as well
    would nest two retry policies and multiply attempts
    (09-failure-resilience.md section 4.3, "Nesting: forbidden").
    """
    from workeasy360.platform.notifications.services import attempt_delivery

    return attempt_delivery(delivery_id)


@shared_task(
    base=CrossTenantTask,
    queue="default",
    soft_time_limit=120,
    time_limit=180,
    name="platform.notifications.sweep_pending",
)
def sweep_pending_deliveries(limit: int = 500) -> int:
    """Re-dispatch deliveries whose backoff has elapsed.

    Also the recovery path for deliveries that were never queued because the
    broker was unreachable at commit time. Without this, a broker blip would
    silently strand notifications that the database says are pending.
    """
    from django.utils import timezone

    from workeasy360.contracts.enums import DeliveryStatus
    from workeasy360.platform.notifications.models import NotificationDelivery
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(
        reason="Notification sweeper spans tenants by design",
        module="platform.notifications.tasks",
    ):
        due = list(
            NotificationDelivery.unscoped.filter(
                status__in=[DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value],
                available_at__lte=timezone.now(),
            ).values_list("id", flat=True)[:limit]
        )

    for delivery_id in due:
        deliver_notification.apply_async(
            args=[str(delivery_id)], headers={"w360_tenant": None}
        )

    if due:
        logger.info("notification.sweep", redispatched=len(due))
    return len(due)
