"""The platform's portals: which host serves which experience, and where a
sign-in may return to (docs/portals/design.md, ADR-043).

Pure values and rules, no Django: the settings that name each portal's origin
are read by ``platform.identity.portals``; this module only interprets them.

**A portal is a presentation, never an authority.** Nothing here answers "may
this person do X" - that is StaffGrant, PartnerMember and Membership, checked
on every request. What lives here decides which UI a host shows, which API
prefixes a host refuses (a narrowing: only ever a denial), and which paths a
hand-off may land on (the return-URL allowlist).
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

#: The four browser portals, plus the API host (no UI).
ADMIN: Final = "admin"
PARTNER: Final = "partner"
APP: Final = "app"
AUTH: Final = "auth"
API: Final = "api"

WEB_PORTALS: Final = (ADMIN, PARTNER, APP, AUTH)
ALL_PORTALS: Final = (*WEB_PORTALS, API)

#: Portals a sign-in hands off to. ``auth`` is where the sign-in happens.
DESTINATIONS: Final = (ADMIN, PARTNER, APP)

#: The sign-in page's navigation intent (``?mode=``) -> the portal it aims at.
#: Intent only: the hand-off still checks the account may enter that portal,
#: and the portal re-checks on every request.
MODE_TO_PORTAL: Final = {"admin": ADMIN, "partner": PARTNER, "user": APP}

#: Where each destination opens when no (valid) page was asked for.
PORTAL_ROOT: Final = {ADMIN: "/admin", PARTNER: "/partner", APP: "/dashboard"}

#: The return-URL allowlist: the page paths a hand-off may land on, per
#: portal. A path matches a prefix when it equals it or continues with ``/``.
PORTAL_PATH_PREFIXES: Final = {
    ADMIN: ("/admin",),
    PARTNER: ("/partner",),
    APP: ("/dashboard", "/o", "/organizations", "/onboarding", "/billing", "/settings"),
}

#: The longest ``next`` accepted. Anything longer is ignored, not truncated.
MAX_NEXT_LENGTH: Final = 512


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def safe_next(portal: str, raw: object) -> str:
    """The page a hand-off to ``portal`` lands on: ``raw`` when it is a plain
    same-site path this portal serves, otherwise the portal's root.

    Never a URL: a scheme, a host (``//evil``), a backslash, a control
    character, a ``..`` segment, ``%2e``/``%2f``/``%5c`` tricks or an over-long
    value all fall back to the root - so ``next`` can never become an open
    redirect. The origin is always the configured one for ``portal``.
    """
    root = PORTAL_ROOT[portal]
    if not isinstance(raw, str) or not raw or len(raw) > MAX_NEXT_LENGTH:
        return root
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return root
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return root
    lowered = raw.lower()
    if "%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        return root
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return root
    segments = parts.path.split("/")
    if any(segment in (".", "..") for segment in segments):
        return root
    if not any(_matches(parts.path, prefix) for prefix in PORTAL_PATH_PREFIXES[portal]):
        return root
    query = f"?{parts.query}" if parts.query else ""
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    return f"{parts.path}{query}{fragment}"


def parse_origin(raw: str) -> tuple[str, str] | None:
    """``(scheme, host[:port])`` of a bare origin, lower-cased, or ``None``
    when ``raw`` is not exactly ``scheme://host[:port]`` (no path, query,
    fragment, credentials or wildcard)."""
    value = (raw or "").strip()
    if not value or "*" in value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname.lower()
    # A default port is the same origin without it - and the Host a browser
    # sends then carries no port, so keeping it would match nothing.
    if (parts.scheme, port) in (("https", 443), ("http", 80)):
        port = None
    netloc = f"{host}:{port}" if port else host
    return parts.scheme, netloc


def host_of(origin: str) -> str:
    """The ``host[:port]`` of an origin, lower-cased; ``""`` if malformed."""
    parsed = parse_origin(origin)
    return parsed[1] if parsed else ""


def portal_for_host(host: str, origins: dict[str, str]) -> str | None:
    """Which portal ``host`` (a request's ``Host``, port included when not
    the default) serves, or ``None`` for any other host."""
    wanted = (host or "").strip().lower().rstrip(".")
    if not wanted:
        return None
    for portal in ALL_PORTALS:
        if host_of(origins.get(portal, "")) == wanted:
            return portal
    return None
