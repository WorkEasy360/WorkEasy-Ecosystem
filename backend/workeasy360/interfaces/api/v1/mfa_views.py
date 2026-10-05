"""MFA enrollment, challenge and step-up (Phase 5, Task 37-39).

Session-authenticated, not tenant-scoped, exactly like ``auth_views`` -
``AuthFactor`` belongs to the global ``User``, not to any organization
membership. ``step_up_view`` is the one endpoint that raises the session to
AAL2 (``platform.tenancy.resolution.record_step_up``); ``login_view`` resets
it to 1, and ``resolve_tenant_from_path`` reads it - time-boxed to
``STEP_UP_MAX_AGE_SECONDS`` by ``session_aal`` - into every tenant-scoped
request's ``TenantContext``.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from workeasy360.contracts.errors import MfaThrottled
from workeasy360.platform.observability import throttle
from workeasy360.platform.observability.client_ip import client_ip, rate_limit_identity
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics
from workeasy360.platform.tenancy.resolution import record_step_up, session_aal

logger = get_logger(__name__)

_KEY_PREFIX = "w360:mfa:rl:"


def _require_session_user(request: HttpRequest) -> Any | None:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    return user


def _step_up_required(detail: str) -> JsonResponse:
    return JsonResponse({"code": "step_up_required", "detail": detail}, status=401)


def _enrollment_needs_step_up(request: HttpRequest, user: Any) -> bool:
    """Adding a factor to an account that already has one is a factor
    change, and needs the same proof as removing one. Otherwise a password
    alone (a phished or reused one) is enough to enroll the attacker's own
    authenticator, step up with it, and replace the victim's recovery
    codes. The first-ever enrollment cannot require this - there is no
    factor yet to prove possession of."""
    return user.has_mfa and session_aal(request.session) < 2


def _first_enrollment_needs_fresh_sign_in(request: HttpRequest, user: Any) -> bool:
    """The first authenticator needs a *recent* sign-in (Phase 12, G-15).

    With no factor yet there is nothing to step up with, so this used to need
    only a session - any session, however old. A stolen session cookie could
    then enroll the thief's authenticator, take the recovery codes, and leave
    the owner unable to step up or to reach an organization that requires
    MFA. Requiring that the password was entered within the step-up window
    (15 minutes) limits that to someone who can sign in, not merely someone
    holding a cookie.
    """
    import time

    from django.conf import settings

    from workeasy360.platform.tenancy.resolution import SESSION_AUTHENTICATED_AT_KEY

    if user.has_mfa:
        return False
    try:
        signed_in_at = float(request.session.get(SESSION_AUTHENTICATED_AT_KEY))
    except (TypeError, ValueError):
        return True
    return time.time() - signed_in_at > int(settings.STEP_UP_MAX_AGE_SECONDS)


def _fresh_sign_in_required() -> JsonResponse:
    return JsonResponse(
        {
            "code": "reauthentication_required",
            "detail": "Sign in again to add your first authenticator.",
        },
        status=401,
    )


@require_http_methods(["POST"])
@never_cache
def totp_enroll_view(request: HttpRequest) -> JsonResponse:
    """Begin enrollment. Returns the raw secret and an ``otpauth://`` URI
    exactly once - neither is ever retrievable again."""
    from workeasy360.platform.identity.services import begin_totp_enrollment

    user = _require_session_user(request)
    if user is None:
        return JsonResponse({"code": "login_required"}, status=401)

    if _enrollment_needs_step_up(request, user):
        return _step_up_required("Verify a code before adding an authenticator.")
    if _first_enrollment_needs_fresh_sign_in(request, user):
        return _fresh_sign_in_required()

    result = begin_totp_enrollment(user.id)
    logger.info("identity.mfa.enrollment_started", user_id=str(user.id))
    return JsonResponse(
        {
            "factor_id": str(result.factor_id),
            "secret": result.secret_base32,
            "provisioning_uri": result.provisioning_uri,
        }
    )


@require_http_methods(["POST"])
@never_cache
def totp_confirm_view(request: HttpRequest) -> JsonResponse:
    """Confirm enrollment with the first code. Domain errors
    (``MfaEnrollmentNotFound``, ``MfaCodeInvalid``) are handled by
    ``DomainErrorMiddleware`` - see auth_views for why nothing here catches
    them."""
    from workeasy360.platform.identity.services import confirm_totp_enrollment

    user = _require_session_user(request)
    if user is None:
        return JsonResponse({"code": "login_required"}, status=401)

    if _enrollment_needs_step_up(request, user):
        return _step_up_required("Verify a code before adding an authenticator.")
    if _first_enrollment_needs_fresh_sign_in(request, user):
        return _fresh_sign_in_required()

    limited = _throttle_code_attempt(request, user.id)
    if limited is not None:
        return limited

    body = _body(request)
    factor_id = _uuid_or_none(body.get("factor_id"))
    code = str(body.get("code") or "")
    if factor_id is None:
        from workeasy360.contracts.errors import MfaEnrollmentNotFound

        raise MfaEnrollmentNotFound("No pending TOTP enrollment for this account.")

    recovery_codes = confirm_totp_enrollment(user.id, factor_id, code)
    metrics.increment("mfa_totp_enrolled_total")
    # The account-security trail (Audit 50): who changed their factors, and
    # when. Identifiers only - never the secret, a code or a recovery code.
    logger.info(
        "identity.mfa.factor_enrolled", user_id=str(user.id), factor_id=str(factor_id)
    )
    return JsonResponse({"recovery_codes": recovery_codes})


@require_http_methods(["GET"])
@never_cache
def factors_view(request: HttpRequest) -> JsonResponse:
    from workeasy360.platform.identity.services import list_mfa_factors

    user = _require_session_user(request)
    if user is None:
        return JsonResponse({"code": "login_required"}, status=401)

    return JsonResponse({"factors": list_mfa_factors(user.id)})


@require_http_methods(["DELETE"])
@never_cache
def factor_detail_view(request: HttpRequest, factor_id: str) -> JsonResponse:
    """Remove an enrolled factor. Requires the *current session* to already
    hold AAL2 (Task 38: a dangerous reset needs a trusted process, and
    "you already proved you hold a working factor this session" is that
    process - there is no other recovery path in this phase, stated
    explicitly rather than silently absent)."""
    from workeasy360.platform.identity.services import remove_mfa_factor

    user = _require_session_user(request)
    if user is None:
        return JsonResponse({"code": "login_required"}, status=401)

    if session_aal(request.session) < 2:
        return _step_up_required(
            "Verify a code before removing an authentication factor."
        )

    # Idempotent: an id that names nothing - malformed included - removes
    # nothing, rather than reaching the query as a non-UUID (a 500).
    parsed = _uuid_or_none(factor_id)
    if parsed is not None:
        remove_mfa_factor(user.id, parsed)
        metrics.increment("mfa_factor_removed_total")
        logger.info(
            "identity.mfa.factor_removed", user_id=str(user.id), factor_id=str(parsed)
        )
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@never_cache
def step_up_view(request: HttpRequest) -> JsonResponse:
    """Raise the session to AAL2 by verifying a TOTP or recovery code.

    Throttled per-account (Task 39/58): a six-digit TOTP code has enough
    entropy that an *unthrottled* endpoint is a meaningful online guessing
    target, unlike a password. Failures are counted as a named metric
    (``mfa_step_up_failures_total``) rather than folded into the generic
    login-failure one, so a step-up-specific attack is visible as its own
    signal.
    """
    from workeasy360.platform.identity.services import verify_step_up_code

    user = _require_session_user(request)
    if user is None:
        return JsonResponse({"code": "login_required"}, status=401)

    limited = _throttle_code_attempt(request, user.id)
    if limited is not None:
        return limited

    body = _body(request)
    code = str(body.get("code") or "")

    try:
        verified = verify_step_up_code(user.id, code)
    except MfaThrottled as exc:
        # Consecutive failures, counted durably on the account - unlike the
        # per-minute buckets above, this survives new windows, new sessions,
        # other instances and a cache outage.
        metrics.increment("mfa_step_up_throttled_total", bucket="consecutive_failures")
        logger.warning(
            "identity.mfa.step_up_locked",
            user_id=str(user.id),
            retry_after=exc.retry_after,
        )
        response = JsonResponse({"code": exc.code, "detail": exc.message}, status=429)
        response["Retry-After"] = str(exc.retry_after)
        return response

    if not verified:
        metrics.increment("mfa_step_up_failures_total")
        logger.info("identity.mfa.step_up_failed", user_id=str(user.id))
        return JsonResponse(
            {"code": "mfa_code_invalid", "detail": "That code did not verify."},
            status=400,
        )

    # A new session key at the new assurance level (Phase 12, G-20), as sign-in
    # already does: a key observed at AAL 1 must not become an AAL 2 key.
    request.session.cycle_key()
    record_step_up(request.session)
    metrics.increment("mfa_step_up_success_total")
    logger.info("identity.mfa.step_up_succeeded", user_id=str(user.id))
    return JsonResponse({"aal": 2})


def _throttle_code_attempt(request: HttpRequest, user_id: Any) -> JsonResponse | None:
    """One budget for every MFA code an account submits - step-up (TOTP or
    recovery code) and enrollment confirmation alike - so alternating
    endpoints does not double a guesser's attempts."""
    from django.conf import settings

    limits = getattr(settings, "MFA_STEP_UP_RATE_LIMITS", {})
    address = rate_limit_identity(client_ip(request))

    for bucket, identity in (("step_up_account", str(user_id)), ("step_up_ip", address)):
        limit, window = limits.get(bucket, (0, 60))
        limit, window = int(limit), max(1, int(window))
        if limit <= 0:
            continue
        key = f"{_KEY_PREFIX}{bucket}:{identity}:{throttle.window_slot(window)}"
        if throttle.hit(key, window) <= limit:
            continue

        retry_after = throttle.retry_after(window)
        metrics.increment("mfa_step_up_throttled_total", bucket=bucket)
        logger.warning("mfa.step_up.throttled", bucket=bucket, limit=limit)
        response = JsonResponse(
            {"code": "rate_limited", "detail": "Too many attempts. Try again shortly."},
            status=429,
        )
        response["Retry-After"] = str(retry_after)
        return response
    return None


def _uuid_or_none(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


def _body(request: HttpRequest) -> dict[str, Any]:
    import json

    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
