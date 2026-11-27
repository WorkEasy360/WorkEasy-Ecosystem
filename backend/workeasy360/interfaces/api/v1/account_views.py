"""A person's own account: password, sessions, security trail - and the
invitation acceptance page (Phase 8 extension).

Session-authenticated, not tenant-scoped, like ``auth_views``: these belong
to the global account. The caller only ever acts on
their *own* account - no endpoint here takes a user id.

Changing a password is guarded by the current password, which the session
alone does not prove, and ends every other session
(``identity.account.change_password``).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.contrib.auth import update_session_auth_hash
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from workeasy360.contracts.errors import (
    AuthenticationRequired,
    MembershipValidationError,
    SessionNotFound,
)
from workeasy360.contracts.values import json_input
from workeasy360.interfaces.api.v1.auth_views import connection_facts, establish_session
from workeasy360.interfaces.api.v1.control_plane_http import limited
from workeasy360.platform.identity import account
from workeasy360.platform.identity.middleware import SESSION_EPOCH_KEY
from workeasy360.platform.identity.sessions import (
    SESSION_SIGN_IN_ID_KEY,
    session_public_id,
)
from workeasy360.platform.tenancy.resolution import SESSION_PASSWORD_NONCOMPLIANT_KEY


def _user(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise AuthenticationRequired("Sign in to continue.")
    return user


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json_input.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MembershipValidationError("Malformed JSON body.", field="body") from exc
    if not isinstance(payload, dict):
        raise MembershipValidationError("The body must be a JSON object.", field="body")
    return payload


@require_http_methods(["POST"])
@never_cache
def password_view(request: HttpRequest) -> JsonResponse:
    """Change the password. Needs the current one; ends every other session;
    the caller stays signed in on a rotated session key."""
    from workeasy360.platform.organizations.selectors import (
        password_min_lengths_for_user,
    )

    user = _user(request)
    blocked = limited(request, "password_change", user=user)
    if blocked is not None:
        return blocked
    body = _body(request)
    minimums = password_min_lengths_for_user(user.id)
    where = connection_facts(request)
    changed = account.change_password(
        user.id,
        current_password=str(body.get("current_password") or ""),
        new_password=str(body.get("new_password") or ""),
        min_length=max(minimums.values(), default=None),
        ip_prefix=where["ip_prefix"],
        device_label=where["device_label"],
    )
    # Rotates this session's key and re-binds it to the new password hash;
    # every other session was deleted by the service.
    update_session_auth_hash(request, changed)
    request.session[SESSION_EPOCH_KEY] = changed.session_epoch
    request.session[SESSION_PASSWORD_NONCOMPLIANT_KEY] = []
    from workeasy360.platform.administration import account_security

    account_security.password_changed_notice(changed, device_label=where["device_label"])
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@never_cache
def email_change_view(request: HttpRequest) -> JsonResponse:
    """Ask to sign in with another email (ADR-066). Needs the current
    password - and, with two-step verification on, a current code (2026-11-27;
    ``code`` in the body) - the confirmation link goes to the new address, and
    nothing changes until it is used. Answers 202 whatever the address (an
    address another account holds is told instead - ``self_service``)."""
    from workeasy360.contracts.errors import TwoStepCodeInvalid
    from workeasy360.interfaces.api.v1.control_plane_http import (
        failures_exhausted,
        spend_failure,
    )
    from workeasy360.platform.administration import account_security, self_service
    from workeasy360.platform.identity import portals as portal_links

    user = _user(request)
    body = _body(request)
    new_email = str(body.get("new_email") or "")
    blocked = limited(
        request, "email_change", user=user, account_email=new_email.strip() or None
    )
    if blocked is not None:
        return blocked
    # A wrong code here spends the account changes' day budget for wrong codes,
    # with their hourly notice (ADR-075 review L1 / L-1, as closure does).
    blocked = failures_exhausted("two_step_daily", user=user)
    if blocked is not None:
        return blocked
    where = connection_facts(request)
    try:
        self_service.request_email_change(
            user.id,
            password=str(body.get("password") or ""),
            new_email=new_email,
            portal=portal_links.request_portal(request),
            ip_prefix=where["ip_prefix"],
            device_label=where["device_label"],
            code=body.get("code"),
        )
    except TwoStepCodeInvalid:
        spend_failure("two_step_daily", user=user)
        account_security.two_step_code_refused(
            user,
            ip_prefix=where["ip_prefix"],
            device_label=where["device_label"],
            at_sign_in=False,
        )
        raise
    return JsonResponse({"status": "confirmation_sent"}, status=202)


@require_http_methods(["GET"])
@never_cache
def sessions_view(request: HttpRequest) -> JsonResponse:
    user = _user(request)
    return JsonResponse(
        {
            "sessions": account.list_sessions(
                user.id, current_public_id=session_public_id(request.session)
            ),
        }
    )


@require_http_methods(["POST"])
@never_cache
def session_revoke_view(request: HttpRequest, session_id: str) -> JsonResponse:
    user = _user(request)
    blocked = limited(request, "session_revoke", user=user)
    if blocked is not None:
        return blocked
    try:
        target = uuid.UUID(str(session_id))
    except ValueError as exc:
        raise SessionNotFound("No such session.") from exc
    current = session_public_id(request.session)
    if target == current:
        # Ending your own session is signing out, which has its own
        # endpoint; refusing here keeps this one unambiguous.
        raise SessionNotFound("Use sign out to end this session.")
    where = connection_facts(request)
    # ADR-072: revoking ends the whole sign-in the session belongs to (every
    # host's session of it), so a session of the caller's own sign-in - the
    # same sign-in on another portal - is refused like the caller's own.
    ended = account.revoke_session(
        user.id,
        target,
        keep_sign_in_id=request.session.get(SESSION_SIGN_IN_ID_KEY),
        **where,
    )
    return JsonResponse({"ok": True, "ended": ended})


@require_http_methods(["POST"])
@never_cache
def sessions_revoke_others_view(request: HttpRequest) -> JsonResponse:
    user = _user(request)
    blocked = limited(request, "session_revoke", user=user)
    if blocked is not None:
        return blocked
    where = connection_facts(request)
    ended = account.revoke_other_sessions(
        user.id, keep_public_id=session_public_id(request.session), **where
    )
    return JsonResponse({"ok": True, "ended": ended})


#: How long "sign out everywhere" waits for products to confirm before it
#: answers. Workeasy360's own sessions end before this; the wait is only so
#: the answer can say which products have already confirmed (ADR-069).
SIGN_OUT_EVERYWHERE_WAIT_S = 4.0


@require_http_methods(["POST"])
@never_cache
def sign_out_everywhere_view(request: HttpRequest) -> JsonResponse:
    """End every Workeasy360 session of this person - this one included - and
    every product session that takes back-channel logout (ADR-069).

    The answer reports what is true, not what was hoped: ``workeasy360`` is
    the number of platform sessions ended (synchronous, done), and
    ``products`` lists each product that was told, with ``delivered`` or
    ``pending``. A pending product is still being retried by the delivery
    worker; until it confirms, its session ends at its next entitlement
    re-check at the latest.
    """
    import time

    from django.contrib.auth import logout as django_logout
    from django.utils import timezone

    from workeasy360.platform.ecosystem import backchannel

    user = _user(request)
    blocked = limited(request, "session_revoke", user=user)
    if blocked is not None:
        return blocked
    user_id = user.id
    started = time.monotonic()
    # Only what this sign-out asked for: deliveries queued from this moment.
    asked_from = timezone.now()
    ended = account.sign_out_everywhere(user_id)
    django_logout(request)

    products = backchannel.pending_for_user(user_id, since=asked_from)
    while any(p["status"] == "pending" for p in products):
        if time.monotonic() - started > SIGN_OUT_EVERYWHERE_WAIT_S:
            break
        time.sleep(0.25)
        products = backchannel.pending_for_user(user_id, since=asked_from)
    return JsonResponse(
        {
            "ok": True,
            "workeasy360": {"sessions_ended": ended},
            "products": [
                {
                    "product_code": p["product_code"],
                    "name": p["display_name"],
                    "status": "delivered"
                    if p["status"] == "delivered"
                    else ("pending" if p["status"] == "pending" else "failed"),
                }
                for p in products
            ],
            "complete": all(p["status"] == "delivered" for p in products),
        }
    )


@require_http_methods(["GET"])
@never_cache
def activity_view(request: HttpRequest) -> JsonResponse:
    user = _user(request)
    return JsonResponse({"events": account.recent_account_events(user.id)})


# --- invitations: the invitee's side -------------------------------------------


def _token(body: dict[str, Any]) -> str:
    token = body.get("token")
    return token if isinstance(token, str) and len(token) <= 200 else ""


@require_http_methods(["POST"])
@never_cache
def invitation_preview_view(request: HttpRequest) -> JsonResponse:
    """What an invitation is for. POST, not GET, so the token never appears in
    a URL (and so never in an access log)."""
    from workeasy360.platform.administration import invitations

    blocked = limited(request, "invitation_public")
    if blocked is not None:
        return blocked
    user = getattr(request, "user", None)
    signed_in_email = user.email if user is not None and user.is_authenticated else None
    return JsonResponse(
        invitations.preview(_token(_body(request)), signed_in_email=signed_in_email)
    )


@require_http_methods(["POST"])
@never_cache
def invitation_accept_view(request: HttpRequest) -> JsonResponse:
    """Accept: as the signed-in invitee, or by creating the account (name and
    password) and signing it in here."""
    from workeasy360.platform.administration import invitations

    blocked = limited(request, "invitation_public")
    if blocked is not None:
        return blocked
    body = _body(request)
    user = getattr(request, "user", None)
    signed_in = user if user is not None and user.is_authenticated else None
    password = body.get("password")
    result = invitations.accept(
        _token(body),
        signed_in_user=signed_in,
        password=password if isinstance(password, str) else None,
        full_name=body.get("full_name")
        if isinstance(body.get("full_name"), str)
        else None,
    )
    if signed_in is None:
        establish_session(request, result.user, password=str(password or ""))
    return JsonResponse(
        {
            "organization_slug": result.organization_slug,
            "membership_id": str(result.membership_id),
            "account_created": result.account_created,
        }
    )


# --- product sign-in preferences (ADR-044) -----------------------------------


@require_http_methods(["GET", "PUT", "DELETE"])
@never_cache
def sign_in_preferences_view(request: HttpRequest) -> JsonResponse:
    """The caller's own product sign-in preferences: a default for every
    product (including products registered later) and optional per-product
    overrides. ``PUT`` changes only the keys sent (``inherit`` removes an
    override); ``DELETE`` is "Reset all to default". The person's own - no
    user id is accepted, no organization administrator can read or change it,
    and it grants nothing: entitlement, seat and access policy are decided
    before any preference is read. Takes effect on the next launch."""
    from workeasy360.contracts.errors import SignInPreferenceInvalid
    from workeasy360.platform.ecosystem import sign_in

    user = _user(request)
    where = connection_facts(request)
    if request.method == "PUT":
        body = _body(request)
        products = body.get("products")
        if products is not None and not isinstance(products, dict):
            raise SignInPreferenceInvalid("products must be an object.", field="products")
        sign_in.set_preferences(
            user.pk,
            default=body.get("default"),
            products=products,
            ip_prefix=where["ip_prefix"],
            device_label=where["device_label"],
        )
    elif request.method == "DELETE":
        sign_in.reset_preferences(
            user.pk, ip_prefix=where["ip_prefix"], device_label=where["device_label"]
        )
    return JsonResponse(_sign_in_preferences(user.pk))


def _sign_in_preferences(user_id: Any) -> dict[str, Any]:
    """Every product that can be entered (registered, not draft or retired),
    with the product's own sign-in mode, whether Workeasy360 sign-in works for
    it, the person's override and the effective result. Names and icons from
    the catalog; nothing about any organization."""
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.contracts.values.sign_in import DEFAULT_CHOICES, DEFAULT_PREFERENCE
    from workeasy360.platform.ecosystem import sign_in

    default, _overrides = sign_in.preferences_of(user_id)
    products = sign_in.product_sign_in(user_id)
    shown = catalog.product_presentations(list(products))
    rows = []
    for code, dto in products.items():
        row = dto.as_dict()
        presentation = shown.get(code, {})
        row["name"] = presentation.get("name") or dto.display_name
        row["icon"] = presentation.get("icon", "")
        rows.append(row)
    rows.sort(key=lambda r: (str(r["name"]).lower(), r["product_code"]))
    return {
        "default": (default or DEFAULT_PREFERENCE).value,
        "default_is_set": default is not None,
        "default_choices": [c.value for c in DEFAULT_CHOICES],
        "products": rows,
    }


# --- Accounts V3 (ADR-075): the pending email change ---------------------------------


@require_http_methods(["GET", "DELETE"])
@never_cache
def email_pending_view(request: HttpRequest) -> JsonResponse:
    """``GET``: the change of sign-in email waiting for its link, if any.
    ``DELETE``: withdraw it. The current email keeps working throughout."""
    from workeasy360.platform.administration import self_service

    user = _user(request)
    if request.method == "DELETE":
        where = connection_facts(request)
        self_service.cancel_email_change(
            user.id, ip_prefix=where["ip_prefix"], device_label=where["device_label"]
        )
        return JsonResponse({"pending": None})
    return JsonResponse({"pending": self_service.pending_email_change(user.id)})


@require_http_methods(["POST"])
@never_cache
def email_pending_resend_view(request: HttpRequest) -> JsonResponse:
    from workeasy360.platform.administration import self_service
    from workeasy360.platform.identity import portals as portal_links

    user = _user(request)
    blocked = limited(request, "email_change_resend", user=user)
    if blocked is not None:
        return blocked
    pending = self_service.resend_email_change(
        user.id, portal=portal_links.request_portal(request)
    )
    return JsonResponse({"pending": pending}, status=202)


# --- Accounts V3 (ADR-075): personal two-step verification ----------------------------


def _two_step_limited(request: HttpRequest, user: Any) -> JsonResponse | None:
    """Every attempt counts toward the 5-minute bucket; the day's budget
    counts wrong codes only (``_code_refused``, ADR-075 review L-2)."""
    from workeasy360.interfaces.api.v1.control_plane_http import failures_exhausted

    blocked = limited(request, "two_step", user=user)
    if blocked is not None:
        return blocked
    return failures_exhausted("two_step_daily", user=user)


def _code_refused(request: HttpRequest, user: Any) -> None:
    """A wrong current code on an account change: spent from the day's
    budget, and counted toward the same hourly notice as the sign-in's
    (someone holds the password and a live session)."""
    from workeasy360.interfaces.api.v1.control_plane_http import spend_failure
    from workeasy360.platform.administration import account_security

    spend_failure("two_step_daily", user=user)
    where = connection_facts(request)
    account_security.two_step_code_refused(
        user,
        ip_prefix=where["ip_prefix"],
        device_label=where["device_label"],
        at_sign_in=False,
    )


@require_http_methods(["GET"])
@never_cache
def two_step_view(request: HttpRequest) -> JsonResponse:
    from workeasy360.platform.identity import two_step

    user = _user(request)
    return JsonResponse(two_step.status(user.id))


@require_http_methods(["POST"])
@never_cache
def two_step_setup_view(request: HttpRequest) -> JsonResponse:
    """Start setting up (or replacing) the authenticator. Password, and a
    current code when two-step is already on. The secret is shown once."""
    from workeasy360.platform.identity import two_step

    user = _user(request)
    blocked = _two_step_limited(request, user)
    if blocked is not None:
        return blocked
    from workeasy360.contracts.errors import TwoStepCodeInvalid

    body = _body(request)
    try:
        setup = two_step.begin_setup(
            user.id, password=body.get("password"), code=body.get("code")
        )
    except TwoStepCodeInvalid:
        _code_refused(request, user)
        raise
    return JsonResponse(
        {
            "secret": setup.secret,
            "otpauth_uri": setup.otpauth_uri,
            "qr_svg_data_uri": setup.qr_svg_data_uri,
            "expires_at": setup.expires_at.isoformat(),
        }
    )


@require_http_methods(["POST"])
@never_cache
def two_step_confirm_view(request: HttpRequest) -> JsonResponse:
    """A code from the new authenticator turns it on. The recovery codes are
    in this answer and nowhere else; every other session ends."""
    from workeasy360.platform.administration import account_security

    user = _user(request)
    blocked = _two_step_limited(request, user)
    if blocked is not None:
        return blocked
    body = _body(request)
    where = connection_facts(request)
    done = account_security.confirm_two_step(
        user.id,
        code=body.get("code"),
        keep_public_id=session_public_id(request.session),
        ip_prefix=where["ip_prefix"],
        device_label=where["device_label"],
    )
    return JsonResponse(
        {
            "enabled": True,
            "replaced": done.replaced,
            "recovery_codes": done.recovery_codes,
            "other_sessions_ended": done.other_sessions_ended,
        }
    )


@require_http_methods(["POST"])
@never_cache
def two_step_recovery_codes_view(request: HttpRequest) -> JsonResponse:
    from workeasy360.platform.administration import account_security

    user = _user(request)
    blocked = _two_step_limited(request, user)
    if blocked is not None:
        return blocked
    from workeasy360.contracts.errors import TwoStepCodeInvalid

    body = _body(request)
    where = connection_facts(request)
    try:
        codes = account_security.regenerate_codes(
            user.id,
            password=body.get("password"),
            code=body.get("code"),
            ip_prefix=where["ip_prefix"],
            device_label=where["device_label"],
        )
    except TwoStepCodeInvalid:
        _code_refused(request, user)
        raise
    return JsonResponse({"recovery_codes": codes})


@require_http_methods(["POST"])
@never_cache
def two_step_disable_view(request: HttpRequest) -> JsonResponse:
    from workeasy360.platform.administration import account_security

    user = _user(request)
    blocked = _two_step_limited(request, user)
    if blocked is not None:
        return blocked
    from workeasy360.contracts.errors import TwoStepCodeInvalid

    body = _body(request)
    where = connection_facts(request)
    try:
        ended = account_security.turn_off_two_step(
            user.id,
            password=body.get("password"),
            code=body.get("code"),
            keep_public_id=session_public_id(request.session),
            ip_prefix=where["ip_prefix"],
            device_label=where["device_label"],
        )
    except TwoStepCodeInvalid:
        _code_refused(request, user)
        raise
    return JsonResponse({"enabled": False, "other_sessions_ended": ended})
