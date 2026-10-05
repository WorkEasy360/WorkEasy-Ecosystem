"""The analytics query: one metric, a range, a granularity, approved groupings
(Tasks 74-77, 79, 80, 108).

A query names **a registered metric** and nothing else a caller could turn
into SQL: no expression, no field name, no filter language. The statement is
fixed; the caller's choices are parameters - the metric key and version, the
range, ``hour``/``day``/``month``, and up to two of the metric's own
dimensions to group by (Task 74).

**Bounds** (Task 75), checked before the database is touched: a maximum range
per granularity (7 days hourly, 400 days daily, ~3 years monthly), aligned
bounds, and a worst-case row count (buckets x each grouped dimension's
enumerated values x currencies) no larger than ``ANALYTICS_MAX_ROWS``. Every
query is served by the ``(organization, metric, granularity, bucket)`` index
over pre-aggregated rows; nothing rescans facts.

**Who may query what** (Tasks 76, 77, 108):

- a product, over its credential: its own metrics, and metrics whose owner
  listed it in ``shared_with``. Anything else - another product's private
  metric, an unregistered key - is the same ``metric_not_found``;
- an organization administrator (``platform.analytics.view``): metrics owned
  by products the organization holds.

Every row is the organization's own: the organization comes from the
authenticated context, the statement filters on it, and RLS enforces it again
(Task 66).

**Money** (Task 79): a money metric's rows carry integer minor units and
their currency, and the currency is always part of the grouping - amounts in
different currencies are never summed into one number.

**Freshness** (Task 80): every answer says how many facts are still waiting to
be projected and since when, and when the metric's buckets last changed, so a
caller never mistakes a lagging aggregate for real time.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any, Final

from django.conf import settings
from django.db import connection
from django.db.models import Max, Min

from workeasy360.contracts.errors import AnalyticsQueryInvalid, MetricNotFound
from workeasy360.contracts.values import intelligence_keys as keys
from workeasy360.intelligence.analytics.extraction import OTHER, UNKNOWN
from workeasy360.intelligence.analytics.models import (
    AnalyticsAggregate,
    AnalyticsFact,
    Granularity,
)
from workeasy360.intelligence.analytics.registry import MetricDTO, resolve
from workeasy360.platform.observability.metrics import metrics

GRANULARITIES: Final[frozenset[str]] = frozenset({"hour", "day", "month"})
MAX_GROUP_BY: Final = 2

#: Fixed statements, one per number of grouped dimensions. ``date_trunc``'s
#: unit, the grouped dimension names and every bound are bound parameters; no
#: caller text is ever part of the statement (G7).
_SELECT: Final = (
    "SELECT date_trunc(%(unit)s, bucket_start AT TIME ZONE 'UTC') AS bucket, currency"
)
_FROM: Final = (
    " FROM analytics_aggregate"
    " WHERE organization_id = %(organization)s AND metric_key = %(key)s"
    " AND metric_version = %(version)s AND granularity = %(source)s"
    " AND bucket_start >= %(start)s AND bucket_start < %(end)s"
)
_TOTALS: Final = ", SUM(sum_value), SUM(event_count)"
_SQL_BY_GROUPS: Final[dict[int, str]] = {
    0: _SELECT + _TOTALS + _FROM + " GROUP BY 1, 2 ORDER BY 1, 2",
    1: _SELECT
    + ", dims ->> %(g0)s"
    + _TOTALS
    + _FROM
    + " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
    2: _SELECT
    + ", dims ->> %(g0)s, dims ->> %(g1)s"
    + _TOTALS
    + _FROM
    + " GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4",
}


def authorize_product(caller: str, key: Any, version: Any) -> MetricDTO:
    metric = resolve(key, version)
    if metric.owner != caller and caller not in metric.shared_with:
        metrics.increment("analytics_query_denied_total", reason="not_shared")
        raise MetricNotFound("No such metric.")
    return metric


def authorize_organization(organization_id: Any, key: Any, version: Any) -> MetricDTO:
    """An administrator may read metrics of products the organization holds."""
    from workeasy360.platform.entitlements.services import read as entitlements

    metric = resolve(key, version)
    held = {str(code) for code in entitlements.products(organization_id)}
    if metric.owner != keys.PLATFORM_ROOT and metric.owner not in held:
        metrics.increment("analytics_query_denied_total", reason="product_not_held")
        raise MetricNotFound("No such metric.")
    return metric


def run(
    metric: MetricDTO,
    *,
    organization_id: Any,
    start: Any,
    end: Any,
    granularity: Any,
    group_by: Any = None,
) -> dict[str, Any]:
    """Run a validated query for the organization in scope."""
    import time

    started = time.monotonic()
    window = _window(start, end, granularity)
    groups = _group_by(metric, group_by)
    _check_size(metric, window, groups)

    unit, source = _units(window.granularity)
    params: dict[str, Any] = {
        "unit": unit,
        "organization": organization_id,
        "key": metric.key,
        "version": metric.version,
        "source": source,
        "start": window.start,
        "end": window.end,
    }
    for index, name in enumerate(groups):
        params[f"g{index}"] = name
    with connection.cursor() as cur:
        cur.execute(_SQL_BY_GROUPS[len(groups)], params)
        rows = cur.fetchall()

    results = []
    for row in rows:
        bucket, currency = row[0], row[1]
        dimension_values = row[2 : 2 + len(groups)]
        total, count = int(row[-2] or 0), int(row[-1] or 0)
        value: Any = count if metric.aggregation == "count" else total
        entry: dict[str, Any] = {
            "bucket": bucket.replace(tzinfo=dt_timezone.utc).isoformat(),
            "dimensions": dict(zip(groups, dimension_values, strict=True)),
            "count": count,
        }
        if metric.is_money:
            entry["amount_minor"] = value
            entry["currency"] = currency
        else:
            entry["value"] = value
        results.append(entry)

    metrics.observe("analytics_query_duration_seconds", time.monotonic() - started)
    return {
        "metric": {
            "key": metric.key,
            "version": metric.version,
            "unit": metric.unit,
            "aggregation": metric.aggregation,
            "value_type": metric.value_type,
        },
        "granularity": window.granularity,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "group_by": groups,
        "results": results,
        "freshness": freshness(metric),
    }


def freshness(metric: MetricDTO) -> dict[str, Any]:
    pending = AnalyticsFact.objects.filter(
        projected_at__isnull=True, metric_key=metric.key, metric_version=metric.version
    ).aggregate(n=Min("created_at"))
    count = (
        AnalyticsFact.objects.filter(
            projected_at__isnull=True,
            metric_key=metric.key,
            metric_version=metric.version,
        ).count()
        if pending["n"] is not None
        else 0
    )
    last = AnalyticsAggregate.objects.filter(
        metric_key=metric.key, metric_version=metric.version
    ).aggregate(t=Max("updated_at"))["t"]
    return {
        "pending_facts": count,
        "oldest_pending_since": pending["n"].isoformat() if pending["n"] else None,
        "last_projected_at": last.isoformat() if last else None,
        "up_to_date": count == 0,
    }


# --- validation ------------------------------------------------------------------------


class _Window:
    __slots__ = ("end", "granularity", "start")

    def __init__(self, start: datetime, end: datetime, granularity: str) -> None:
        self.start = start
        self.end = end
        self.granularity = granularity


def _window(start: Any, end: Any, granularity: Any) -> _Window:
    if granularity not in GRANULARITIES:
        raise AnalyticsQueryInvalid(
            "granularity must be hour, day or month.", field="granularity"
        )
    begin = _instant(start, "start")
    finish = _instant(end, "end")
    if finish <= begin:
        raise AnalyticsQueryInvalid("end must be after start.", field="end")
    if granularity == "hour" and (begin.minute or begin.second or begin.microsecond):
        raise AnalyticsQueryInvalid("start must be on an hour.", field="start")
    if granularity in ("day", "month") and (
        begin.hour or begin.minute or begin.second or begin.microsecond
    ):
        raise AnalyticsQueryInvalid("start must be at midnight UTC.", field="start")
    if granularity == "month" and begin.day != 1:
        raise AnalyticsQueryInvalid("start must be the first of a month.", field="start")
    limit_days = int(settings.ANALYTICS_MAX_RANGE_DAYS[granularity])
    if (finish - begin).total_seconds() > limit_days * 86_400:
        raise AnalyticsQueryInvalid(
            f"A {granularity} query may span at most {limit_days} days.", field="end"
        )
    return _Window(begin, finish, granularity)


def _instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise AnalyticsQueryInvalid(
            f"{field} must be an ISO 8601 timestamp.", field=field
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AnalyticsQueryInvalid(
            f"{field} must be an ISO 8601 timestamp.", field=field
        ) from None
    if parsed.tzinfo is None:
        raise AnalyticsQueryInvalid(f"{field} must include a timezone.", field=field)
    return parsed.astimezone(dt_timezone.utc)


def _group_by(metric: MetricDTO, raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    names = raw.split(",") if isinstance(raw, str) else raw
    if (
        not isinstance(names, list)
        or len(names) > MAX_GROUP_BY
        or len(set(names)) != len(names)
        or not all(isinstance(n, str) and metric.dimension(n) is not None for n in names)
    ):
        raise AnalyticsQueryInvalid(
            f"group_by may name at most {MAX_GROUP_BY} of the metric's own dimensions.",
            field="group_by",
        )
    return list(names)


def _check_size(metric: MetricDTO, window: _Window, groups: list[str]) -> None:
    seconds = (window.end - window.start).total_seconds()
    span = {"hour": 3600, "day": 86_400, "month": 28 * 86_400}[window.granularity]
    buckets = int(seconds // span) + 1
    rows = buckets
    for name in groups:
        dimension = metric.dimension(name)
        rows *= len(dimension.values) + len({OTHER, UNKNOWN}) if dimension else 1
    if metric.is_money:
        rows *= 8  # the supported currencies
    if rows > int(settings.ANALYTICS_MAX_ROWS):
        raise AnalyticsQueryInvalid(
            "The query could return too many rows. Narrow the range, coarsen the "
            "granularity or group by fewer dimensions.",
            field="group_by",
        )


def _units(granularity: str) -> tuple[str, str]:
    if granularity == "hour":
        return "hour", Granularity.HOUR.value
    return granularity, Granularity.DAY.value
