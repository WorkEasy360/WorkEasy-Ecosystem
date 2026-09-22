"""The commercial clock.

One scheduled task, because every transition it applies has the same cause:
a durable timestamp that is now in the past. Three separate sweeps of the
same table for the same reason would be three times the load for no extra
information.
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import CrossTenantTask


@shared_task(
    base=CrossTenantTask,
    queue="default",
    soft_time_limit=300,
    time_limit=420,
    name="commercial.subscriptions.process_due",
)
def process_due_subscriptions() -> dict[str, int]:
    """Expire ended trials, apply due cancellations, roll billing periods.

    Idempotent: every transition is guarded by the state machine and by the
    timestamp that caused it, so a second run within the same window applies
    nothing. Safe to run more often than strictly necessary, which is what
    makes a missed schedule a delay rather than a defect.
    """
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    return process_due_subscriptions()
