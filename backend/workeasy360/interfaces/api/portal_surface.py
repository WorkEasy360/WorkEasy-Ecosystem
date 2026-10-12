"""Which API prefixes each portal host serves (ADR-043, design section 2).

**A narrowing, never a grant.** Every rule below can only refuse; a
request it lets through is decided exactly as before, by StaffGrant,
PartnerMember and Membership, read live. What it prevents is a privileged
surface being reachable through another portal's origin: with
``/api/v1/admin/*`` served only on the Super Admin host, an XSS on the
customer portal cannot drive a staff member's customer-portal session
against the admin API, and the admin session lives on one origin only.

Off in single-host mode (``PORTALS_ENABLED`` false: development and the test
suite), where one host serves everything.

The Host header is validated against ``ALLOWED_HOSTS`` by
``request.get_host()`` (a 400 for anything else) before any rule is read.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from workeasy360.contracts.values import portals

#: Staff control-plane administration that predates ``/api/v1/admin/`` and
#: lives under the tenant path shape (catalog/pricing administration and the
#: product-integration registry, all staff-only): the Super Admin host's, not
#: the customer portal's (final audit, P2-2).
STAFF_UNDER_ORGS: Final = re.compile(
    r"^/api/v1/orgs/[^/]+/(commercial|integrations)(/|$)"
)

#: ``(path, exact, hosts)``: a request whose path equals ``path`` (``exact``)
#: or starts with it is served only on those portals' hosts. The first
#: matching rule wins, so the more specific rule comes first.
SURFACE_RULES: Final[tuple[tuple[str, bool, frozenset[str]], ...]] = (
    ("/api/v1/admin/", False, frozenset({portals.ADMIN})),
    ("/api/v1/partner/", False, frozenset({portals.PARTNER})),
    ("/api/v1/orgs/", False, frozenset({portals.APP})),
    # The one sign-in form, on the central sign-in host only.
    ("/api/v1/auth/login", True, frozenset({portals.AUTH})),
    ("/api/v1/auth/handoff/redeem", True, frozenset(portals.DESTINATIONS)),
    ("/api/v1/auth/handoff", True, frozenset({portals.AUTH})),
    # The OIDC authorization endpoint needs the central sign-in session.
    ("/api/v1/integrations/oauth/authorize", True, frozenset({portals.AUTH})),
    ("/api/v1/integrations/oauth/resume", True, frozenset({portals.AUTH})),
    # ADR-044: the access chooser and RP-initiated sign-out act on that same
    # session, so they live where it lives.
    ("/api/v1/integrations/oauth/pending", False, frozenset({portals.AUTH})),
    ("/api/v1/integrations/oauth/logout", False, frozenset({portals.AUTH})),
)


def allowed_portals(path: str) -> frozenset[str] | None:
    """The portals that serve ``path``, or ``None`` when every host does."""
    if STAFF_UNDER_ORGS.match(path):
        return frozenset({portals.ADMIN})
    for prefix, exact, hosts in SURFACE_RULES:
        if path == prefix or (not exact and path.startswith(prefix)):
            return hosts
        if exact and path == prefix + "/":
            return hosts
    return None


_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class PortalSurfaceMiddleware:
    """Refuse (404) a portal-owned API prefix on any other host, and (403) a
    write to a web portal that names another origin.

    The second rule is the per-portal half of CSRF. ``CSRF_TRUSTED_ORIGINS``
    is one list for the whole process - all four portals - so Django's own
    Origin check accepts a write from ``app.`` to ``admin.``. No portal ever
    legitimately writes to another: each calls its own ``/api`` same-origin.
    A request with no ``Origin`` header (a server-to-server caller) is left to
    Django's checks, unchanged.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if getattr(settings, "PORTALS_ENABLED", False):
            origins = settings.W360_PORTAL_ORIGINS
            portal = portals.portal_for_host(request.get_host(), origins)
            wanted = allowed_portals(request.path)
            if wanted is not None and portal not in wanted:
                return JsonResponse({"code": "not_found"}, status=404)
            origin = request.META.get("HTTP_ORIGIN")
            if (
                origin is not None
                and portal in portals.WEB_PORTALS
                and request.method not in _SAFE_METHODS
                and portals.parse_origin(origin) != portals.parse_origin(origins[portal])
            ):
                return JsonResponse(
                    {"code": "csrf_failed", "detail": "Refused: another origin."},
                    status=403,
                )
        return self.get_response(request)
