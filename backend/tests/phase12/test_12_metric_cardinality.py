"""Phase 12: telemetry never fails the request it describes.

The HTTP metrics are labelled route x method x status class. With 277 routes
that legitimately passes the 500-combination ceiling on a long-lived process,
and the method is client-supplied (any token passes the edge). The guard used
to *raise* there, turning requests into 500s - found by the D-037 structural
test. Production now drops the new series and logs once; tests and
development still fail loudly.
"""

from __future__ import annotations

import pytest

from workeasy360.platform.observability.metrics import (
    HTTP_REQUESTS,
    MAX_SERIES_PER_METRIC,
    CardinalityError,
    MetricRegistry,
    metrics,
)

pytestmark = pytest.mark.django_db


def test_strict_mode_still_raises(settings):
    settings.METRICS_STRICT_CARDINALITY = True
    registry = MetricRegistry()
    with pytest.raises(CardinalityError):
        for i in range(MAX_SERIES_PER_METRIC + 1):
            registry.increment("leaky_total", route=f"/r/{i}")


def test_production_drops_the_overflow_and_keeps_counting_known_series(settings):
    settings.METRICS_STRICT_CARDINALITY = False
    registry = MetricRegistry()
    for i in range(MAX_SERIES_PER_METRIC):
        registry.increment("busy_total", route=f"/r/{i}")
    registry.increment("busy_total", route="/r/new-1")  # dropped, no raise
    registry.increment("busy_total", route="/r/new-2")
    registry.observe("busy_total", 0.1, route="/r/new-3")
    registry.increment("busy_total", route="/r/0")  # an existing series still counts

    assert registry.value("busy_total", route="/r/0") == 2.0
    assert registry.value("busy_total", route="/r/new-1") == 0.0
    assert len(registry.snapshot()["busy_total"]) == MAX_SERIES_PER_METRIC


def test_an_unknown_http_method_is_one_label_value(client):
    metrics.reset()
    for verb in ("BREW", "PROPFIND", "XYZZY"):
        client.generic(verb, "/api/v1/auth/session")
    keys = [k for k in metrics.snapshot().get(HTTP_REQUESTS, {}) if "method=" in k]
    methods = {
        part.split("=", 1)[1]
        for k in keys
        for part in k.split(",")
        if part.startswith("method=")
    }
    assert methods == {"OTHER"}


def test_a_full_registry_never_turns_a_request_into_a_500(client, settings):
    settings.METRICS_STRICT_CARDINALITY = False
    metrics.reset()
    for i in range(MAX_SERIES_PER_METRIC):
        metrics.increment(
            HTTP_REQUESTS, method="GET", route=f"/filler/{i}", status_class="2xx"
        )
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    metrics.reset()
