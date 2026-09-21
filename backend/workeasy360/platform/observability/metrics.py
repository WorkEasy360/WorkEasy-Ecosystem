"""Metric hooks with a bounded-cardinality guard.

Phase 1 ships an in-process registry rather than a Prometheus client. The
reason is the one in 10-performance-concurrency.md section 1: do not commit to
infrastructure before it is justified. What *is* justified now is the
**interface** and the **cardinality discipline**, because both are expensive
to retrofit - every call site would have to change, and by then the labels
would already be wrong.

The rule this enforces mechanically: ``organization_id`` is never a metric
label. With 10,000 tenants and 20 metrics that is 200,000 series. Per-tenant
analysis belongs in traces, logs and the analytics store
(11-observability.md section 6).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final

#: Labels that would explode cardinality. Rejected at call time rather than
#: discovered on a metrics bill.
FORBIDDEN_LABELS: Final[frozenset[str]] = frozenset(
    {
        "organization_id",
        "org_id",
        "user_id",
        "membership_id",
        "email",
        "request_id",
        "correlation_id",
        "trace_id",
        "path",  # the resolved path; use the templated `route` instead
        "id",
        "resource_id",
    }
)

#: Ceiling on distinct label combinations per metric. A metric that exceeds it
#: is a design error; failing loudly in development beats degrading silently.
MAX_SERIES_PER_METRIC: Final = 500


class CardinalityError(ValueError):
    """A metric was emitted with a forbidden or unbounded label."""


def _validate_labels(name: str, labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    for key in labels:
        if key in FORBIDDEN_LABELS:
            raise CardinalityError(
                f"Metric {name!r} used forbidden label {key!r}. High-cardinality "
                f"identifiers must not be metric labels - they belong on spans "
                f"and log lines (11-observability.md section 6)."
            )
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


@dataclass
class _Series:
    counts: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    observations: dict[tuple[tuple[str, str], ...], list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )


class MetricRegistry:
    """Thread-safe in-process metrics.

    Swapping in a real backend means reimplementing this class; no call site
    changes, which is the point of introducing it now.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, _Series] = defaultdict(_Series)

    def increment(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = _validate_labels(name, labels)
        with self._lock:
            series = self._metrics[name]
            self._guard_series_count(name, series, key)
            series.counts[key] = series.counts.get(key, 0.0) + value

    def observe(self, name: str, value: float, **labels: str) -> None:
        """Record a duration or size for later percentile computation."""
        key = _validate_labels(name, labels)
        with self._lock:
            series = self._metrics[name]
            self._guard_series_count(name, series, key)
            series.observations[key].append(value)

    def gauge(self, name: str, value: float, **labels: str) -> None:
        key = _validate_labels(name, labels)
        with self._lock:
            series = self._metrics[name]
            self._guard_series_count(name, series, key)
            series.counts[key] = value

    @staticmethod
    def _guard_series_count(
        name: str, series: _Series, key: tuple[tuple[str, str], ...]
    ) -> None:
        if key in series.counts or key in series.observations:
            return
        distinct = len(set(series.counts) | set(series.observations))
        if distinct >= MAX_SERIES_PER_METRIC:
            raise CardinalityError(
                f"Metric {name!r} exceeded {MAX_SERIES_PER_METRIC} label "
                f"combinations. One of its labels is unbounded."
            )

    # --- reading ----------------------------------------------------------

    def value(self, name: str, **labels: str) -> float:
        key = _validate_labels(name, labels)
        with self._lock:
            return self._metrics[name].counts.get(key, 0.0)

    def observations(self, name: str, **labels: str) -> list[float]:
        key = _validate_labels(name, labels)
        with self._lock:
            return list(self._metrics[name].observations.get(key, []))

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Flattened view, for the deep health endpoint and tests."""
        out: dict[str, dict[str, float]] = {}
        with self._lock:
            for name, series in self._metrics.items():
                bucket: dict[str, float] = {}
                for key, count in series.counts.items():
                    bucket[_render(key)] = count
                for key, values in series.observations.items():
                    if values:
                        bucket[f"{_render(key)}|count"] = float(len(values))
                        bucket[f"{_render(key)}|sum"] = float(sum(values))
                out[name] = bucket
        return out

    def reset(self) -> None:
        """Test hook. Never called in production."""
        with self._lock:
            self._metrics.clear()


def _render(key: tuple[tuple[str, str], ...]) -> str:
    return ",".join(f"{k}={v}" for k, v in key) or "_"


#: Process-wide registry.
metrics: Final = MetricRegistry()


@contextmanager
def timed(name: str, **labels: str) -> Iterator[None]:
    """Observe the wall-clock duration of a block, in seconds."""
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics.observe(name, time.perf_counter() - started, **labels)


# --- Named metrics -------------------------------------------------------
#
# The three correctness alarms from 11-observability.md section 6. They are
# not performance metrics: any non-zero value pages, regardless of volume,
# because each means a code path can run unscoped or a tenant boundary has
# been crossed.

TENANT_CONTEXT_MISSING: Final = "tenant_context_missing_total"
RLS_VIOLATION: Final = "rls_violation_total"
UNSCOPED_QUERY: Final = "unscoped_query_total"

ENTITLEMENT_CHECKS: Final = "entitlement_checks_total"
ENTITLEMENT_CACHE_HITS: Final = "entitlement_cache_hits_total"
ENTITLEMENT_CACHE_MISSES: Final = "entitlement_cache_misses_total"
ENTITLEMENT_CHECK_DURATION: Final = "entitlement_check_duration_seconds"

AUTHZ_CHECKS: Final = "authz_checks_total"
AUTHZ_DENIALS: Final = "authz_denials_total"

HTTP_REQUESTS: Final = "http_requests_total"
HTTP_REQUEST_DURATION: Final = "http_request_duration_seconds"

TASK_FAILURES: Final = "task_failures_total"
TASK_RETRIES: Final = "task_retries_total"

OUTBOX_PENDING: Final = "outbox_pending_count"
OUTBOX_PUBLISHED: Final = "outbox_published_total"
OUTBOX_DEAD_LETTERED: Final = "outbox_dead_lettered_total"

NOTIFICATION_SENT: Final = "notification_sent_total"
NOTIFICATION_FAILED: Final = "notification_failed_total"

WRITE_BLOCKED_SUSPENDED: Final = "write_blocked_suspended_total"
