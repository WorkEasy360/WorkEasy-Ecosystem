"""Back-channel logout delivery (ADR-067).

``deliver_due`` runs on a short beat *and* is kicked by name right after a
sign-out commits, so a product normally hears within a second; the beat is
the guarantee when the kick is lost. Queue ``default``, not ``heavy``: a
sign-out is a security action a person is waiting on, and each request is
bounded at 8 seconds. Idempotent: rows are leased with ``SKIP LOCKED``.
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import CrossTenantTask


@shared_task(
    base=CrossTenantTask,
    queue="default",
    soft_time_limit=60,
    time_limit=90,
    name="integration.backchannel.deliver_due",
)
def deliver_due() -> dict[str, int]:
    from workeasy360.integration.backchannel import delivery

    return delivery.deliver_due()


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=120,
    time_limit=150,
    name="integration.backchannel.purge_finished",
)
def purge_finished() -> int:
    from workeasy360.platform.ecosystem import backchannel

    return backchannel.purge_finished()
