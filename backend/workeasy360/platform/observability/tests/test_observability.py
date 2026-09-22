"""Observability: health semantics, correlation, redaction, cardinality."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.django_db


# --- health endpoints -----------------------------------------------------


def test_liveness_checks_nothing(client):
    """Liveness must never check dependencies.

    If it does, a brief database blip restarts every container at once,
    turning a 30-second degradation into a full outage with cold caches
    (11-observability.md section 7).
    """
    response = client.get("/healthz/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_liveness_survives_a_database_outage(client, monkeypatch):
    """The specific failure the rule exists to prevent."""
    from django.db import connection

    def explode(*args, **kwargs):
        raise ConnectionError("database is gone")

    monkeypatch.setattr(connection, "cursor", explode)

    response = client.get("/healthz/live")
    assert response.status_code == 200


def test_liveness_issues_no_queries(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        client.get("/healthz/live")


def test_readiness_reports_dependencies(client):
    response = client.get("/healthz/ready")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ready"
    names = {check["name"] for check in payload["checks"]}
    assert {"postgresql", "migrations", "cache"} <= names


def test_readiness_does_not_fail_on_a_cache_outage(client, monkeypatch):
    """Redis is Tier 1 with a documented degradation path.

    Pulling every instance out of the load balancer over a cache outage would
    convert a slowdown into an outage.
    """
    from django.core import cache as cache_module

    from workeasy360.platform.observability import health

    health._readiness_cache.update({"at": 0.0, "payload": None, "ok": False})

    def explode(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(cache_module.cache, "set", explode)
    monkeypatch.setattr(cache_module.cache, "get", explode)

    response = client.get("/healthz/ready")
    assert response.status_code == 200

    cache_check = next(c for c in response.json()["checks"] if c["name"] == "cache")
    assert cache_check["ok"] is False  # reported...
    assert response.json()["status"] == "ready"  # ...but not blocking


def test_readiness_is_cached_briefly(client, settings):
    """A health-check storm must not itself become database load."""
    from workeasy360.platform.observability import health

    settings.READINESS_CACHE_SECONDS = 30
    health._readiness_cache.update({"at": 0.0, "payload": None, "ok": False})

    client.get("/healthz/ready")
    first = health._readiness_cache["at"]
    client.get("/healthz/ready")
    assert health._readiness_cache["at"] == first


def test_startup_verifies_the_rls_role(client):
    """A container whose role can bypass RLS has no tenant isolation and must
    not serve traffic (R-02)."""
    response = client.get("/healthz/startup")
    assert response.status_code == 200

    names = {c["name"] for c in response.json()["checks"]}
    assert "rls_role" in names, (
        "platform/tenancy must register its RLS role probe at app-ready."
    )
    rls = next(c for c in response.json()["checks"] if c["name"] == "rls_role")
    assert rls["ok"]


def test_deep_health_requires_staff(client, user_factory):
    """An unauthenticated deep probe would disclose infrastructure topology."""
    assert client.get("/healthz/deep").status_code == 404

    staff = user_factory(is_staff=True)
    client.force_login(staff)
    assert client.get("/healthz/deep").status_code == 200


def test_health_endpoints_need_no_tenant_context(client):
    """They are infrastructure contracts with the orchestrator, not tenant
    API, and must answer before any tenant exists."""
    for path in ("/healthz/live", "/healthz/ready", "/healthz/startup"):
        assert client.get(path).status_code in (200, 503)


# --- correlation ----------------------------------------------------------


def test_correlation_id_is_generated_and_returned(client):
    """Returned so a customer reporting "this failed" can quote an id that
    locates the exact request."""
    response = client.get("/healthz/live")
    assert response["X-Correlation-ID"]
    uuid.UUID(response["X-Correlation-ID"])  # parses


def test_client_supplied_correlation_id_is_honoured(client):
    supplied = str(uuid.uuid4())
    response = client.get("/healthz/live", HTTP_X_CORRELATION_ID=supplied)
    assert response["X-Correlation-ID"] == supplied


def test_malformed_correlation_id_is_replaced_not_propagated(client):
    """An unvalidated client string reaching log aggregation is a
    log-injection vector, and an unbounded one bloats every log line."""
    response = client.get("/healthz/live", HTTP_X_CORRELATION_ID="not-a-uuid<script>")
    returned = response["X-Correlation-ID"]
    assert "script" not in returned
    uuid.UUID(returned)


def test_correlation_survives_the_task_boundary():
    """Contextvars do not cross into a worker, so the id travels in headers."""
    from workeasy360.platform.observability.correlation import (
        correlation_scope,
        current_correlation_id,
        set_correlation_id,
    )

    with correlation_scope() as correlation_id:
        serialised = str(current_correlation_id())

    assert serialised == str(correlation_id)

    set_correlation_id(serialised)
    assert str(current_correlation_id()) == serialised
    set_correlation_id(None)


# --- redaction ------------------------------------------------------------


def test_secrets_are_redacted_from_log_events():
    """A backstop, not a licence to log secrets - it is there because one day
    someone will (11-observability.md section 4)."""
    from workeasy360.platform.observability.logging import _redact

    out = _redact(
        None,
        "info",
        {
            "event": "auth.login",
            "password": "hunter2",
            "session_id": "abc",
            "api_key": "w360_live_x",
            "nested": {"authorization": "Bearer y"},
            "organization_id": "safe",
        },
    )
    assert out["password"] == "[redacted]"
    assert out["session_id"] == "[redacted]"
    assert out["api_key"] == "[redacted]"
    assert out["nested"]["authorization"] == "[redacted]"
    assert out["organization_id"] == "safe"


def test_pii_is_redacted_from_log_events():
    """Log identifiers; join to the data when authorised."""
    from workeasy360.platform.observability.logging import _redact

    out = _redact(
        None,
        "info",
        {"email": "a@b.com", "full_name": "Ada", "user_id": "018f-..."},
    )
    assert out["email"] == "[pii]"
    assert out["full_name"] == "[pii]"
    assert out["user_id"] == "018f-..."  # an identifier, not PII


def test_tenant_context_reaches_log_events(two_tenants, in_tenant):
    """organization_id must be on every log line in tenant context, supplied
    through the contracts registry rather than an import (F-5)."""
    from workeasy360.contracts.ports.observability import (
        collect_log_context,
        registered_providers,
    )

    assert "tenancy" in registered_providers()

    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        fields = collect_log_context()

    assert fields["organization_id"] == str(a["org"].id)
    assert "reason" not in fields  # operator free text belongs in audit


def test_log_context_provider_failure_does_not_break_logging():
    """A logging failure that breaks the request it was describing is worse
    than a missing field."""
    from workeasy360.contracts.ports.observability import (
        collect_log_context,
        register_log_context_provider,
    )

    def broken():
        raise RuntimeError("provider exploded")

    register_log_context_provider("broken-canary", broken)
    try:
        assert isinstance(collect_log_context(), dict)
    finally:
        register_log_context_provider("broken-canary", dict)


# --- metrics cardinality --------------------------------------------------


def test_high_cardinality_labels_are_refused():
    """organization_id is never a metric label: 10k tenants x 20 metrics is
    200k series. Per-tenant analysis belongs in traces and logs."""
    from workeasy360.platform.observability.metrics import (
        CardinalityError,
        metrics,
    )

    with pytest.raises(CardinalityError):
        metrics.increment("test_total", organization_id=str(uuid.uuid4()))
    with pytest.raises(CardinalityError):
        metrics.increment("test_total", user_id="x")
    with pytest.raises(CardinalityError):
        metrics.increment("test_total", path="/api/v1/orgs/acme/things/123")


def test_bounded_labels_are_accepted():
    from workeasy360.platform.observability.metrics import metrics

    metrics.increment("http_requests_total", method="GET", status_class="2xx")
    assert metrics.value("http_requests_total", method="GET", status_class="2xx") == 1


def test_unbounded_label_values_are_caught():
    """A forbidden *name* is not the only way to explode cardinality; an
    unbounded value under an innocuous name does it too."""
    from workeasy360.platform.observability.metrics import (
        MAX_SERIES_PER_METRIC,
        CardinalityError,
        metrics,
    )

    with pytest.raises(CardinalityError):
        for i in range(MAX_SERIES_PER_METRIC + 5):
            metrics.increment("leaky_total", route=f"/thing/{i}")


def test_route_label_uses_the_url_pattern(client):
    """Never the resolved path, or every object id becomes a series."""
    from workeasy360.platform.observability.metrics import HTTP_REQUESTS, metrics

    client.get("/healthz/live")
    snapshot = metrics.snapshot().get(HTTP_REQUESTS, {})
    assert snapshot
    for key in snapshot:
        assert "018f" not in key


# --- declared metrics are actually emitted --------------------------------
#
# 11-observability.md §6 names three correctness alarms and says any non-zero
# value pages. Naming them in the registry module is not emitting them, and
# five of the declared constants had no call site at all - including the one
# `unscoped.py` told the reader it was incrementing. A control that is
# documented and not wired is worse than one that is neither, because it is
# believed.


def test_every_named_metric_has_an_emitter():
    """A constant with no call site is a metric that will never fire.

    Asserted over the source, so adding a name to the registry without wiring
    it fails here rather than during an incident.
    """
    import ast
    import pathlib

    from workeasy360.platform.observability import metrics as metrics_module

    root = pathlib.Path(metrics_module.__file__).resolve().parents[3]
    declared = {
        name
        for name, value in vars(metrics_module).items()
        if name.isupper()
        and isinstance(value, str)
        and value.endswith(("_total", "_seconds", "_count"))
    }

    used: set[str] = set()
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.endswith("observability/metrics.py"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in declared:
                used.add(node.id)

    assert not (declared - used), (
        "Declared metrics with no emitter anywhere: "
        f"{sorted(declared - used)}. Either wire them or remove the constant."
    )
