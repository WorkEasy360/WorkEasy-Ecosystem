"""A person's own account: password, sessions, security trail - and the
invitation acceptance page (Phase 8 extension).

Session-authenticated, not tenant-scoped, like ``auth_views`` and
``mfa_views``: these belong to the global account. The caller only ever acts on
their *own* account - no endpoint here takes a user id.

**Step-up.** Changing a password and ending a session are dangerous: the
session must hold AAL 2 (a verified second factor within the step-up window).
A person without MFA cannot reach AAL 2, so for them revoking sessions is
unavailable - and changing the password is their path instead: it ends every
other session anyway (``identity.account.change_password``) and is guarded by
the current password, which the session alone does not prove.
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
from workeasy360.platform.identity.sessions import session_public_id
from workeasy360.platform.tenancy.resolution import (
    SESSION_PASSWORD_NONCOMPLIANT_KEY,
    session_aal,
)


def _user(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise AuthenticationRequired("Sign in to continue.")
    return user


def _step_up_required(detail: str) -> JsonResponse:
    return JsonResponse({"code": "step_up_required", "detail": detail}, status=401)


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
    return JsonResponse({"ok": True})


@require_http_methods(["GET"])
@never_cache
def sessions_view(request: HttpRequest) -> JsonResponse:
    user = _user(request)
    return JsonResponse(
        {
            "sessions": account.list_sessions(
                user.id, current_public_id=session_public_id(request.session)
            ),
            "aal": session_aal(request.session),
        }
    )


@require_http_methods(["POST"])
@never_cache
def session_revoke_view(request: HttpRequest, session_id: str) -> JsonResponse:
    user = _user(request)
    blocked = limited(request, "session_revoke", user=user)
    if blocked is not None:
        return blocked
    if session_aal(request.session) < 2:
        return _step_up_required("Verify a code before signing a session out.")
    try:
        target = uuid.UUID(str(session_id))
    except ValueError as exc:
        raise SessionNotFound("No such session.") from exc
    current = session_public_id(request.session)
    if target == current:
        # Ending your own session is signing out, which needs no step-up and
        # has its own endpoint; refusing here keeps this one unambiguous.
        raise SessionNotFound("Use sign out to end this session.")
    where = connection_facts(request)
    account.revoke_session(user.id, target, **where)
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@never_cache
def sessions_revoke_others_view(request: HttpRequest) -> JsonResponse:
    user = _user(request)
    blocked = limited(request, "session_revoke", user=user)
    if blocked is not None:
        return blocked
    if session_aal(request.session) < 2:
        return _step_up_required("Verify a code before signing other sessions out.")
    where = connection_facts(request)
    ended = account.revoke_other_sessions(
        user.id, keep_public_id=session_public_id(request.session), **where
    )
    return JsonResponse({"ok": True, "ended": ended})


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
