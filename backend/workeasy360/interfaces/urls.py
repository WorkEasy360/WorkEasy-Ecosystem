"""Root routing. Composition only - no business logic (01 section 9).

A code reviewer who sees a domain rule in interfaces/ rejects the PR.
"""

from __future__ import annotations

from django.urls import include, path

from workeasy360.interfaces.api.v1 import ecosystem_views
from workeasy360.platform.observability import health

urlpatterns = [
    # Health endpoints sit outside the API version namespace: they are
    # infrastructure contracts with the orchestrator, not product API, and
    # must not move when the API version does.
    path("healthz/live", health.liveness, name="healthz-live"),
    path("healthz/ready", health.readiness, name="healthz-ready"),
    path("healthz/startup", health.startup, name="healthz-startup"),
    path("healthz/deep", health.deep, name="healthz-deep"),
    # OpenID Connect discovery and the key set live at the issuer root, not
    # under /api/v1/. That is not a style choice: OpenID Connect Discovery
    # 1.0 section 4 requires the metadata to be at
    # <issuer>/.well-known/openid-configuration, and every conforming client
    # library constructs that path itself from the issuer. Putting it behind
    # a version prefix would mean no standard library could find it.
    #
    # They are unversioned for the same reason the health endpoints are: they
    # are a contract with the wider world rather than with this API, and the
    # document's own contents name the versioned endpoints it points at.
    path(
        ".well-known/openid-configuration",
        ecosystem_views.openid_configuration,
        name="oidc-discovery",
    ),
    path(".well-known/jwks.json", ecosystem_views.jwks_document, name="oidc-jwks"),
    path("api/v1/", include("workeasy360.interfaces.api.v1.urls")),
]
