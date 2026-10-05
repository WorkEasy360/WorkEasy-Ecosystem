"""Scheduled commission work.

Each task **establishes its own trusted context** and inherits nothing from
whoever enqueued it (Task 99, 100): there is no request, no session and - under
``CELERY_TASK_ALWAYS_EAGER`` in a test - no reason to believe an ambient tenant
context is the right one. They enumerate customers from the control plane and
every read of a customer's sales enters that customer's own database scope.

Queues follow the existing architecture (Task 101): accrual and pruning are
bulk, latency-insensitive work, so they run on ``heavy`` and never on
``critical``, which stays reserved for the work a waiting customer feels.
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import CrossTenantTask


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=900,
    time_limit=1200,
    name="commercial.commissions.sweep",
)
def sweep_commissions(limit: int | None = None) -> dict[str, int]:
    """Accrue for every customer that could have earned a commission.

    The guarantee behind the after-commit listener: derives the same answer
    from the same durable facts, and is idempotent through the ledger's unique
    key, so a run that overlaps another - or repeats - creates nothing twice.
    """
    from workeasy360.commercial.commissions.accrual import sweep

    return sweep(limit=limit)


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=300,
    time_limit=420,
    name="commercial.commissions.prune_referrals",
)
def prune_referrals() -> int:
    """Retention: remove long-expired, never-converted referral captures."""
    from workeasy360.commercial.commissions.referrals import prune_expired_referrals

    return prune_expired_referrals()
