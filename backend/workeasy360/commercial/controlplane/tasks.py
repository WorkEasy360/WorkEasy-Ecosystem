"""Scheduled Super Admin work: re-verifying product domain ownership (ADR-068)."""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import CrossTenantTask


@shared_task(
    base=CrossTenantTask,
    # Heavy, matching its schedule entry (base.py): up to 200 DNS lookups of
    # several seconds each must never hold a default-queue worker.
    queue="heavy",
    soft_time_limit=300,
    time_limit=420,
    name="commercial.controlplane.recheck_product_domains",
)
def recheck_product_domains() -> dict[str, int]:
    """Re-look for the ownership proof of every DNS-proven product domain.

    Reads nothing from any request; audits as the system. A resolver outage
    is not evidence of lost control, so it changes nothing.
    """
    from workeasy360.commercial.controlplane.product_domains import (
        recheck_verified_domains,
    )

    return recheck_verified_domains()
