"""Scheduled provisioning work.

Both tasks are cross-tenant by nature and both iterate organizations,
entering tenant scope per organization rather than running one unscoped query
across every tenant (03-multi-tenancy.md).
"""

from __future__ import annotations

from celery import shared_task

# Phase 12 (G-32): SoftTimeLimitExceeded subclasses Exception. Swallowed
# here, a pass that outran its budget kept going until the hard kill, with
# no summary, and the organizations enumerated last were never reached.
from celery.exceptions import SoftTimeLimitExceeded

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask

logger = get_logger(__name__)


@shared_task(
    base=CrossTenantTask,
    queue="critical",
    soft_time_limit=60,
    time_limit=90,
    name="commercial.provisioning.reconcile_organization",
)
def reconcile_organization(organization_id: str) -> int:
    """Recompute one organization's entitlements.

    On the ``critical`` queue: a customer who has just paid and cannot use
    what they bought is the most expensive failure this system has, and it
    must not queue behind a bulk import.
    """
    from uuid import UUID

    from workeasy360.commercial.provisioning.services import reconcile

    return reconcile(UUID(organization_id))


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=900,
    time_limit=1200,
    name="commercial.provisioning.reconcile_all",
)
def reconcile_all(limit: int | None = None) -> dict[str, int]:
    """The drift-repair and disaster-recovery pass.

    Recomputes every organization that holds commercial state or entitlements
    and repairs what disagrees. This is also the mechanism that applies
    **time-based** entitlement changes - a trial that ended, a cancellation
    whose date has arrived - because those need no command, only a clock.

    Per-organization failures are isolated: one corrupt tenant must not stop
    the pass for everyone else.
    """
    from workeasy360.commercial.provisioning.services import (
        RECONCILE_FAILURES,
        reconcile_and_report_drift,
    )
    from workeasy360.platform.observability.metrics import metrics
    from workeasy360.platform.organizations.selectors import all_organization_ids

    # Enumerated from the **control-plane** Organization table, which has no
    # RLS policy. A query over a tenant table with no tenant variable set
    # matches zero rows by design, so building the work list from
    # subscriptions or entitlements would silently find nothing to do - a
    # scheduled job that reports success having visited nobody.
    targets = all_organization_ids()
    if limit:
        targets = targets[:limit]

    visited = drifted = failed = 0
    for organization_id in targets:
        try:
            report = reconcile_and_report_drift(organization_id)
            visited += 1
            drifted += 1 if report.has_drift else 0
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            failed += 1
            metrics.increment(RECONCILE_FAILURES)
            logger.error(
                "provisioning.reconcile.failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )

    logger.info(
        "provisioning.reconcile_all.finished",
        visited=visited,
        drifted=drifted,
        failed=failed,
    )
    return {"visited": visited, "drifted": drifted, "failed": failed}
