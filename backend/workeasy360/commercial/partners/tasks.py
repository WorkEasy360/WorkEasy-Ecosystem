"""Scheduled partner work: custom-domain control re-verification."""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import CrossTenantTask


@shared_task(
    base=CrossTenantTask,
    # Heavy, matching its schedule entry (base.py): up to 200 DNS lookups of
    # several seconds each (Phase 12: the decorator used to say ``default``).
    queue="heavy",
    soft_time_limit=300,
    time_limit=420,
    name="commercial.partners.recheck_domains",
)
def recheck_domains() -> dict[str, int]:
    """Re-look for the ownership proof on every verified or active domain.

    Establishes its own context: it writes the platform audit log as ``SYSTEM``
    inside the platform organization's scope and reads nothing from any
    request. A resolver outage is not evidence of lost control, so it disables
    nothing.
    """
    from workeasy360.commercial.partners.domains import recheck_proven_domains

    return recheck_proven_domains()
