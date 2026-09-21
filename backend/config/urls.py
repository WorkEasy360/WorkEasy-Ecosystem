"""Root URL configuration.

Includes `interfaces/` only. No app is routed directly from here: URL assembly
and API versioning are `interfaces/`' job, and routing an app from the root
would put composition in two places (01-domain-boundaries.md section 9).
"""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("", include("workeasy360.interfaces.urls")),
]
