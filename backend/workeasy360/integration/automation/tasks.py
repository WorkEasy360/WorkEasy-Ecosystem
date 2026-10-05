"""Automation tasks. Queue ``default``: both are short, database-only units
of work (conditions are pure; actions only create records), so they belong
with the platform's other Tier 3 background work, never on ``critical``."""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import TenantTask


@shared_task(
    base=TenantTask,
    queue="default",
    soft_time_limit=60,
    time_limit=90,
    name="integration.automation.run_execution",
    max_retries=0,  # retries are the engine's (a timer), never Celery's
)
def run_execution(execution_id: str) -> str:
    from workeasy360.integration.automation.engine import run_execution as run

    return run(execution_id)


@shared_task(
    base=TenantTask,
    queue="default",
    soft_time_limit=30,
    time_limit=60,
    name="integration.automation.fire_schedule",
    max_retries=0,
)
def fire_schedule(rule_id: str) -> str:
    from django.conf import settings
    from django.utils import timezone

    from workeasy360.integration.automation import services
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.organizations.selectors import organization_status
    from workeasy360.platform.tenancy.context import current_tenant, tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    dispatched_ctx = current_tenant()
    live = organization_status(dispatched_ctx.organization_id)
    if live is None:
        return "organization_gone"
    if not live.allows_write:
        # No new automation side effects for a suspended organization; the
        # missed runs are coalesced when it is reactivated (Tasks 45, 54).
        from uuid import UUID

        scheduling.schedule(
            services.SCHEDULE_TIMER,
            organization_id=dispatched_ctx.organization_id,
            target_id=UUID(str(rule_id)),
            due_at=timezone.now()
            + timezone.timedelta(seconds=float(settings.WEBHOOK_HOLD_RECHECK_S)),
        )
        return "held_organization"

    ctx = dispatched_ctx.replace_status(live)
    with tenant_context(ctx), organization_db_scope(ctx.organization_id):
        status, pending = services.fire_schedule(rule_id)
        scheduling.enqueue_after_commit(run_execution, pending, ctx=ctx)
    return status
