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

import hashlib
import json
from typing import Any

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from workeasy360.contracts.errors import OrganizationValidationError
from workeasy360.platform.identity.middleware import SESSION_EPOCH_KEY
from workeasy360.platform.identity.services import LoginOutcome, authenticate
from workeasy360.platform.observability import throttle
from workeasy360.platform.observability.client_ip import client_ip, rate_limit_identity
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics
from workeasy360.platform.tenancy.resolution import reset_session_aal

logger = get_logger(__name__)

#: Cache-key namespace, distinct from the ecosystem limiter's so the two
#: cannot collide on a bucket name.
_KEY_PREFIX = "w360:auth:rl:"


@require_http_methods(["POST"])
def login_view(request: HttpRequest) -> JsonResponse:
    """Authenticate and establish a session (Task 4).

    **Throttled on two keys, and both are needed.** The per-account lockout
    in ``platform.identity.services`` stops a password list being worked
    through against one person, but it cannot see one password sprayed
    across a thousand accounts, and it is itself a denial-of-service lever -
    ten wrong guesses lock a real user out for fifteen minutes, so anything
    that reaches the lockout at will can keep a chosen account locked
    indefinitely. The per-address bucket is also the CPU guard: Argon2id is
    deliberately memory-hard, and Django hashes a dummy password even for an
    unknown email (its own timing-attack defence), so an unthrottled login
    endpoint is a workload anyone on the internet can schedule.
    """
    body = _body(request)
    email = str(body.get("email") or "")
    password = str(body.get("password") or "")

    limited = _throttle_login(request, email)
    if limited is not None:
        return limited

    outcome: LoginOutcome = authenticate(email, password)
    if not outcome.success:
        metrics.increment("identity_login_failures_total", reason=outcome.reason)
        if outcome.subject_id is not None:
            # The account holder's own trail: someone tried their account.
            from workeasy360.platform.identity.account import record_account_event
            from workeasy360.platform.identity.models import AccountEvent

            where = connection_facts(request)
            record_account_event(
                outcome.subject_id,
                AccountEvent.Kind.SIGN_IN_FAILED,
                reason=outcome.reason,
                ip_prefix=where["ip_prefix"],
                device_label=where["device_label"],
            )
        return JsonResponse({"code": outcome.reason}, status=401)

    user = outcome.user
    establish_session(request, user, password=password)
    if user.is_staff:
        from workeasy360.platform.administration.staff_users import record_staff_session

        record_staff_session(user, signed_in=True, where=connection_facts(request))

    metrics.increment("identity_login_success_total")
    return JsonResponse(
        {"user_id": str(user.id), "email": user.email, "name": user.full_name}
    )


def connection_facts(request: HttpRequest) -> dict[str, str]:
    """The coarse network and device a request came from - what the security
    trail and the session list record (``platform.identity.devices``)."""
    from workeasy360.platform.identity.devices import device_label, network_prefix

    return {
        "ip_prefix": network_prefix(client_ip(request)),
        "device_label": device_label(request.META.get("HTTP_USER_AGENT")),
    }


def establish_session(request: HttpRequest, user: Any, *, password: str) -> None:
    """Sign ``user`` in on this request's session - the one place a session
    is established (the login form and an accepted invitation both use it).

    ``password`` is the one just verified or set; it is used here, once, to
    judge which organizations' password policies it falls short of, and is
    never stored - only the list of those organizations is.
    """
    import time

    from workeasy360.contracts.values.access_policy import password_meets
    from workeasy360.platform.identity.account import record_sign_in
    from workeasy360.platform.identity.sessions import (
        SESSION_DEVICE_KEY,
        SESSION_IP_PREFIX_KEY,
    )
    from workeasy360.platform.organizations.selectors import (
        password_min_lengths_for_user,
    )
    from workeasy360.platform.tenancy.resolution import (
        SESSION_AUTHENTICATED_AT_KEY,
        SESSION_PASSWORD_NONCOMPLIANT_KEY,
    )

    # django_login rotates the session key (CVE-class session fixation) -
    # Django does this internally on every call, which is why the session
    # must be established *here*, once, rather than the frontend merely
    # trusting that authenticate() succeeded.
    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session[SESSION_EPOCH_KEY] = user.session_epoch
    # AAL 1: a password alone. Explicitly reset rather than left to the
    # default: django_login keeps session data when the same user signs in
    # again without logging out, and a new sign-in must never inherit an
    # earlier step-up. AAL 2 is reached only through mfa_views.step_up_view.
    reset_session_aal(request.session)

    where = connection_facts(request)
    request.session[SESSION_IP_PREFIX_KEY] = where["ip_prefix"]
    request.session[SESSION_DEVICE_KEY] = where["device_label"]
    request.session[SESSION_AUTHENTICATED_AT_KEY] = time.time()
    request.session[SESSION_PASSWORD_NONCOMPLIANT_KEY] = [
        organization_id
        for organization_id, minimum in password_min_lengths_for_user(user.id).items()
        if not password_meets(password, min_length=minimum)
    ]
    record_sign_in(user, ip_prefix=where["ip_prefix"], device_label=where["device_label"])


