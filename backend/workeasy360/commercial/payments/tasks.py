"""Payment tasks. Queue ``default``: a provider call waits on a network, so it
never occupies the ``critical`` workers sign-in and the outbox depend on.

Retries are the pipeline's own durable state - a due-work timer per attempt or
refund - never Celery's: a lost enqueue delays the work until the sweeper, it
never loses it.
"""

from __future__ import annotations

from uuid import UUID

from celery import shared_task

from workeasy360.platform.tenancy.tasks import CrossTenantTask, TenantTask


@shared_task(
    base=TenantTask,
    queue="default",
    soft_time_limit=40,
    time_limit=60,
    name="commercial.payments.create_checkout",
    max_retries=0,
)
def create_checkout(attempt_pk: str) -> str:
    from workeasy360.commercial.payments.checkout import create_checkout as run
    from workeasy360.platform.tenancy.context import current_tenant

    return run(current_tenant().organization_id, UUID(str(attempt_pk)))


@shared_task(
    base=TenantTask,
    queue="default",
    soft_time_limit=40,
    time_limit=60,
    name="commercial.payments.send_refund",
    max_retries=0,
)
def send_refund(refund_pk: str) -> str:
    from workeasy360.commercial.payments.refunds import send_refund as run
    from workeasy360.platform.tenancy.context import current_tenant

    return run(current_tenant().organization_id, UUID(str(refund_pk)))


@shared_task(
    base=CrossTenantTask,
    queue="default",
    soft_time_limit=240,
    time_limit=280,
    name="commercial.payments.process_due",
)
def process_due() -> dict[str, int]:
    from workeasy360.commercial.payments.dunning import process_due as run

    return run()


def register_dispatchers() -> None:
    """The due-work sweeper's route back into these tasks."""
    from workeasy360.commercial.payments.checkout import CHECKOUT_TIMER
    from workeasy360.commercial.payments.refunds import REFUND_TIMER
    from workeasy360.integration.scheduling.services import register_dispatcher

    register_dispatcher(
        CHECKOUT_TIMER,
        lambda item: create_checkout.apply_async(args=[str(item.target_id)]),
    )
    register_dispatcher(
        REFUND_TIMER,
        lambda item: send_refund.apply_async(args=[str(item.target_id)]),
    )
