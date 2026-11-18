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
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse

from workeasy360.contracts.values import portals
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


def _count_legacy(request: HttpRequest, answer: HttpResponse | None) -> None:
    """ADR-067: what still reaches the previous sign-in host - the number an
    operator watches fall to zero before retiring the name. Never the path or
    the query: a resume token or a code may be in it."""
    if answer is None:
        kind = "served"
    elif answer.status_code == 302:
        kind = "forwarded"
    else:
        kind = "refused"
    metrics.increment("accounts_legacy_host_requests_total", kind=kind)
    # The durable record of the same thing: what w360_issuer_readiness reads from the API logs
    # before the name is retired (the in-process counter resets with every restart).
    logger.info("accounts.legacy_host.request", kind=kind)


#: Staff control-plane administration that predates ``/api/v1/admin/`` and
#: lives under the tenant path shape (catalog/pricing administration and the
#: product-integration registry, all staff-only): the Super Admin host's, not
#: the customer portal's (final audit, P2-2).
STAFF_UNDER_ORGS: Final = re.compile(
    r"^/api/v1/orgs/[^/]+/(commercial|integrations)(/|$)"
)

#: ADR-065: the organization endpoints the Digital Store's own host serves - its
#: checkout (access, quote, order, billing details, paying an invoice and
#: following the payment), Recharge (wallet balances, top-ups, credit packs),
#: trials and the bell. Everything else of an organization (members, security,
#: integrations, settings...) stays the customer portal's alone, so an XSS on the
#: Store could not reach it.
STORE_UNDER_ORGS: Final = re.compile(
    r"^/api/v1/orgs/[^/]+/("
    r"access"
    r"|orders(/quote|/[0-9a-f-]{36}(/cancel)?)?"
    r"|billing-account"
    r"|billing/invoices/[0-9a-f-]{36}(/pay)?"
    r"|billing/payments/[0-9a-f-]{36}"
    r"|wallets(/top-up|/credit-packs)?"
    r"|trials(/[0-9a-f-]{36}/cancel)?"
    r"|notifications"
    r")/?$"
)

#: ``(path, exact, hosts)``: a request whose path equals ``path`` (``exact``)
#: or starts with it is served only on those portals' hosts. The first
#: matching rule wins, so the more specific rule comes first.
SURFACE_RULES: Final[tuple[tuple[str, bool, frozenset[str]], ...]] = (
    ("/api/v1/admin/", False, frozenset({portals.ADMIN})),
    ("/api/v1/partner/", False, frozenset({portals.PARTNER})),
    ("/api/v1/orgs/", False, frozenset({portals.APP})),
    # ADR-059: every web portal has its own sign-in page, so the one sign-in
    # endpoint answers on each of them (the session cookie is that host's).
    ("/api/v1/auth/login", True, frozenset(portals.WEB_PORTALS)),
    # ADR-072: the Accounts Control Center is WorkEasy360 Accounts' own - the
    # person's products, connected logins, sessions and history on that host
    # only (and only while it hosts identity: the views answer 404 otherwise).
    ("/api/v1/auth/account/", False, frozenset({portals.AUTH})),
    # ADR-066: Switch Account starts on a portal and is finished on the
    # central sign-in host, whose session it may end.
    ("/api/v1/auth/switch-account/finish", True, frozenset({portals.AUTH})),
    ("/api/v1/auth/switch-account", True, frozenset(portals.ACCOUNT_PORTALS)),
    ("/api/v1/auth/handoff/redeem", True, frozenset(portals.DESTINATIONS)),
    # ADR-065: the Store's "My Account" - issued on the Store, spent on User Admin.
    ("/api/v1/auth/account-pass/enter", True, frozenset({portals.APP})),
    ("/api/v1/auth/account-pass", True, frozenset({portals.STORE})),
    ("/api/v1/auth/handoff", True, frozenset({portals.AUTH})),
    # The OIDC authorization endpoint needs the central sign-in session.
    ("/api/v1/integrations/oauth/authorize", True, frozenset({portals.AUTH})),
    ("/api/v1/integrations/oauth/resume", True, frozenset({portals.AUTH})),
    # ADR-044: the access chooser and RP-initiated sign-out act on that same
    # session, so they live where it lives.
    ("/api/v1/integrations/oauth/pending", False, frozenset({portals.AUTH})),
    ("/api/v1/integrations/oauth/logout", False, frozenset({portals.AUTH})),
)


