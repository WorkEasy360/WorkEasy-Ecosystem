"""The organization console API (Phase 8 extension): ``/orgs/<slug>/...``.

Thin adapters over ``platform.administration``. The tenancy middleware has
already resolved the membership, the organization's status and its access
policy; every service authorizes again - the permission list ``access_view``
returns drives the console's navigation and nothing else.

Inputs are read field by field (no generic deserialisation), so a body cannot
set a status, an owner flag, an organization or a version it was not asked for.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.contracts.errors import MembershipValidationError
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    page_response,
    query_page_args,
)
from workeasy360.platform.administration import (
    dashboard,
    invitations,
    members,
    security,
)
from workeasy360.platform.administration.access import console_access
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.client_ip import client_ip


def _ctx(request: HttpRequest) -> Any:
    return request.tenant_context  # type: ignore[attr-defined]


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MembershipValidationError("Malformed JSON body.", field="body") from exc
    if not isinstance(payload, dict):
        raise MembershipValidationError("The body must be a JSON object.", field="body")
    return payload


def _version(body: dict[str, Any], field: str = "version") -> int:
    raw = body.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise MembershipValidationError(f"{field} must be an integer.", field=field)
    return raw


# --- who am I here ---------------------------------------------------------------


@require_http_methods(["GET"])
def access_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.interfaces.api.v1.auth_views import connection_facts
    from workeasy360.platform.identity.sessions import session_public_id

    where = connection_facts(request)
    return JsonResponse(
        console_access(
            _ctx(request),
            session_ref=session_public_id(getattr(request, "session", None)),
            **where,
        )
    )


@require_http_methods(["GET"])
def dashboard_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    ctx = _ctx(request)
    try:
        window = int(request.GET.get("window") or 7)
    except ValueError:
        window = 7
    result = dashboard.summary(ctx, window_days=window)
    if authz.check(ctx, "commercial.subscription.view"):
        result["subscription"] = _subscription_summary(ctx)
    return JsonResponse(result)


def _subscription_summary(ctx: Any) -> dict[str, Any] | None:
    from django.utils import timezone

    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_for_organization,
    )

    now = timezone.now()
    live = [s for s in subscriptions_for_organization() if s.status.value != "expired"]
    if not live:
        return None
    current = live[0]
    items = current.effective_items(now)
    return {
        "status": current.status.value,
        "billing_interval": current.billing_interval.value,
        "current_period_end": current.current_period_end.isoformat(),
        "trial_ends_at": current.trial_ends_at.isoformat()
        if current.trial_ends_at
        else None,
        "products": sorted({item.product_code for item in items}),
    }


# --- members ---------------------------------------------------------------------


@require_http_methods(["GET"])
def members_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    page = members.directory(
        _ctx(request),
        status=request.GET.get("status") or None,
        search=request.GET.get("q") or None,
        **query_page_args(request),
    )
    return page_response(page)


@require_http_methods(["POST"])
def member_role_view(
    request: HttpRequest, org_slug: str, membership_id: uuid.UUID
) -> JsonResponse:
    role = _body(request).get("role")
    if not isinstance(role, str):
        raise MembershipValidationError("role is required.", field="role")
    return JsonResponse(members.change_role(_ctx(request), membership_id, role_key=role))


@require_http_methods(["POST"])
def member_suspend_view(
    request: HttpRequest, org_slug: str, membership_id: uuid.UUID
) -> JsonResponse:
    return JsonResponse(members.suspend(_ctx(request), membership_id))


@require_http_methods(["POST"])
def member_reactivate_view(
    request: HttpRequest, org_slug: str, membership_id: uuid.UUID
) -> JsonResponse:
    return JsonResponse(members.reactivate(_ctx(request), membership_id))


@require_http_methods(["POST"])
def member_sign_out_view(
    request: HttpRequest, org_slug: str, membership_id: uuid.UUID
) -> JsonResponse:
    return JsonResponse(members.sign_out(_ctx(request), membership_id))


# --- invitations -------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def invitations_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    ctx = _ctx(request)
    if request.method == "GET":
        return page_response(
            invitations.page(
                ctx, state=request.GET.get("state") or None, **query_page_args(request)
            )
        )
    blocked = limited(request, "invite", user=request.user)
    if blocked is not None:
        return blocked
    body = _body(request)
    role = body.get("role", "member")
    if not isinstance(role, str):
        raise MembershipValidationError("role must be a string.", field="role")
    return JsonResponse(
        invitations.invite(ctx, email=body.get("email"), role_key=role), status=201
    )


@require_http_methods(["POST"])
def invitation_resend_view(
    request: HttpRequest, org_slug: str, invitation_id: uuid.UUID
) -> JsonResponse:
    blocked = limited(request, "invite", user=request.user)
    if blocked is not None:
        return blocked
    return JsonResponse(invitations.resend(_ctx(request), invitation_id))


@require_http_methods(["POST"])
def invitation_revoke_view(
    request: HttpRequest, org_slug: str, invitation_id: uuid.UUID
) -> JsonResponse:
    return JsonResponse(invitations.revoke(_ctx(request), invitation_id))


# --- audit and activity -------------------------------------------------------------


@require_http_methods(["GET"])
def audit_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """The organization's own audit log - ``platform.audit.view``, declared
    since Phase 1 and enforced from here."""
    from workeasy360.platform.audit.selectors import audit_page

    ctx = _ctx(request)
    authz.require(ctx, "platform.audit.view")
    return page_response(
        audit_page(
            action_prefix=request.GET.get("action") or None, **query_page_args(request)
        )
    )


@require_http_methods(["GET"])
def sign_ins_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.platform.organizations.selectors import sign_in_page

    ctx = _ctx(request)
    authz.require(ctx, "platform.audit.view")
    return page_response(sign_in_page(ctx, **query_page_args(request)))


# --- security policy -----------------------------------------------------------------


@require_http_methods(["GET", "PUT"])
def security_policy_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    ctx = _ctx(request)
    if request.method == "GET":
        return JsonResponse(security.read(ctx))
    body = _body(request)
    return JsonResponse(
        security.update_authentication(
            ctx,
            require_mfa=body.get("require_mfa"),
            session_max_age_minutes=body.get("session_max_age_minutes"),
            password_min_length=body.get("password_min_length"),
            expected_version=_version(body),
        )
    )


@require_http_methods(["GET", "PUT"])
def network_policy_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.platform.identity.devices import network_prefix

    ctx = _ctx(request)
    address = client_ip(request)
    if request.method == "GET":
        policy = security.read(ctx)
    else:
        body = _body(request)
        policy = security.update_network(
            ctx,
            enabled=body.get("ip_allowlist_enabled"),
            entries=body.get("ip_allowlist"),
            expected_version=_version(body),
            client_ip=address,
        )
    # The caller's own address, so the page can offer "add my network" - it is
    # the caller's, and they are allowed to see where they are connecting from.
    policy["your_address"] = address
    policy["your_network"] = network_prefix(address)
    return JsonResponse(policy)
