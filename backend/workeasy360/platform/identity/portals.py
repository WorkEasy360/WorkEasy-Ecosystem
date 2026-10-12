"""The configured portal origins, read at call time (ADR-043).

Every link the platform builds for a browser - an emailed account link, an
OIDC login redirect, a billing or launcher return - names the portal it
belongs to, and the origin always comes from configuration
(``W360_PORTAL_ORIGINS``), never from a request. In single-host mode (no
portal origins configured) every portal is ``W360_FRONTEND_URL``.
"""

from __future__ import annotations

from django.conf import settings

from workeasy360.contracts.values import portals


def enabled() -> bool:
    return bool(getattr(settings, "PORTALS_ENABLED", False))


def origin(portal: str) -> str:
    """The origin of ``portal`` (no trailing slash)."""
    configured = getattr(settings, "W360_PORTAL_ORIGINS", {}).get(portal, "")
    return (configured or settings.W360_FRONTEND_URL).rstrip("/")


def auth_url() -> str:
    """Where the sign-in pages live: verification, reset and invitation
    links, and the OIDC login and organization-choice redirects."""
    return origin(portals.AUTH)


def app_url() -> str:
    """Where the customer portal lives: billing and launcher returns."""
    return origin(portals.APP)


def platform_hosts() -> set[str]:
    """Every host the platform itself serves (lower-case, port included when
    not the default)."""
    hosts = {
        portals.host_of(o) for o in getattr(settings, "W360_PORTAL_ORIGINS", {}).values()
    }
    hosts.add(portals.host_of(settings.W360_FRONTEND_URL))
    hosts.discard("")
    return hosts
