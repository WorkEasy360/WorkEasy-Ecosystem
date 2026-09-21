"""Root routing. Composition only - no business logic (01 section 9).

A code reviewer who sees a domain rule in interfaces/ rejects the PR.
"""

from __future__ import annotations

from django.urls import include, path

from workeasy360.platform.observability import health

urlpatterns = [
    # Health endpoints sit outside the API version namespace: they are
    # infrastructure contracts with the orchestrator, not product API, and
    # must not move when the API version does.
    path("healthz/live", health.liveness, name="healthz-live"),
    path("healthz/ready", health.readiness, name="healthz-ready"),
    path("healthz/startup", health.startup, name="healthz-startup"),
    path("healthz/deep", health.deep, name="healthz-deep"),
    path("api/v1/", include("workeasy360.interfaces.api.v1.urls")),
]
