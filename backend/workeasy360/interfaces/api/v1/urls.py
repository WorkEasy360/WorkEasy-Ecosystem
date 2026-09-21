"""API v1 URL assembly.

Each module owns its own API surface; this file only includes them. What is
genuinely cross-cutting - version routing, throttles, schema generation -
lives here and nowhere else.

Phase 1 exposes no product endpoints. The tenant-scoped path shape is fixed
now because the tenancy middleware resolves the organization from it:

    /api/v1/orgs/{org_slug}/...

Organization comes from the path and never from a header or body (TB-1/TB-2).
"""

from __future__ import annotations

from django.urls import path

from workeasy360.interfaces.api.v1 import views

urlpatterns = [
    # A minimal tenant-scoped surface, present so the tenancy middleware,
    # the suspension block and the isolation tests have a real HTTP path to
    # exercise rather than a synthetic one.
    path(
        "orgs/<slug:org_slug>/context",
        views.tenant_context_view,
        name="tenant-context",
    ),
    path(
        "orgs/<slug:org_slug>/memberships",
        views.memberships_view,
        name="memberships",
    ),
    path(
        "orgs/<slug:org_slug>/entitlements",
        views.entitlements_view,
        name="entitlements",
    ),
]
