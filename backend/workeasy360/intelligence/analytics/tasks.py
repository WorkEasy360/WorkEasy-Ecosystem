"""The analytics projection task.

Queue ``heavy`` (Task 40): analytics is Tier 3 and lag-tolerant, so its
batches never compete with the ``default`` workers routing and automation
use, nor the ``critical`` ones sign-in depends on. Retries are the due-work
timer's, never Celery's.
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import TenantTask


@shared_task(
    base=TenantTask,
    queue="heavy",
    soft_time_limit=60,
    time_limit=90,
    name="intelligence.analytics.project",
    max_retries=0,
)
def project(organization_pk: str) -> str:
    """Establishes its own trusted context (Task 81): the dispatcher passes
    only an organization id; the organization's live status is read here, and
    the work runs in that one organization's database scope."""
    from uuid import UUID

    from django.conf import settings
    from django.utils import timezone

    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.intelligence.analytics.consumer import PROJECT_TIMER
    from workeasy360.intelligence.analytics.projection import project as run
    from workeasy360.platform.organizations.selectors import organization_status
    from workeasy360.platform.tenancy.context import current_tenant, tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    dispatched = current_tenant()
    organization_id = UUID(str(organization_pk))
    if organization_id != dispatched.organization_id:
        return "scope_mismatch"
    live = organization_status(organization_id)
    if live is None:
        scheduling.complete(
            PROJECT_TIMER, organization_id, organization_id=organization_id
        )
        return "organization_gone"
    if not live.allows_write:
        scheduling.schedule(
            PROJECT_TIMER,
            organization_id=organization_id,
            target_id=organization_id,
            due_at=timezone.now()
            + timezone.timedelta(seconds=float(settings.WEBHOOK_HOLD_RECHECK_S)),
        )
        return "held_organization"
    with (
        tenant_context(dispatched.replace_status(live)),
        organization_db_scope(organization_id),
    ):
        return run(organization_id)
