"""Gateway tasks: running async AI jobs, settling lapsed reservations, and the
intelligence platform's daily retention sweep.

Queue ``heavy`` for all of them (Task 40): a job waits on a provider for up
to ``AI_ASYNC_DEADLINE_S``, and none of this may occupy the ``critical``
workers sign-in and the outbox relay depend on. Retries are the due-work
timer's, never Celery's (``max_retries=0``).
"""

from __future__ import annotations

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask, TenantTask

logger = get_logger(__name__)


@shared_task(
    base=TenantTask,
    queue="heavy",
    # Above the async deadline plus two short transactions, below the async
    # lease (AI_ASYNC_LEASE_S), so a stuck worker is killed before its
    # reservation could be settled by the lease timer underneath it.
    soft_time_limit=180,
    time_limit=240,
    name="intelligence.gateway.run_execution",
    max_retries=0,
)
def run_execution(execution_pk: str) -> str:
    """The consumer of an execution's due-work timer. The task establishes
    its own context: the organization's *live* status, never the dispatcher's
    assumption (Task 81)."""
    from uuid import UUID

    from workeasy360.intelligence.ai import budget
    from workeasy360.intelligence.gateway.service import run_job
    from workeasy360.platform.organizations.selectors import organization_status
    from workeasy360.platform.tenancy.context import current_tenant

    dispatched = current_tenant()
    try:
        key = UUID(str(execution_pk))
    except ValueError:
        return "missing"
    live = organization_status(dispatched.organization_id)
    if live is None:
        from workeasy360.integration.scheduling import services as scheduling

        scheduling.complete(
            budget.TIMER_KIND, key, organization_id=dispatched.organization_id
        )
        return "organization_gone"
    return run_job(dispatched.replace_status(live), key)


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=600,
    time_limit=900,
    name="intelligence.prune",
)
def prune() -> dict[str, int]:
    """Daily retention for AI, RAG and analytics (Tasks 46, 83, 84).

    Visits each organization in its own scope - never one query across
    tenants - and isolates failures so one organization cannot stop the
    rest. The platform's own organization-wide control-plane rows (registry,
    skills, metrics) are not pruned: they are configuration, not telemetry.
    """
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.intelligence.ai.retention import prune_organization as prune_ai
    from workeasy360.intelligence.analytics.retention import (
        prune_organization as prune_analytics,
    )
    from workeasy360.intelligence.rag.retention import prune_organization as prune_rag
    from workeasy360.platform.organizations.selectors import all_organization_ids
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    totals: dict[str, int] = {"failed": 0}
    for organization_id in all_organization_ids():
        ctx = routing_context(organization_id)
        if ctx is None:
            continue
        try:
            with tenant_context(ctx), organization_db_scope(organization_id):
                for part in (prune_ai(), prune_rag(), prune_analytics()):
                    for key, value in part.items():
                        totals[key] = totals.get(key, 0) + value
        except SoftTimeLimitExceeded:
            raise  # the budget ends the pass (Phase 12, G-32)
        except Exception as exc:
            totals["failed"] += 1
            logger.error(
                "intelligence.prune.failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )
    if any(totals.values()):
        logger.info("intelligence.prune", **totals)
    return totals
