"""Files platform tasks (2026-11-10)."""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask

logger = get_logger(__name__)


@shared_task(
    base=CrossTenantTask,
    queue="default",
    soft_time_limit=240,
    time_limit=300,
    name="platform.files.rescan_quarantined",
)
def rescan_quarantined() -> dict[str, int]:
    """Retry the malware scan of strict files still waiting for a verdict.

    KYC documents are stored under the platform organization's scope (the
    control plane's own tenant), so that is the one scope to visit. A file that
    gets no verdict again simply keeps waiting: nothing here ever marks one clean
    without a scanner saying so.
    """
    from uuid import UUID

    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.files.services import rescan_waiting
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    # The platform organization with no actor: nobody is acting and none is
    # claimed (the shape of the notification sweeper's routing context, built
    # here because files sits below notifications).
    nobody = UUID(int=0)
    ctx = TenantContext(
        organization_id=PLATFORM_ORGANIZATION_ID,
        membership_id=nobody,
        user_id=nobody,
        organization_status=OrganizationStatus.ACTIVE,
    )
    with tenant_context(ctx), organization_db_scope(PLATFORM_ORGANIZATION_ID):
        counts = rescan_waiting()
    if any(counts.values()):
        logger.info("files.rescan", **counts)
    return counts
