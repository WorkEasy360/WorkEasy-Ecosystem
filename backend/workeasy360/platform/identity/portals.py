"""The configured portal origins, read at call time (ADR-043).

Every link the platform builds for a browser - an emailed account link, an
OIDC login redirect, a billing or launcher return - names the portal it
belongs to, and the origin always comes from configuration
(``W360_PORTAL_ORIGINS``), never from a request. In single-host mode (no
portal origins configured) every portal is ``W360_FRONTEND_URL``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from workeasy360.contracts.values import portals


def enabled() -> bool:
    return bool(getattr(settings, "PORTALS_ENABLED", False))


def origin(portal: str) -> str:
    """The origin of ``portal`` (no trailing slash)."""
    configured = getattr(settings, "W360_PORTAL_ORIGINS", {}).get(portal, "")
    return (configured or settings.W360_FRONTEND_URL).rstrip("/")


def auth_url() -> str:
    """The central sign-in host: only the product sign-in (OIDC) pages live
    there now - the login and organization-choice redirects, the access
    chooser and the product sign-out. Account links use :func:`account_url`
    (ADR-059)."""
    return origin(portals.AUTH)


def account_url(portal: str | None = None) -> str:
    """Where a portal's own account pages live (ADR-059): sign-in, sign-up,
    email verification, password reset and invitations. Each of the Admin,
    Partner and customer portals serves them on its own host, so a link
    names the portal it belongs to; anything else is the customer portal."""
    return origin(portal if portal in portals.DESTINATIONS else portals.APP)


def request_portal(request: Any) -> str:
    """The destination portal ``request`` arrived on - the portal whose own
    pages a link sent in answer to it should open - or the customer portal
    (single-host mode, the central sign-in host, any other host). Chooses a
    link only; it grants nothing."""
    if not enabled():
        return portals.APP
    try:
        host = request.get_host()
    except Exception:  # noqa: BLE001 - a disallowed host never reaches a view
        return portals.APP
    found = portals.portal_for_host(host, getattr(settings, "W360_PORTAL_ORIGINS", {}))
    return found if found in portals.DESTINATIONS else portals.APP


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
