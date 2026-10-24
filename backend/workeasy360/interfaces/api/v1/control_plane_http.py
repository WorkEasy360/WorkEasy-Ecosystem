"""HTTP plumbing shared by the Super Admin, partner and public adapters (Phase 7).

Adapters only: authenticate the session, build the acting context, apply a
rate limit, parse a bounded body. **No authorization decision lives here.** The
decorators establish *who is acting*; whether that actor may do the thing is
decided, again, inside the application service by the one decision function.
Hiding a route in the frontend is a convenience; every request is re-checked
server-side for staff status, a verified second factor (Phase 12, D-037),
the explicit permission and AAL 2 where the permission is dangerous (Task 77).

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
    AccessPolicyDenied,
    AuthenticationRequired,
    PartnerRecordNotFound,
    PermissionDenied,
)
from workeasy360.contracts.values.access_policy import MFA_REQUIRED
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz.control_plane import platform_context
from workeasy360.platform.observability import throttle
from workeasy360.platform.observability.client_ip import client_ip, rate_limit_identity
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics
from workeasy360.platform.tenancy.resolution import session_aal, session_mfa_verified

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

    A staff session must also have verified a second factor (Phase 12, D-037):
    even a *view* permission reads across every tenant, so a password alone
    never opens the control plane. The answer is the organization policy's
    ``mfa_required``, which the admin shell turns into "verify a code" (or
    "set up an authenticator" first). This is the sign-in's verification, not
    AAL 2: it does not lapse, while ``dangerous`` permissions still demand a
    recent step-up in ``authz.check``.
    """
    user = authenticated_user(request)
    if not user.is_staff:
        metrics.increment("staff_endpoint_denials_total", reason="not_staff")
        raise PermissionDenied(
            "Not permitted.", permission="platform.staff", reason="not_platform_staff"
        )
    session = getattr(request, "session", None)
    if not session_mfa_verified(session):
        metrics.increment("staff_endpoint_denials_total", reason="mfa_required")
        raise AccessPolicyDenied(
            MFA_REQUIRED,
            "Workeasy360 staff access needs two-step verification in this session.",
        )
    return platform_context(user_id=user.id, aal=session_aal(session))


def require_staff_second_factor(request: HttpRequest) -> None:
    """D-037 for staff routes that are not ``staff_view`` (the org-scoped
    integration registry): a *staff* session must have verified a factor.
    Anyone else is left to the route's own refusal - these routes are
    control-plane only, so a customer is refused by the service anyway.
    """
    user = getattr(request, "user", None)
    if (
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_staff
        and not session_mfa_verified(getattr(request, "session", None))
    ):
        metrics.increment("staff_endpoint_denials_total", reason="mfa_required")
        raise AccessPolicyDenied(
            MFA_REQUIRED,
            "Workeasy360 staff access needs two-step verification in this session.",
        )


def hidden_unless_verified_staff(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """A staff-only diagnostic (``/healthz/deep``) answers 404 - as it does for
    everyone else - unless the session is staff *and* verified (D-037)."""

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = getattr(request, "user", None)
        if not (
            user is not None
            and getattr(user, "is_authenticated", False)
            and user.is_staff
            and session_mfa_verified(getattr(request, "session", None))
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
    """

    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @require_http_methods(methods)
        @functools.wraps(view)
        def wrapper(
            request: HttpRequest, *args: Any, partner_id: UUID, **kwargs: Any
        ) -> HttpResponse:
            user = authenticated_user(request)
            try:
                ctx = access.partner_actor(
                    user_id=user.id,
                    partner_id=partner_id,
                    aal=session_aal(getattr(request, "session", None)),
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

    Only for a verified staff session that still holds the staff partner view
    permission and whose sign-in has a live guest grant for exactly this
    partner (``commercial.partners.guest``). A guest reads; every unsafe
    method is refused here, before any view runs.
    """
    from workeasy360.commercial.partners import guest
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    user = getattr(request, "user", None)
    session = getattr(request, "session", None)
    if user is None or not getattr(user, "is_staff", False) or session is None:
        return None
    view = guest.current(user.id, session.get(SESSION_SIGN_IN_ID_KEY))
    if view is None or view.partner_id != partner_id:
        return None
    authz.require(staff_context(request), guest.STAFF_VIEW)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        raise PermissionDenied(
            "Guest view is read-only.",
            permission="partner.guest",
            reason="guest_read_only",
        )
    return access.guest_actor(
        user_id=user.id,
        partner_id=partner_id,
        aal=session_aal(session),
        reason=view.reason,
    )


def live_guest_view(request: HttpRequest) -> Any | None:
    """This session's live guest view (``commercial.partners.guest``), or
    ``None``. Never for a session that is not staff: only staff can start one,
    and the staff check stays here, with the other readers of the flag."""
    from workeasy360.commercial.partners import guest
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    user = getattr(request, "user", None)
    session = getattr(request, "session", None)
    if user is None or not getattr(user, "is_staff", False) or session is None:
        return None
    return guest.current(user.id, session.get(SESSION_SIGN_IN_ID_KEY))


def end_guest_view(request: HttpRequest) -> bool:
    """ "Exit guest session": end this sign-in's guest view; whether there was one.

    On its own portal host (multi-portal mode) the staff member's partner-portal
    session existed only for the guest view, so it ends too - nothing of the
    guest view stays signed in on the partner origin. The Super Admin and
    central sign-in sessions are other rows on other hosts and are untouched.
    A staff member who is also a partner member keeps the session.
    """
    from workeasy360.commercial.partners import guest
    from workeasy360.commercial.partners import selectors as partner_selectors
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    user = authenticated_user(request)
    if not user.is_staff:
        return False
    session = request.session
    ctx = platform_context(user_id=user.id, aal=session_aal(session))
    ended = guest.end(ctx, session.get(SESSION_SIGN_IN_ID_KEY))
    if getattr(settings, "PORTALS_ENABLED", False) and not (
        partner_selectors.partners_for_user(user.id)
    ):
        session.flush()
    return ended


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
