"""The scheduled product health sweep.

Queue ``heavy`` (Phase 12, G-31): like every integration task it is Tier 3 and
must never queue ahead of the outbox relay on ``critical`` - and at up to two
minutes a run, against products that may be slow or down, it must not hold a
``default`` worker that customer checkouts and the commercial clock wait for
either. Idempotent and safe to run more often than scheduled - it only reads
other people's front doors and records what it saw.
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import CrossTenantTask


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=120,
    time_limit=150,
    name="integration.health.check_all",
)
def check_all() -> dict[str, int]:
    from workeasy360.integration.health import probe

    return probe.check_all()
