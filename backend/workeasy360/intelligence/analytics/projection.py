"""Facts to aggregates, exactly once (Tasks 69, 70, 72, 98).

One organization at a time, in one transaction, under a per-organization
advisory lock the rebuild takes too:

1. take up to ``ANALYTICS_PROJECTION_BATCH`` unprojected facts (oldest
   received first);
2. sum them in memory per (metric, version, granularity, bucket, dimensions,
   currency) for the **hour** and the **day** (UTC) of ``occurred_at``;
3. add those sums to the aggregate rows with one ``INSERT ... ON CONFLICT DO
   UPDATE`` - atomic per row;
4. mark the facts projected.

Steps 3 and 4 commit together or not at all, so a fact is added to its
buckets exactly once: a crash rolls both back and the timer runs it again; a
duplicate dispatch finds nothing unprojected. A **late event** is bucketed by
when it *occurred*, so it lands in - and corrects - the historical hour and
day it belongs to, exactly once (Task 72). Only new facts are read: a query
never rescans history (Task 70).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from workeasy360.intelligence.analytics.consumer import PROJECT_TIMER
from workeasy360.intelligence.analytics.models import AnalyticsFact, Granularity
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: The advisory lock the projection and the rebuild share, per organization.
LOCK_SQL: Final = "SELECT pg_advisory_xact_lock(hashtext(%s))"

#: One statement per batch. The VALUES list repeats a fixed placeholder
#: group; nothing from a fact is interpolated (G7).
_UPSERT_SQL: Final = """
    INSERT INTO analytics_aggregate
        (id, organization_id, metric_key, metric_version, granularity,
         bucket_start, dims_key, dims, currency, sum_value, event_count,
         created_at, updated_at)
    VALUES {values}
    ON CONFLICT (organization_id, metric_key, metric_version, granularity,
                 bucket_start, dims_key, currency)
    DO UPDATE SET sum_value = analytics_aggregate.sum_value + EXCLUDED.sum_value,
                  event_count = analytics_aggregate.event_count + EXCLUDED.event_count,
                  updated_at = EXCLUDED.updated_at
"""
_ROW: Final = "(gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)"


def lock_key(organization_id: UUID) -> str:
    return f"analytics:{organization_id}"


def buckets(moment: datetime) -> tuple[tuple[str, datetime], tuple[str, datetime]]:
    """The UTC hour and day an occurrence belongs to."""
    hour = moment.astimezone(dt_timezone.utc).replace(minute=0, second=0, microsecond=0)
    return (Granularity.HOUR.value, hour), (Granularity.DAY.value, hour.replace(hour=0))


def project(organization_id: UUID) -> str:
    """Project one batch for the organization in scope. Returns ``done``,
    ``more`` (another batch is waiting) or ``nothing``."""
    from workeasy360.integration.scheduling import services as scheduling

    batch = int(settings.ANALYTICS_PROJECTION_BATCH)
    now = timezone.now()
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(LOCK_SQL, [lock_key(organization_id)])
        facts = list(
            AnalyticsFact.objects.filter(projected_at__isnull=True)
            .order_by("created_at", "id")
            .values(
                "id",
                "metric_key",
                "metric_version",
                "occurred_at",
                "dims_key",
                "dims",
                "currency",
                "value",
                "created_at",
            )[:batch]
        )
        if not facts:
            scheduling.complete(PROJECT_TIMER, organization_id)
            return "nothing"

        sums: dict[tuple[Any, ...], list[Any]] = defaultdict(lambda: [0, 0, None])
        for fact in facts:
            for granularity, bucket in buckets(fact["occurred_at"]):
                slot = sums[
                    (
                        fact["metric_key"],
                        fact["metric_version"],
                        granularity,
                        bucket,
                        fact["dims_key"],
                        fact["currency"],
                    )
                ]
                slot[0] += fact["value"]
                slot[1] += 1
                slot[2] = fact["dims"]

        params: list[Any] = []
        for (key, version, granularity, bucket, dims_key, currency), (
            total,
            count,
            dims,
        ) in sums.items():
            params.extend(
                [
                    organization_id,
                    key,
                    version,
                    granularity,
                    bucket,
                    dims_key,
                    json.dumps(dims or {}, sort_keys=True),
                    currency,
                    total,
                    count,
                    now,
                    now,
                ]
            )
        with connection.cursor() as cur:
            cur.execute(_UPSERT_SQL.format(values=", ".join([_ROW] * len(sums))), params)
        AnalyticsFact.objects.filter(id__in=[f["id"] for f in facts]).update(
            projected_at=now
        )

        more = len(facts) == batch
        if more:
            scheduling.schedule(
                PROJECT_TIMER,
                organization_id=organization_id,
                target_id=organization_id,
                due_at=now,
            )
        else:
            scheduling.complete(PROJECT_TIMER, organization_id)

    oldest = min(f["created_at"] for f in facts)
    metrics.observe("analytics_projection_lag_seconds", (now - oldest).total_seconds())
    metrics.increment("analytics_facts_projected_total", float(len(facts)))
    logger.info("analytics.projected", facts=len(facts), buckets=len(sums), more=more)
    return "more" if more else "done"
