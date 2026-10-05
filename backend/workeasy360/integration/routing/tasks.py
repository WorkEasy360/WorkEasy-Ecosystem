"""Routing and retention tasks."""

from __future__ import annotations

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask, TenantTask

logger = get_logger(__name__)


@shared_task(
    base=TenantTask,
    queue="default",
    soft_time_limit=30,
    time_limit=60,
    name="integration.routing.route_event",
    max_retries=0,  # the routing timer is the retry
)
def route_event(event_pk: str) -> str:
    """Fan one accepted event out. Database-only work, so ``default``."""
    from uuid import UUID

    from django.conf import settings
    from django.utils import timezone

    from workeasy360.integration.automation import tasks as automation_tasks
    from workeasy360.integration.routing import fanout
    from workeasy360.integration.routing.ingestion import ROUTING_TIMER
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.integration.webhooks import tasks as webhook_tasks
    from workeasy360.platform.organizations.selectors import organization_status
    from workeasy360.platform.tenancy.context import current_tenant, tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    dispatched_ctx = current_tenant()
    live = organization_status(dispatched_ctx.organization_id)
    if live is None:
        return "organization_gone"
    if not live.allows_write:
        # Suspended after the event was accepted: route nothing now (no new
        # side effects), keep the event, and look again later (Task 54).
        scheduling.schedule(
            ROUTING_TIMER,
            organization_id=dispatched_ctx.organization_id,
            target_id=UUID(str(event_pk)),
            due_at=timezone.now()
            + timezone.timedelta(seconds=float(settings.WEBHOOK_HOLD_RECHECK_S)),
        )
        return "held_organization"

    ctx = dispatched_ctx.replace_status(live)
    with tenant_context(ctx), organization_db_scope(ctx.organization_id):
        routed = fanout.route_event(event_pk)
        scheduling.enqueue_after_commit(webhook_tasks.deliver, routed.deliveries, ctx=ctx)
        scheduling.enqueue_after_commit(
            automation_tasks.run_execution, routed.executions, ctx=ctx
        )
    return routed.status


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=600,
    time_limit=900,
    name="integration.routing.prune",
)
def prune() -> dict[str, int]:
    """Daily retention across organizations (Tasks 25, 76).

    Visits each organization in turn inside its own scope - never one query
    across tenants - and isolates failures so one organization cannot stop
    the rest being pruned.
    """
    from workeasy360.integration.routing.retention import prune_organization
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.organizations.selectors import all_organization_ids
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    totals = {"deliveries": 0, "executions": 0, "events": 0, "failed": 0}
    for organization_id in all_organization_ids():
        ctx = routing_context(organization_id)
        if ctx is None or not ctx.is_write_allowed:
            continue
        try:
            with tenant_context(ctx), organization_db_scope(organization_id):
                for key, value in prune_organization(organization_id).items():
                    totals[key] += value
        except SoftTimeLimitExceeded:
            raise  # the budget ends the pass (Phase 12, G-32)
        except Exception as exc:
            totals["failed"] += 1
            logger.error(
                "integration.prune.failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )
    if any(totals.values()):
        logger.info("integration.prune", **totals)
    return totals
