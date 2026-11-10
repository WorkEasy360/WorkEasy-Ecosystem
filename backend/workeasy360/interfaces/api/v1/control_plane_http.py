"""HTTP plumbing shared by the Super Admin, partner and public adapters (Phase 7).

Adapters only: authenticate the session, build the acting context, apply a
rate limit, parse a bounded body. **No authorization decision lives here.** The
decorators establish *who is acting*; whether that actor may do the thing is
decided, again, inside the application service by the one decision function.
Hiding a route in the frontend is a convenience; every request is re-checked
server-side for staff status and the explicit permission (Task 77).

Cookie-authenticated mutations go through Django's CSRF middleware like every
other unsafe request; nothing here is exempt.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.partners import access
from workeasy360.contracts.errors import (
    AuthenticationRequired,
    PartnerRecordNotFound,
    PermissionDenied,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz.control_plane import platform_context
from workeasy360.platform.observability import throttle
from workeasy360.platform.observability.client_ip import client_ip, rate_limit_identity
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

_KEY_PREFIX = "w360:cp:rl:"


def authenticated_user(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        raise AuthenticationRequired("Sign in to continue.")
    return user


def staff_context(request: HttpRequest) -> TenantContext:
    """A ``STAFF`` context for the signed-in user, or a refusal.

    ``is_staff`` is a precondition, not a permission: it says "this identity is
    Workeasy360 staff" and unlocks nothing by itself. Django's ``is_superuser``
    is never read.

    A password sign-in opens the control plane (owner decision 2026-10-24,
    withdrawing D-037's sign-in code), and no second factor is asked for
    anywhere (owner decision 2026-10-24, ADR-050).
    """
    from workeasy360.interfaces.api.guest_session import guest_session_id

    user = authenticated_user(request)
    if not user.is_staff:
        metrics.increment("staff_endpoint_denials_total", reason="not_staff")
        raise PermissionDenied(
            "Not permitted.", permission="platform.staff", reason="not_platform_staff"
        )
    if guest_session_id(request) is not None:
        # A partner-portal session opened by Login as Guest (ADR-062) is never a
        # staff session, on any host: it holds none of the Admin's powers.
        metrics.increment("staff_endpoint_denials_total", reason="guest_session")
        raise PermissionDenied(
            "Not permitted in Guest Mode.",
            permission="platform.staff",
            reason="guest_session",
        )
    return platform_context(user_id=user.id)


def hidden_unless_staff(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """A staff-only diagnostic (``/healthz/deep``) answers 404 - as it does for
    everyone else - unless the session is staff."""

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        from workeasy360.interfaces.api.guest_session import guest_session_id

        user = getattr(request, "user", None)
        if not (
            user is not None
            and getattr(user, "is_authenticated", False)
            and user.is_staff
            and guest_session_id(request) is None
        ):
            return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)
        return view(request, *args, **kwargs)

    return wrapper


def staff_view(
    methods: list[str],
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Decorate ``view(request, ctx, **kwargs)`` as a staff endpoint."""

    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @require_http_methods(methods)
        @functools.wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            ctx = staff_context(request)
            return view(request, ctx, *args, **kwargs)

        return wrapper

    return decorate


def partner_view(
    methods: list[str],
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Decorate ``view(request, ctx, **kwargs)`` as a partner endpoint.

    The partner comes from the URL and is **verified against the caller's own
    active membership**; a partner the user does not belong to is a 404,
    indistinguishable from one that does not exist.

    A session opened by Login as Guest (ADR-062) reaches the one partner its
    guest view is for, as that guest view, and no other - not even a partner
    the staff member belongs to.
    """
    from workeasy360.interfaces.api.guest_session import guest_session_id

    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @require_http_methods(methods)
        @functools.wraps(view)
        def wrapper(
            request: HttpRequest, *args: Any, partner_id: UUID, **kwargs: Any
        ) -> HttpResponse:
            user = authenticated_user(request)
            if guest_session_id(request) is not None:
                guest_ctx = guest_context(request, partner_id)
                if guest_ctx is None:
                    raise PartnerRecordNotFound(
                        "No such partner.", partner_id=str(partner_id)
                    )
                return view(request, guest_ctx, *args, **kwargs)
            try:
                ctx = access.partner_actor(
                    user_id=user.id,
                    partner_id=partner_id,
                )
            except PartnerRecordNotFound:
                ctx = guest_context(request, partner_id)
                if ctx is None:
                    raise
            return view(request, ctx, *args, **kwargs)

        return wrapper

    return decorate


def guest_context(request: HttpRequest, partner_id: UUID) -> TenantContext | None:
    """A staff member's read-only guest view of this partner, or ``None``.

    Only for a staff identity that still holds ``platform.staff_partner.guest``
    (read again on every request, so taking it away ends the view) and whose
    live guest view (``commercial.partners.guest``) is for exactly this
    partner. A guest reads; every unsafe method is refused here, before any
    view runs.
    """
    from workeasy360.commercial.partners import guest
    from workeasy360.platform.authz import services as authz

    view = live_guest_view(request)
    if view is None or view.partner_id != partner_id:
        return None
    authz.require(platform_context(user_id=request.user.id), guest.STAFF_GUEST)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        raise PermissionDenied(
            "Guest view is read-only.",
            permission="partner.guest",
            reason="guest_read_only",
        )
    return access.guest_actor(
        user_id=request.user.id,
        partner_id=partner_id,
        reason=view.reason,
    )


def live_guest_view(request: HttpRequest) -> Any | None:
    """This request's live guest view (``commercial.partners.guest``), or
    ``None``. Never for a session that is not staff.

    Multi-portal mode: only a partner-portal session opened for a guest view
    (``GuestSessionMiddleware`` has read its row on this request). Single-host
    mode (development): the guest view of this sign-in, on the one session
    every page shares.
    """
    from workeasy360.commercial.partners import guest
    from workeasy360.interfaces.api.guest_session import guest_session_id
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    user = getattr(request, "user", None)
    session = getattr(request, "session", None)
    if (
        user is None
        or not getattr(user, "is_authenticated", False)
        or not getattr(user, "is_staff", False)
        or session is None
    ):
        return None
    if guest_session_id(request) is not None:
        return getattr(request, "w360_guest_view", None)
    if getattr(settings, "PORTALS_ENABLED", False):
        return None
    return guest.current(user.id, session.get(SESSION_SIGN_IN_ID_KEY))


def end_guest_view(request: HttpRequest) -> dict[str, Any]:
    """ "Exit Guest Mode": end this session's guest view and say where the
    Admin is (``return_url``).

    On the partner portal's own host (multi-portal mode) the session existed
    only for the guest view, so it ends too - nothing of the guest view stays
    signed in there. The Admin's own session lives on the Admin host and is
    untouched, so returning there is returning to it. ``return_url`` is built
    from configuration and a validated path, never from the request.
    """
    from workeasy360.commercial.partners import guest
    from workeasy360.interfaces.api.guest_session import guest_session_id
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    user = authenticated_user(request)
    portals_on = getattr(settings, "PORTALS_ENABLED", False)
    admin_origin = settings.W360_PORTAL_ORIGINS["admin"] if portals_on else ""
    if not user.is_staff:
        return {"ended": False, "return_url": f"{admin_origin}{guest.DEFAULT_RETURN}"}
    session = request.session
    ctx = platform_context(user_id=user.id)
    marker = guest_session_id(request)
    if marker is not None:
        view = guest.end_session(ctx, marker)
        session.flush()
        ended = view is not None
    else:
        view = live_guest_view(request)
        ended = guest.end(ctx, session.get(SESSION_SIGN_IN_ID_KEY))
    back = view.return_path if view is not None else guest.DEFAULT_RETURN
    return {"ended": ended, "return_url": f"{admin_origin}{back}"}


def limited(
    request: HttpRequest,
    bucket: str,
    *,
    user: Any = None,
    account_email: str | None = None,
) -> JsonResponse | None:
    """Count this request against ``bucket`` (per user, if given, per email
    account, if given, and per address); a 429 response if any is exhausted,
    else ``None``.

    The user key is the id and the account key a SHA-256 of the normalised
    address (Phase 9: signup and reset are aimed at an inbox, not a user) -
    never an email - and the address key the trusted client address, so none
    reaches a metric label: the metric carries the bucket name only.
    """
    import hashlib

    from workeasy360.platform.identity.email import normalize_email

    limits = getattr(settings, "CONTROL_PLANE_RATE_LIMITS", {})
    identities = [("ip", rate_limit_identity(client_ip(request)))]
    if user is not None:
        identities.append(("user", str(user.id)))
    if account_email:
        digest = hashlib.sha256(normalize_email(account_email).encode("utf-8"))
        identities.append(("account", digest.hexdigest()))
    for kind, identity in identities:
        limit, window = limits.get(f"{bucket}_{kind}", (0, 60))
        limit, window = int(limit), max(1, int(window))
        if limit <= 0:
            continue
        key = f"{_KEY_PREFIX}{bucket}_{kind}:{identity}:{throttle.window_slot(window)}"
        if throttle.hit(key, window) <= limit:
            continue
        metrics.increment("control_plane_throttled_total", bucket=f"{bucket}_{kind}")
        logger.warning("control_plane.throttled", bucket=f"{bucket}_{kind}", limit=limit)
        response = JsonResponse(
            {"code": "rate_limited", "detail": "Too many requests. Try again shortly."},
            status=429,
        )
        response["Retry-After"] = str(throttle.retry_after(window))
        return response
    return None


def page_response(page: Any) -> JsonResponse:
    """Any keyset page (``items``, ``next_cursor``, ``has_more``) as the wire shape."""
    return JsonResponse(
        {
            "results": page.items,
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }
    )


def query_page_args(request: HttpRequest) -> dict[str, Any]:
    """Untrusted paging arguments: a bad limit is the default, never a 500."""
    from workeasy360.contracts.values.cursor import clamp_limit

    return {
        "limit": clamp_limit(request.GET.get("limit"), default=25, maximum=100),
        "cursor": request.GET.get("cursor") or None,
    }
