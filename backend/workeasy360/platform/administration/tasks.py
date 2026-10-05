"""Retention for the activity the consoles record (Phase 8 extension).

One daily sweep, ``heavy`` queue: expired browser sessions (Django's own
``clearsessions``, now also enforcing the absolute lifetime), each person's
security trail and each organization's sign-in activity past their retention,
and (Phase 9) spent account links and signups that never verified.
Idempotent - running it twice deletes nothing the second time.
"""

from __future__ import annotations

from celery import shared_task
from django.conf import settings

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask

logger = get_logger(__name__)


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=900,
    time_limit=1200,
    name="platform.administration.prune_activity",
)
def prune_activity() -> dict[str, int]:
    from workeasy360.platform.administration.self_service import prune_pending_accounts
    from workeasy360.platform.identity import tokens
    from workeasy360.platform.identity.account import prune_account_events
    from workeasy360.platform.identity.sessions import SessionStore
    from workeasy360.platform.organizations.services import prune_member_sign_ins

    SessionStore.clear_expired()
    result = {
        "account_events": prune_account_events(
            older_than_days=settings.ACCOUNT_EVENT_RETENTION_DAYS
        ),
        "member_sign_ins": prune_member_sign_ins(
            older_than_days=settings.MEMBER_SIGN_IN_RETENTION_DAYS
        ),
        "account_tokens": tokens.prune(),
        "pending_accounts": prune_pending_accounts(),
    }
    logger.info("administration.activity_pruned", **result)
    return result