def _throttle_login(request: HttpRequest, email: str) -> JsonResponse | None:
    """Count this attempt against both buckets; return a 429 if either is
    exhausted, otherwise ``None``.

    Counted **before** credentials are examined, so that the expensive part
    never runs for a caller who is already over the limit - the whole point
    of the address bucket.

    The account key is a hash of the normalised email, never the email
    itself: cache keys reach Redis, Redis reaches backups and dashboards,
    and an address is PII (12-security.md section 5). A hash buckets
    identically without storing who was throttled.
    """
    from workeasy360.platform.identity.email import normalize_email

    limits = getattr(settings, "AUTH_RATE_LIMITS", {})
    address = rate_limit_identity(client_ip(request))
    account = hashlib.sha256(normalize_email(email or "").encode("utf-8")).hexdigest()

    for bucket, identity in (("login_ip", address), ("login_account", account)):
        limit, window = limits.get(bucket, (0, 60))
        limit, window = int(limit), max(1, int(window))
        if limit <= 0:
            continue
        key = f"{_KEY_PREFIX}{bucket}:{identity}:{throttle.window_slot(window)}"
        if throttle.hit(key, window) <= limit:
            continue

        retry_after = throttle.retry_after(window)
        metrics.increment("identity_login_throttled_total", bucket=bucket)
        # Bucket only - never the address or the account hash. Metric labels
        # are cardinality-bounded (FORBIDDEN_LABELS); who was throttled
        # belongs in the log line below.
        logger.warning("identity.login.throttled", bucket=bucket, limit=limit)
        response = JsonResponse(
            {
                "code": "rate_limited",
                "detail": "Too many sign-in attempts. Try again shortly.",
            },
            status=429,
        )
        response["Retry-After"] = str(retry_after)
        return response
    return None


