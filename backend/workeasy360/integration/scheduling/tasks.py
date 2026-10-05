"""The due-work sweeper.

Queue ``default``: integration work is Tier 3 and must never queue ahead of
the outbox relay on ``critical`` (09-failure-resilience.md section 4.1).
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask

logger = get_logger(__name__)


@shared_task(
    base=CrossTenantTask,
    queue="default",
    soft_time_limit=60,
    time_limit=90,
    name="integration.scheduling.dispatch_due",
)
def dispatch_due(limit: int | None = None) -> dict[str, int]:
    """Claim due timers and dispatch their consumers.

    Cross-tenant by nature, and safe because it reads only the timer table
    and the control-plane organization status - never a tenant row. Each
    consumer task it dispatches enters its own organization's scope.
    """
    from workeasy360.integration.scheduling import services
    from workeasy360.platform.observability.metrics import metrics

    metrics.gauge("integration_due_work_lag_seconds", services.overdue_seconds())
    items = services.claim_due(limit=limit)
    if not items:
        return {"claimed": 0, "dispatched": 0, "failed": 0, "dropped": 0}

    result = services.dispatch(items)
    logger.info("scheduling.sweep", claimed=len(items), **result)
    return {"claimed": len(items), **result}
