"""Webhook delivery task.

Queue ``heavy`` (Task 48). Delivery is the one Phase 6 workload that waits
on a third party's network - up to the egress deadline per attempt - and it
must not occupy the ``default`` workers that notification delivery and the
due-work sweeper share, nor ever the ``critical`` ones the outbox relay
needs. Routing is a separate declaration per task, so moving delivery to a
dedicated queue later is a deployment change (R-12).
"""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import TenantTask


@shared_task(
    base=TenantTask,
    queue="heavy",
    # Comfortably above the egress deadline plus two short transactions,
    # and below the claim lease (WEBHOOK_LEASE_S), so a worker is killed
    # before another could legitimately re-claim its attempt.
    soft_time_limit=60,
    time_limit=75,
    name="integration.webhooks.deliver",
    # Retries are owned by the pipeline (a timer), never by Celery: two
    # retry policies nested is 09-failure-resilience.md's "retry
    # amplification", and a broker ETA is not durable enough to hold a
    # backoff of hours.
    max_retries=0,
)
def deliver(delivery_id: str) -> str:
    from workeasy360.integration.webhooks.pipeline import run_delivery

    return run_delivery(delivery_id)
