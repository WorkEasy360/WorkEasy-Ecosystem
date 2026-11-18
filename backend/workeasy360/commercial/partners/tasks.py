"""Scheduled partner work: custom-domain control re-verification, and the
end of guest views that ran out (ADR-062)."""

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


@shared_task(
    base=CrossTenantTask,
    queue="default",
    soft_time_limit=120,
    time_limit=180,
    name="commercial.partners.close_guest_sessions",
)
def close_guest_sessions() -> int:
    """Record the end of every guest view past its time (``guest.close_expired``).

    Refusing them needs nothing from here - a guest view is refused from the
    moment it expires. This writes the row's end and the audit entry, once each,
    as ``SYSTEM`` in the platform organization's scope.
    """
    from workeasy360.commercial.partners import guest

    return guest.close_expired()
