"""Retention (Tasks 25, 76): what the integration platform keeps, and for
how long.

==========================  ==================================  ============
Record                      Kept                                Why
==========================  ==================================  ============
succeeded delivery          ``..._SUCCEEDED_DAYS`` (7)          diagnosis
dead delivery               ``..._DEAD_DAYS`` (30)              replay window
automation execution        ``..._EXECUTION_DAYS`` (30)         diagnosis
routed event (payload)      ``..._EVENT_DAYS`` (30), and never  replay needs
                            while any delivery or execution     the original
                            still refers to it
due-work timer              until its work finishes             -
==========================  ==================================  ============

Audit entries for control-plane changes (connections, secrets, subscriptions,
rules, replays) live in ``platform/audit`` under *its* retention, not here.

Per organization, inside that organization's scope, in bounded batches. A
read-only (suspended) organization is skipped: R-10 refuses its deletes like
any other write, and it accrues no new integration data while suspended.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)


def prune_organization(organization_id: UUID, *, now: Any = None) -> dict[str, int]:
    """Prune one organization. Caller has entered its tenant context and
    database scope."""
    from workeasy360.integration.automation import selectors as automation_selectors
    from workeasy360.integration.automation.services import prune_executions
    from workeasy360.integration.events.selectors import events_older_than
    from workeasy360.integration.events.services import prune_routed_events
    from workeasy360.integration.webhooks import selectors as webhook_selectors
    from workeasy360.integration.webhooks.services import prune as prune_deliveries

    now = now or timezone.now()

    def days(setting: str) -> Any:
        return now - timezone.timedelta(days=int(getattr(settings, setting)))

    deliveries = prune_deliveries(
        succeeded_before=days("INTEGRATION_RETENTION_SUCCEEDED_DAYS"),
        dead_before=days("INTEGRATION_RETENTION_DEAD_DAYS"),
    )
    executions = prune_executions(before=days("INTEGRATION_RETENTION_EXECUTION_DAYS"))

    event_cutoff = days("INTEGRATION_RETENTION_EVENT_DAYS")
    events = 0
    while True:
        candidates = events_older_than(event_cutoff, limit=500)
        if not candidates:
            break
        keep = webhook_selectors.event_pks_in_use(candidates) | (
            automation_selectors.event_pks_in_use(candidates)
        )
        removed = prune_routed_events(older_than=event_cutoff, keep=keep, limit=500)
        events += removed
        if removed == 0 or len(candidates) < 500:
            break
    return {"deliveries": deliveries, "executions": executions, "events": events}
