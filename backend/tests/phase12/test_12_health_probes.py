"""Orchestrator probes under production transport settings (Phase 12, G-05).

A load balancer probes over plain http with the task's own address as ``Host``.
With ``SECURE_SSL_REDIRECT`` and a real ``ALLOWED_HOSTS`` list, Django answered
that probe 301 or 400 - no instance could ever become healthy. The probe paths
are now answered before both checks; every other path still goes through them.
"""

from __future__ import annotations

import pytest
from django.db import OperationalError

from workeasy360.platform.observability import health

pytestmark = pytest.mark.django_db


@pytest.fixture
def production_transport(settings):
    settings.SECURE_SSL_REDIRECT = True
    settings.ALLOWED_HOSTS = ["app.workeasy360.com"]
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    health._readiness_cache.update({"at": 0.0, "payload": None, "ok": False})
    yield
    health._readiness_cache.update({"at": 0.0, "payload": None, "ok": False})


@pytest.mark.parametrize("path", ["/healthz/live", "/healthz/ready", "/healthz/startup"])
def test_a_plain_http_probe_by_ip_is_answered(client, production_transport, path):
    response = client.get(path, HTTP_HOST="10.0.3.7:8000")
    assert response.status_code == 200, response.content
    # The security headers still apply to the probe's response.
    assert response["X-Content-Type-Options"] == "nosniff"


def test_other_paths_still_redirect_and_validate_the_host(client, production_transport):
    assert (
        client.get("/api/v1/auth/session", HTTP_HOST="app.workeasy360.com").status_code
        == 301
    )
    assert (
        client.get("/api/v1/auth/session", HTTP_HOST="10.0.3.7:8000").status_code == 400
    )
    assert client.get("/healthz/deep", HTTP_HOST="10.0.3.7:8000").status_code == 400


def test_a_failed_probe_names_no_internal_topology(
    client, production_transport, monkeypatch
):
    def unreachable() -> str:
        raise OperationalError(
            'connection to server at "pgbouncer" (10.0.3.9), port 6432 failed'
        )

    monkeypatch.setattr(health, "_probe_database", unreachable)
    response = client.get("/healthz/ready", HTTP_HOST="10.0.3.7:8000")
    assert response.status_code == 503
    body = response.content.decode()
    assert "pgbouncer" not in body and "6432" not in body and "10.0.3.9" not in body
    assert "OperationalError" in body


def test_startup_probe_includes_the_timeout_and_ownership_checks(
    client, production_transport
):
    response = client.get("/healthz/startup", HTTP_HOST="10.0.3.7:8000")
    names = {c["name"] for c in response.json()["checks"]}
    assert {"rls_role", "db_timeouts"} <= names
    assert response.json()["status"] == "started"