@require_http_methods(["POST"])
def logout_view(request: HttpRequest) -> JsonResponse:
    """End the session. Idempotent - logging out twice is not an error."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_staff:
        from workeasy360.platform.administration.staff_users import record_staff_session

        record_staff_session(user, signed_in=False, where=connection_facts(request))
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
            # Phase 8 extension: where the shell may send this person, and
            # whether they can satisfy an organization's "require MFA". The
            # consoles re-check everything server-side on every request.
            "mfa_enrolled": user.has_mfa,
            "consoles": _consoles(user),
        }
    )


def _consoles(user: Any) -> dict[str, bool]:
    from workeasy360.commercial.partners.access import has_partner_membership
    from workeasy360.platform.authz.staff import staff_permissions

    return {
        "staff": bool(user.is_staff and staff_permissions(user.id)),
        "partner": has_partner_membership(user.id),
    }


@require_http_methods(["GET", "POST"])
def organizations_view(request: HttpRequest) -> JsonResponse:
    """The organization chooser (GET, Task 5/25-27) and organization
    creation (POST, Tasks 2-13).

    Session-authenticated, not tenant-scoped: there is no organization
    context yet for either verb, which is exactly why this endpoint exists
    outside the ``/orgs/<slug>/...`` shape.
    """
    if request.method == "POST":
        return _create_organization(request)
    return _list_organizations(request)


def _list_organizations(request: HttpRequest) -> JsonResponse:
    from workeasy360.platform.organizations.selectors import organizations_for_user

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return JsonResponse({"code": "login_required"}, status=401)

    # Clamped to [1, MAX_PAGE_SIZE] by the selector; a non-numeric value is
    # the default page, not a 500.
    try:
        limit = int(request.GET.get("limit") or 25)
    except ValueError:
        limit = 25
    cursor = request.GET.get("cursor") or None
    search = request.GET.get("q") or None

    page = organizations_for_user(user.id, limit=limit, cursor=cursor, search=search)
    return JsonResponse(
        {
            "organizations": page.items,
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }
    )


def _create_organization(request: HttpRequest) -> JsonResponse:
    """Task 3-13: create an organization, its creator becomes owner.

    Rate-limited on the caller's own account (Task 10/11) - the same shape
    as login's per-account bucket, because the abuse this guards against is
    identical: an authenticated caller scripting a loop, not distributed
    traffic (that is what the per-IP bucket below is for).
    """
    # Domain errors (validation, idempotency conflict, permission denial)
    # are deliberately not caught here: workeasy360.interfaces.api.errors.
    # DomainErrorMiddleware converts every Workeasy360Error subclass to its
    # declared status code uniformly, with a correlation id attached - the
    # same path every other view in this API relies on (Task 36).
    from workeasy360.platform.onboarding.services import create_organization

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return JsonResponse({"code": "login_required"}, status=401)

    limited = _throttle_organization_creation(request, user.id)
    if limited is not None:
        return limited

    body = _body(request)
    # Mass-assignment allowlist (Task 55): "name" is the only field this
    # endpoint reads from the request body. organization_id, owner_id,
    # membership role and lifecycle status are never accepted from the
    # client - create_organization() derives every one of them itself.
    name = body.get("name")
    idempotency_key = body.get("idempotency_key") or None
    # Refused rather than coerced or truncated: truncating would let two
    # distinct keys collide on one claim (OrganizationCreationRequest.key is
    # 128 characters).
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str) or len(idempotency_key) > 128
    ):
        raise OrganizationValidationError(
            "idempotency_key must be a string of at most 128 characters.",
            field="idempotency_key",
        )

    result = create_organization(
        user_id=user.id, name=name, idempotency_key=idempotency_key
    )

    metrics.increment(
        "organization_creation_http_total",
        outcome="created" if result.created else "replay",
    )
    return JsonResponse(
        {
            "organization_id": str(result.organization_id),
            "slug": result.slug,
            "display_name": result.display_name,
            "membership_id": str(result.membership_id),
        },
        status=201 if result.created else 200,
    )


def _throttle_organization_creation(
    request: HttpRequest, user_id: Any
) -> JsonResponse | None:
    """Two buckets, same shape as login's (Task 11/46): per-account (this
    identity is creating organizations too fast) and per-address (this
    connection is scripting the endpoint, whoever it is authenticated as).
    Uses the same trusted-proxy client-IP resolution as every other
    rate-limited endpoint (Task 47/D-009) - never ``REMOTE_ADDR`` alone.
    """
    limits = getattr(settings, "ORGANIZATION_CREATION_RATE_LIMITS", {})
    address = rate_limit_identity(client_ip(request))

    for bucket, identity in (
        ("org_create_account", str(user_id)),
        ("org_create_ip", address),
    ):
        limit, window = limits.get(bucket, (0, 60))
        limit, window = int(limit), max(1, int(window))
        if limit <= 0:
            continue
        key = f"{_KEY_PREFIX}{bucket}:{identity}:{throttle.window_slot(window)}"
        if throttle.hit(key, window) <= limit:
            continue

        retry_after = throttle.retry_after(window)
        metrics.increment("organization_creation_throttled_total", bucket=bucket)
        logger.warning("organizations.creation.throttled", bucket=bucket, limit=limit)
        response = JsonResponse(
            {
                "code": "organization_creation_rate_limited",
                "detail": "Too many organization-creation attempts. Try again shortly.",
            },
            status=429,
        )
        response["Retry-After"] = str(retry_after)
        return response
    return None


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
