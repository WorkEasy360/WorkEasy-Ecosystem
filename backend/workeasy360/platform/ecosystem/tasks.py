"""Scheduled housekeeping for the ecosystem boundary.

One task. Authorization codes are issued on every product sign-in, live for
sixty seconds, and are then worthless - but nothing deletes them, so the
table grows for the lifetime of the deployment and the redemption lookup gets
slower with every login that ever happened.

``CrossTenantTask`` because these rows are control plane: they carry an
organization id as a plain column and are deliberately not tenant-scoped (see
``models.AuthorizationCode``), so there is no per-organization scope to enter
and nothing to iterate.
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask

logger = get_logger(__name__)


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=120,
    time_limit=300,
    name="platform.ecosystem.prune_authorization_codes",
)
def prune_authorization_codes(older_than_seconds: int | None = None) -> int:
    """Delete codes that expired long enough ago to be uninteresting.

    Idempotent, so running it twice is a no-op and running it late is a
    delay - which is why the schedule can be unambitious.

    The retention past expiry is deliberate and is the reason this does not
    simply delete everything expired: a replay attempt against a recently
    spent code should still *find the row*, so that it is logged as a replay
    rather than as an unknown code. An hour is long enough for that signal to
    be useful and short enough that the table stays small.
    """
    from workeasy360.platform.ecosystem.authorization import prune_expired

    deleted = prune_expired(
        older_than_seconds=older_than_seconds if older_than_seconds is not None else 3600
    )
    if deleted:
        logger.info("ecosystem.authorization_codes.pruned", count=deleted)
    return deleted