#: ADR-066's start of Switch Account, which a portal answers.
SWITCH_ACCOUNT_PATH: Final = "/api/v1/auth/switch-account"


def _accounts_identity() -> bool:
    from workeasy360.platform.identity import portals as portal_links

    return portal_links.accounts_identity()


def allowed_portals(path: str) -> frozenset[str] | None:
    """The portals that serve ``path``, or ``None`` when every host does."""
    if path in (SWITCH_ACCOUNT_PATH, f"{SWITCH_ACCOUNT_PATH}/") and _accounts_identity():
        # ADR-072: while WorkEasy360 Accounts hosts identity, its account page
        # starts Switch Account too (its own sign-in is the one that ends).
        return frozenset({*portals.ACCOUNT_PORTALS, portals.AUTH})
    if STAFF_UNDER_ORGS.match(path):
        return frozenset({portals.ADMIN})
    if STORE_UNDER_ORGS.match(path):
        return frozenset({portals.APP, portals.STORE})
    for prefix, exact, hosts in SURFACE_RULES:
        if path == prefix or (not exact and path.startswith(prefix)):
            return hosts
        if exact and path == prefix + "/":
            return hosts
    return None


_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: Login as Guest's second step (ADR-062): the one write a portal accepts from
#: another portal's origin - the Admin page's form post of a one-time ticket to
#: the partner host. Exactly this path, exactly that pair of origins; the view
#: checks the Origin again and spends the ticket (``auth_views.guest_enter_view``).
GUEST_ENTER_PATH: Final = "/api/v1/partner/guest-view/enter"


def _guest_entry_from_admin(
    path: str, portal: str | None, origin: str, origins: dict[str, str]
) -> bool:
    return (
        path == GUEST_ENTER_PATH
        and portal == portals.PARTNER
        and portals.parse_origin(origin) == portals.parse_origin(origins[portals.ADMIN])
    )


#: ADR-065: the second such write - the Store page's form post of a "My Account"
#: pass to the customer portal. Exactly this path, exactly that pair of origins;
#: the view checks the Origin again and spends the pass (``account_pass_enter_view``).
STORE_ACCOUNT_ENTER_PATH: Final = "/api/v1/auth/account-pass/enter"


def _account_pass_from_store(
    path: str, portal: str | None, origin: str, origins: dict[str, str]
) -> bool:
    store = origins.get(portals.STORE, "")
    return (
        bool(store)
        and path == STORE_ACCOUNT_ENTER_PATH
        and portal == portals.APP
        and portals.parse_origin(origin) == portals.parse_origin(store)
    )


#: ADR-067: on the central sign-in's previous name (``W360_AUTH_LEGACY_ORIGIN``,
#: auth.workeasy360.com), kept while products move to WorkEasy360 Accounts, the
#: browser endpoints a product sends a person to - authorization, the parked
#: request's resume, RP-initiated sign-out - are sent to the same address on the
#: current host, where the sign-in session lives. So is every page.
LEGACY_BROWSER_PATHS: Final = frozenset(
    {
        "/api/v1/integrations/oauth/authorize",
        "/api/v1/integrations/oauth/resume",
        "/api/v1/integrations/oauth/logout",
    }
)
#: The issuer's two public documents, answered on the previous host as before.
OIDC_DOCUMENT_PATHS: Final = frozenset(
    {"/.well-known/openid-configuration", "/.well-known/jwks.json"}
)
#: Everything a product reaches through discovery that is not a browser
#: endpoint lives here - token, userinfo, access verification, events, usage,
#: intelligence - and every one of them authenticates by client credential or
#: bearer token, never by session cookie
#: (``test_credentialed_endpoints_ignore_session_authentication``).
PRODUCT_API_PREFIX: Final = "/api/v1/integrations/"
_FORWARDED_METHODS: Final = frozenset({"GET", "HEAD"})


