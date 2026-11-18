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
    """WorkEasy360 Accounts, the central sign-in host (ADR-067: its own name
    is ``accounts.workeasy360.com``): only the product sign-in (OIDC) pages
    live there - the login and organization-choice redirects, the access
    chooser, Switch Account and the product sign-out. Account links use
    :func:`account_url` (ADR-059)."""
    return origin(portals.AUTH)


def legacy_auth_origin() -> str:
    """The central sign-in's previous origin (ADR-067:
    ``https://auth.workeasy360.com``), kept reachable while products move to
    WorkEasy360 Accounts; ``""`` when there is none. Only in multi-portal
    mode."""
    if not enabled():
        return ""
    return str(getattr(settings, "W360_AUTH_LEGACY_ORIGIN", "") or "").rstrip("/")


def is_legacy_auth_host(host: str) -> bool:
    """Whether ``host`` (a request's ``Host``, port included when not the
    default) is the central sign-in's previous name."""
    legacy = legacy_auth_origin()
    wanted = (host or "").strip().lower().rstrip(".")
    return bool(legacy and wanted and portals.host_of(legacy) == wanted)


def account_url(portal: str | None = None) -> str:
    """Where a portal's own account pages live (ADR-059): sign-in, sign-up,
    email verification, password reset and invitations. Each of the Admin,
    Partner and customer portals serves them on its own host, so a link
    names the portal it belongs to; anything else is the customer portal.
    ADR-065: the Store's host serves them too, for its own customers - when it
    is configured; otherwise its pages are the customer portal's."""
    if portal == portals.STORE and not store_enabled():
        return origin(portals.APP)
    return origin(portal if portal in portals.ACCOUNT_PORTALS else portals.APP)


def request_portal(request: Any) -> str:
    """The destination portal ``request`` arrived on - the portal whose own
    pages a link sent in answer to it should open - or the customer portal
    (single-host mode, the central sign-in host, any other host). Chooses a
    link only; it grants nothing."""
    if not enabled():
        return portals.APP
    try:
        host = request.get_host()
    except Exception:
        return portals.APP
    found = portals.portal_for_host(host, getattr(settings, "W360_PORTAL_ORIGINS", {}))
    return found if found in portals.ACCOUNT_PORTALS else portals.APP


def app_url() -> str:
    """Where the customer portal lives: billing and launcher returns."""
    return origin(portals.APP)


def store_enabled() -> bool:
    """Whether the Store has its own host (ADR-065): ``W360_STORE_ORIGIN`` is
    set in multi-portal mode."""
    return enabled() and bool(
        getattr(settings, "W360_PORTAL_ORIGINS", {}).get(portals.STORE, "")
    )


def store_url() -> str:
    """Where the Store's pages (``/store/...``) are served: its own host when
    configured, otherwise the customer portal (single-host mode included)."""
    return origin(portals.STORE) if store_enabled() else app_url()


def platform_hosts() -> set[str]:
    """Every host the platform itself serves (lower-case, port included when
    not the default)."""
    hosts = {
        portals.host_of(o) for o in getattr(settings, "W360_PORTAL_ORIGINS", {}).values()
    }
    hosts.add(portals.host_of(settings.W360_FRONTEND_URL))
    # ADR-067: the sign-in's previous name is still the platform's own host.
    hosts.add(portals.host_of(legacy_auth_origin()))
    hosts.discard("")
    return hosts
