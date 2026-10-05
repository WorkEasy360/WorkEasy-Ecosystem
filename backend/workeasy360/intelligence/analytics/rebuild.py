"""Rebuild and reconcile aggregates from facts (Tasks 71, 80).

Aggregates are a projection; facts are the record. ``rebuild`` deletes an
organization's aggregates (for one metric, or all) inside the retained fact
window and recomputes them from facts in one statement per granularity, under
the same advisory lock the projection takes - so a rebuild and a projection
can never interleave, and a projection failure, a bug fixed later or a
hand-edited row can always be repaired from authoritative data.

``reconcile`` recomputes without writing and reports every bucket whose
stored sum or count differs from what the facts say: the drift check an
operator runs before, or instead of, a rebuild.

Buckets older than the fact retention window (``ANALYTICS_FACT_RETENTION_DAYS``)
have no facts left to rebuild from; they are left as they are and are final.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from workeasy360.intelligence.analytics.projection import LOCK_SQL, lock_key

#: ``%(granularity)s`` is 'hour' or 'day' - a bound value, used both as the
#: stored label and as ``date_trunc``'s unit.
_RECOMPUTE: Final = """
    SELECT metric_key, metric_version,
           date_trunc(%(granularity)s, occurred_at AT TIME ZONE 'UTC') AS bucket,
           dims_key, currency, any_value(dims) AS dims,
           SUM(value) AS total, COUNT(*) AS n
    FROM analytics_fact
    WHERE organization_id = %(organization)s
      AND occurred_at >= %(since)s
      AND (%(metric)s = '' OR metric_key = %(metric)s)
    GROUP BY metric_key, metric_version, bucket, dims_key, currency
"""

_INSERT: Final = """
    INSERT INTO analytics_aggregate
        (id, organization_id, metric_key, metric_version, granularity,
         bucket_start, dims_key, dims, currency, sum_value, event_count,
         created_at, updated_at)
    SELECT gen_random_uuid(), %(organization)s, r.metric_key, r.metric_version,
           %(granularity)s, r.bucket AT TIME ZONE 'UTC', r.dims_key, r.dims,
           r.currency, r.total, r.n, now(), now()
    FROM ({recompute}) AS r
"""

_DRIFT: Final = """
    SELECT COALESCE(a.metric_key, r.metric_key), COALESCE(a.bucket_start, r.bucket AT TIME ZONE 'UTC'),
           COALESCE(a.dims_key, r.dims_key), COALESCE(a.currency, r.currency),
           a.sum_value, r.total, a.event_count, r.n
    FROM (
        SELECT metric_key, metric_version, bucket_start, dims_key, currency,
               sum_value, event_count
        FROM analytics_aggregate
        WHERE organization_id = %(organization)s AND granularity = %(granularity)s
          AND bucket_start >= %(since)s
          AND (%(metric)s = '' OR metric_key = %(metric)s)
    ) AS a
    FULL OUTER JOIN ({recompute}) AS r
      ON r.metric_key = a.metric_key AND r.metric_version = a.metric_version
     AND r.bucket AT TIME ZONE 'UTC' = a.bucket_start
     AND r.dims_key = a.dims_key AND r.currency = a.currency
    WHERE a.sum_value IS DISTINCT FROM r.total OR a.event_count IS DISTINCT FROM r.n
    LIMIT 100
"""


def _since() -> Any:
    days = int(settings.ANALYTICS_FACT_RETENTION_DAYS)
    return (timezone.now() - timezone.timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


@contextmanager
def _organization(organization_id: UUID) -> Iterator[None]:
    """The operator entry points establish their own trusted context (Task
    81): the organization's live status, a nil actor, its database scope."""
    from workeasy360.contracts.errors import MetricNotFound
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    ctx = routing_context(organization_id)
    if ctx is None:
        raise MetricNotFound("No such organization.")
    with tenant_context(ctx), organization_db_scope(organization_id):
        yield


def rebuild(organization_id: UUID, *, metric_key: str = "") -> dict[str, int]:
    """Recreate the organization's aggregates from its facts."""
    from workeasy360.intelligence.analytics.models import (
        AnalyticsAggregate,
        AnalyticsFact,
    )

    since = _since()
    written: dict[str, int] = {}
    with _organization(organization_id), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(LOCK_SQL, [lock_key(organization_id)])
        stale = AnalyticsAggregate.objects.filter(bucket_start__gte=since)
        if metric_key:
            stale = stale.filter(metric_key=metric_key)
        deleted, _ = stale.delete()
        for granularity in ("hour", "day"):
            with connection.cursor() as cur:
                cur.execute(
                    _INSERT.format(recompute=_RECOMPUTE),
                    {
                        "organization": organization_id,
                        "granularity": granularity,
                        "since": since,
                        "metric": metric_key,
                    },
                )
                written[granularity] = cur.rowcount
        pending = AnalyticsFact.objects.filter(projected_at__isnull=True)
        if metric_key:
            pending = pending.filter(metric_key=metric_key)
        marked = pending.update(projected_at=timezone.now())
    return {"deleted": deleted, **written, "facts_marked": marked}


def reconcile(organization_id: UUID, *, metric_key: str = "") -> list[dict[str, Any]]:
    """Buckets whose stored aggregate disagrees with the facts. Read-only."""
    drift = []
    for granularity in ("hour", "day"):
        with _organization(organization_id), connection.cursor() as cur:
            cur.execute(
                _DRIFT.format(recompute=_RECOMPUTE),
                {
                    "organization": organization_id,
                    "granularity": granularity,
                    "since": _since(),
                    "metric": metric_key,
                },
            )
            for (
                key,
                bucket,
                dims_key,
                currency,
                stored,
                actual,
                stored_n,
                actual_n,
            ) in cur.fetchall():
                drift.append(
                    {
                        "granularity": granularity,
                        "metric": key,
                        "bucket": bucket.isoformat() if bucket else None,
                        "dimensions": dims_key,
                        "currency": currency,
                        "stored": {"sum": stored, "count": stored_n},
                        "from_facts": {"sum": actual, "count": actual_n},
                    }
                )
    return drift