def legacy_auth_answer(request: HttpRequest, current_origin: str) -> HttpResponse | None:
    """What the central sign-in's previous host answers (ADR-067), or ``None``
    to serve the request unchanged.

    - The two documents and the server-to-server product API under
      ``/api/v1/integrations/``: as before, unchanged (``None``) - except the
      endpoints some portal owns (the access chooser's, sign-out's), 404.
    - A navigation - a browser OIDC endpoint or a page: the same path and
      query on the current host (302, temporary, so a rollback is never stuck
      in a browser's cache). Built from configuration: the request supplies
      only the path, never the destination's origin.
    - Anything else - the rest of the API, a write to a page or to a browser
      endpoint: 404.

    So no session is ever read or set here: a person signs in, chooses and
    signs out on WorkEasy360 Accounts only, whichever name a product used.
    """
    path = request.path
    browser = path in LEGACY_BROWSER_PATHS or (
        path.endswith("/") and path[:-1] in LEGACY_BROWSER_PATHS
    )
    if path in OIDC_DOCUMENT_PATHS:
        return None
    if browser or not path.startswith("/api/"):
        if request.method in _FORWARDED_METHODS:
            return HttpResponseRedirect(f"{current_origin}{request.get_full_path()}")
        return JsonResponse({"code": "not_found"}, status=404)
    if path.startswith(PRODUCT_API_PREFIX) and allowed_portals(path) is None:
        return None
    return JsonResponse({"code": "not_found"}, status=404)


class IssuerMiddleware:
    """ADR-067: answer each request as whichever of the platform's own
    configured issuers its host *is* - while products move from the central
    sign-in's previous name to WorkEasy360 Accounts, a product that discovered
    the platform at one origin keeps getting that origin as ``iss``. A choice
    between our own issuers only (``tokens.issuer_for_host``: any other host
    gets the canonical one), and it grants nothing - like every other Host
    rule in this module. A no-op unless ``W360_OIDC_TRANSITION_ISSUERS`` is
    set. After host validation, so the host is one ``ALLOWED_HOSTS`` admits."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        from workeasy360.platform.ecosystem import tokens

        self.get_response = get_response
        self._tokens = tokens

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not getattr(settings, "OIDC_TRANSITION_ISSUERS", None):
            return self.get_response(request)
        chosen = self._tokens.issuer_for_host(request.get_host())
        with self._tokens.answering_as(chosen):
            return self.get_response(request)


class PortalSurfaceMiddleware:
    """Refuse (404) a portal-owned API prefix on any other host, and (403) a
    write to a web portal that names another origin.

    The second rule is the per-portal half of CSRF. ``CSRF_TRUSTED_ORIGINS``
    is one list for the whole process - all four portals - so Django's own
    Origin check accepts a write from ``app.`` to ``admin.``. No portal ever
    legitimately writes to another: each calls its own ``/api`` same-origin -
    except Login as Guest's entry, the Admin page posting a one-time ticket to
    the partner host (``GUEST_ENTER_PATH``, ADR-062).
    A request with no ``Origin`` header (a server-to-server caller) is left to
    Django's checks, unchanged.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if getattr(settings, "PORTALS_ENABLED", False):
            origins = settings.W360_PORTAL_ORIGINS
            host = request.get_host()
            legacy = getattr(settings, "W360_AUTH_LEGACY_ORIGIN", "")
            if legacy and portals.host_of(legacy) == host.strip().lower().rstrip("."):
                answer = legacy_auth_answer(request, origins[portals.AUTH].rstrip("/"))
                _count_legacy(request, answer)
                return answer if answer is not None else self.get_response(request)
            portal = portals.portal_for_host(host, origins)
            wanted = allowed_portals(request.path)
            if wanted is not None and portal not in wanted:
                return JsonResponse({"code": "not_found"}, status=404)
            origin = request.META.get("HTTP_ORIGIN")
            if (
                origin is not None
                and portal in portals.WEB_PORTALS
                and request.method not in _SAFE_METHODS
                and portals.parse_origin(origin) != portals.parse_origin(origins[portal])
                and not _guest_entry_from_admin(request.path, portal, origin, origins)
                and not _account_pass_from_store(request.path, portal, origin, origins)
            ):
                return JsonResponse(
                    {"code": "csrf_failed", "detail": "Refused: another origin."},
                    status=403,
                )
        return self.get_response(request)
