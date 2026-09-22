"""HTTP adapters for platform authentication and the organization chooser
(Tasks 4, 5). Not under the tenant path shape - reached before any
organization is selected, which is the whole point of existing.

**CSRF.** Login is the one write in this module that is genuinely
unauthenticated when it starts, and it still goes through the ordinary CSRF
middleware rather than being exempted - exempting it would be the actual
hole, since a forged cross-site login POST that authenticates the
*attacker's* account into the victim's browser is a session-fixation-
adjacent attack CSRF protection exists to prevent regardless of which side
starts unauthenticated.

The double-submit cookie, though, does **not** appear on its own here the
way it would on a template-rendered site: Django's ``CsrfViewMiddleware``
only issues it when something in the request cycle calls
``get_token()`` - normally ``{% csrf_token %}`` in a template, which a JSON
API never renders. ``session_view`` is therefore decorated with
``@ensure_csrf_cookie``: it is the endpoint the frontend shell calls on
every load to answer "is a session live", so it doubles as the point that
guarantees the cookie exists before the browser could ever need to submit
one back on ``login_view``.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from workeasy360.platform.identity.middleware import SESSION_EPOCH_KEY
from workeasy360.platform.identity.services import LoginOutcome, authenticate
from workeasy360.platform.observability.metrics import metrics


@require_http_methods(["POST"])
def login_view(request: HttpRequest) -> JsonResponse:
    """Authenticate and establish a session (Task 4)."""
    body = _body(request)
    email = str(body.get("email") or "")
    password = str(body.get("password") or "")

    outcome: LoginOutcome = authenticate(email, password)
    if not outcome.success:
        metrics.increment("identity_login_failures_total", reason=outcome.reason)
        return JsonResponse({"code": outcome.reason}, status=401)

    user = outcome.user
    # django_login rotates the session key (CVE-class session fixation) -
    # Django does this internally on every call, which is why the session
    # must be established *here*, once, rather than the frontend merely
    # trusting that authenticate() succeeded.
    django_login(request, user)
    request.session[SESSION_EPOCH_KEY] = user.session_epoch
    # AAL 1: a password alone. Nothing in this phase's UI performs a
    # dangerous action (platform.seat.assign is not `dangerous`), so no
    # step-up is required to use it - see docs/phase-4/07-security.md for
    # the MFA-UI gap this leaves, carried forward rather than built now.
    request.session["w360_aal"] = 1

    metrics.increment("identity_login_success_total")
    return JsonResponse(
        {"user_id": str(user.id), "email": user.email, "name": user.full_name}
    )


@require_http_methods(["POST"])
def logout_view(request: HttpRequest) -> JsonResponse:
    """End the session. Idempotent - logging out twice is not an error."""
    django_logout(request)
    return JsonResponse({"ok": True})


@require_http_methods(["GET"])
@ensure_csrf_cookie
def session_view(request: HttpRequest) -> JsonResponse:
    """Whether a session is live, for the frontend shell to decide whether
    to render signed-out UI without a failed API call first.

    ``@ensure_csrf_cookie`` - see the module docstring: this is the call the
    frontend makes early enough (on shell mount, before any form can be
    submitted) that the CSRF cookie is guaranteed to exist by the time
    ``login_view`` or any other write is reachable.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return JsonResponse({"authenticated": False})
    return JsonResponse(
        {
            "authenticated": True,
            "user_id": str(user.id),
            "email": user.email,
            "name": user.full_name,
        }
    )


@require_http_methods(["GET"])
def organizations_view(request: HttpRequest) -> JsonResponse:
    """Every organization this signed-in user actively belongs to - the
    organization chooser's data (Task 5).

    Session-authenticated, not tenant-scoped: there is no organization
    context yet, which is exactly why this endpoint exists.
    """
    from workeasy360.platform.organizations.selectors import organizations_for_user

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return JsonResponse({"code": "login_required"}, status=401)

    organizations = organizations_for_user(user.id)
    return JsonResponse({"organizations": organizations})


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
