"""The outbox relay and its housekeeping tasks.

Runs on the ``critical`` queue: a stalled relay means cross-product automation
stops for every tenant, and it must not queue behind a bulk import.
"""

from __future__ import annotations

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.tasks import CrossTenantTask

logger = get_logger(__name__)


class TransientPublishError(Exception):
    """A failure that may succeed on retry: timeout, connection, 5xx.

    Transient versus permanent is decided by **exception type**, never by
    string-matching an error message. Retrying a validation error five times
    is wasted work and alert noise
    (07-cross-product-integration.md section 3.7).
    """


@shared_task(
    base=CrossTenantTask,
    queue="critical",
    soft_time_limit=60,
    time_limit=90,
    name="integration.events.relay_outbox",
    bind=True,
)
def relay_outbox(self, batch_size: int | None = None) -> dict[str, int]:
    """Drain a batch of the outbox to the broker.

    Cross-tenant by nature: the outbox spans every organization. It reads only
    its own table and never queries tenant data, which is what makes the
    cross-tenant opt-out safe here.

    Publishing happens **outside** the claim transaction, because an external
    call inside a database transaction holds locks across a network round trip
    (09-failure-resilience.md section 7 rule 1).
    """
    from workeasy360.integration.events.dispatcher import publish_to_broker
    from workeasy360.integration.events.services import (
        claim_batch,
        mark_published,
        pending_age_seconds,
        schedule_retry,
    )
    from workeasy360.platform.observability.metrics import OUTBOX_PENDING, metrics

    claimed = claim_batch(batch_size)
    published = failed = 0

    for row in claimed:
        try:
            publish_to_broker(row)
            mark_published(row)
            published += 1
        except SoftTimeLimitExceeded:
            # The budget ends the batch (Phase 12, G-32); rows still claimed
            # are released by ``release_stale_claims``.
            raise
        except Exception as exc:
            # Per-event isolation: one poison event must not abandon the rest
            # of the batch.
            schedule_retry(row, exc)
            failed += 1

    # Backlog, gauged from the relay rather than sampled by a separate job:
    # this runs every few seconds and has already paid for the connection.
    # Oldest-pending *age* rather than depth, because a large draining backlog
    # is healthy while a small stuck one is not - the same SLI the deep health
    # probe reports, now also a number an alert can be written against.
    metrics.gauge(OUTBOX_PENDING, pending_age_seconds())

    if claimed:
        logger.info(
            "outbox.relay.batch",
            claimed=len(claimed),
            published=published,
            failed=failed,
        )
    return {"claimed": len(claimed), "published": published, "failed": failed}


@shared_task(
    base=CrossTenantTask,
    queue="default",
    soft_time_limit=60,
    time_limit=90,
    name="integration.events.release_stale_claims",
)
def release_stale_claims(older_than_seconds: int = 300) -> int:
    """Return abandoned claims to pending after a worker crash."""
    from workeasy360.integration.events.services import (
        release_stale_claims as _release,
    )

    released = _release(older_than_seconds)
    if released:
        logger.warning(
            "outbox.relay.stale_claims_released",
            count=released,
        )
    return released


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=300,
    time_limit=600,
    name="integration.events.prune_published",
)
def prune_published(retention_days: int | None = None) -> int:
    """Delete long-published rows so the hot table stays small.

    Deleted in bounded chunks rather than one statement: a single DELETE over
    millions of rows exceeds the statement timeout and holds locks for the
    duration (10-performance-concurrency.md section 11).
    """
    from django.conf import settings
    from django.utils import timezone

    from workeasy360.contracts.enums import OutboxStatus
    from workeasy360.integration.events.models import OutboxEvent

    days = retention_days or getattr(settings, "OUTBOX_RETENTION_DAYS", 30)
    cutoff = timezone.now() - timezone.timedelta(days=days)

    total = 0
    while True:
        ids = list(
            OutboxEvent.objects.filter(
                status=OutboxStatus.PUBLISHED.value, published_at__lte=cutoff
            ).values_list("id", flat=True)[:1000]
        )
        if not ids:
            break
        deleted, _ = OutboxEvent.objects.filter(id__in=ids).delete()
        total += deleted

    if total:
        logger.info("outbox.pruned", deleted=total)
    return total
