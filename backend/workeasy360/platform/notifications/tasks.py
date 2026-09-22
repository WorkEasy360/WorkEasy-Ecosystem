"""Notification delivery tasks.

Queue: ``default``. Notifications are Tier 3 - deferrable and never on a
critical path - so they must not share a worker pool with the outbox relay.

**Both isolation layers, in the worker too.** ``TenantTask`` re-establishes
the tenant *context* across the queue boundary, which is Layer 2. It
deliberately does not open a transaction, because a base class cannot know a
task's transaction boundaries - so Layer 3, the RLS variable, is the task's
own responsibility. A task that queries its tenant's rows without entering
``organization_db_scope`` matches zero rows and reports "nothing to do".
"""

from __future__ import annotations

from uuid import UUID

from celery import shared_task

from workeasy360.contracts.enums import OrganizationStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask, TenantTask

logger = get_logger(__name__)

#: Actor ids for work no person performed. Nil rather than borrowed, for the
#: reason ``audit.record_system`` gives: a fabricated actor puts a name
#: against something they did not do.
_NO_ACTOR = UUID(int=0)


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

    The database scope is entered here rather than in ``attempt_delivery``,
    which cannot know the organization until it has read the row it needs the
    scope to see.
    """
    from workeasy360.platform.notifications.services import attempt_delivery
    from workeasy360.platform.tenancy.context import current_tenant
    from workeasy360.platform.tenancy.db import organization_db_scope

    ctx = current_tenant()
    with organization_db_scope(ctx.organization_id):
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

    Per organization, enumerated from the **control-plane** ``Organization``
    table, for the reason the commercial sweeps give: a tenant table matches
    zero rows without a tenant variable, so a single cross-tenant scan finds
    nothing and reports success having visited nobody. This job used to do
    exactly that, which meant the broker-outage recovery path above did not
    work at all.

    One organization's failure is isolated from the rest: the whole point of
    a sweeper is that it runs to completion.
    """
    from django.utils import timezone

    from workeasy360.contracts.enums import DeliveryStatus
    from workeasy360.platform.notifications.models import NotificationDelivery
    from workeasy360.platform.organizations.selectors import (
        all_organization_ids,
        organization_status,
    )
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    now = timezone.now()
    redispatched = 0
    failed = 0

    for organization_id in all_organization_ids():
        try:
            with (
                unscoped_access(
                    reason="Notification sweeper runs without a request tenant context",
                    module="platform.notifications.tasks",
                ),
                organization_db_scope(organization_id),
            ):
                due = list(
                    NotificationDelivery.unscoped.filter(
                        organization_id=organization_id,
                        status__in=[
                            DeliveryStatus.PENDING.value,
                            DeliveryStatus.FAILED.value,
                        ],
                        available_at__lte=now,
                    ).values_list("id", flat=True)[:limit]
                )
            if not due:
                continue

            # ``deliver_notification`` requires tenant context and refuses to
            # be dispatched without one, so the sweeper supplies a routing-only
            # context. Built once here, and only when there is work, so the
            # status lookup costs nothing on the common empty pass.
            status = organization_status(organization_id)
            if status is None:  # purged between enumeration and now
                continue

            with tenant_context(_routing_context(organization_id, status)):
                for delivery_id in due:
                    # Per delivery, because the outage this job recovers from
                    # is a broker outage: dispatching is the operation most
                    # likely to fail here, and one failure must not abandon
                    # the rest of the batch or the organizations after it.
                    # The row stays pending, so the next pass retries it.
                    try:
                        deliver_notification.apply_async(args=[str(delivery_id)])
                        redispatched += 1
                    except Exception as exc:
                        failed += 1
                        logger.warning(
                            "notification.redispatch_failed",
                            delivery_id=str(delivery_id),
                            error_type=type(exc).__name__,
                        )
        except Exception as exc:
            failed += 1
            logger.error(
                "notification.sweep.failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )

    if redispatched or failed:
        logger.info("notification.sweep", redispatched=redispatched, failed=failed)
    return redispatched


def _routing_context(organization_id: UUID, status: OrganizationStatus) -> TenantContext:
    """A tenant context that routes and does not claim to be anybody.

    ``TenantTask`` refuses to dispatch without a context, and rightly: a task
    that cannot establish its tenant must fail at publish time rather than in
    a worker minutes later. A sweep has a tenant and no actor, so it supplies
    the first and leaves the second **nil**.

    Nil rather than borrowed, for the reason ``audit.record_system`` gives: a
    fabricated actor puts a name against work nobody did. A nil UUID cannot
    collide with a real membership, and it reads as "absent" to the next
    person rather than as a membership whose id happens to match something
    else. Nothing on this path reads the actor - the delivery task uses the
    organization to enter its database scope, and ``attempt_delivery`` audits
    nothing.

    The **status is read, never assumed**. It drives the R-10 write block, and
    a sweep that defaulted to ``active`` would let a suspended organization be
    written to from a background path while the request path correctly
    refused - a second write-block exemption created by accident rather than
    by an ADR. Carrying the real status keeps the sweeper exactly as
    permissive as the request path and no more.
    """
    return TenantContext(
        organization_id=organization_id,
        membership_id=_NO_ACTOR,
        user_id=_NO_ACTOR,
        organization_status=status,
    )
