"""Super Admin: users, communication, platform settings and the network
break-glass (Phase 8 extension, Phase 9). Thin adapters; every service
authorizes (``platform.staff_user.*``, ``platform.staff_communication.*``,
``platform.staff_settings.*``, ``platform.staff_customer.manage``)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse

from workeasy360.contracts.errors import CommunicationValidationError
from workeasy360.contracts.values import json_input
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    query_page_args,
    staff_view,
)
from workeasy360.platform.administration import staff_users
from workeasy360.platform.notifications import communication


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json_input.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CommunicationValidationError("Malformed JSON body.", field="body") from exc
    if not isinstance(payload, dict):
        raise CommunicationValidationError(
            "The body must be a JSON object.", field="body"
        )
    return payload


def _version(body: dict[str, Any]) -> int | None:
    raw = body.get("version")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise CommunicationValidationError("version must be an integer.", field="version")
    return raw


# --- users -----------------------------------------------------------------------


@staff_view(["GET"])
def users_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """The user directory: search, and a segment (Explorers, Trial, Customers,
    Unverified, Suspended, Deactivated) - Phase 9."""
    from workeasy360.commercial.controlplane import users

    return JsonResponse(
        users.directory(
            ctx,
            search=request.GET.get("q") or None,
            segment=request.GET.get("segment") or None,
            **query_page_args(request),
        )
    )


@staff_view(["GET"])
def user_detail_view(
    request: HttpRequest, ctx: TenantContext, user_id: UUID
) -> JsonResponse:
    from workeasy360.commercial.controlplane import users

    return JsonResponse(users.detail(ctx, user_id))


@staff_view(["POST"])
def user_action_view(
    request: HttpRequest, ctx: TenantContext, user_id: UUID, action: str
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        staff_users.act(
            ctx, user_id, action=action.replace("-", "_"), reason=body.get("reason")
        )
    )


# --- communication ------------------------------------------------------------------


@staff_view(["GET"])
def templates_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse({"results": communication.list_templates(ctx)})


@staff_view(["PUT", "DELETE"])
def template_detail_view(
    request: HttpRequest, ctx: TenantContext, key: str
) -> JsonResponse:
    body = _body(request)
    if request.method == "DELETE":
        version = _version(body)
        return JsonResponse(
            communication.reset_override(ctx, key=key, expected_version=version or 0)
        )
    return JsonResponse(
        communication.set_override(
            ctx,
            key=key,
            subject=body.get("subject"),
            body_text=body.get("body_text"),
            expected_version=_version(body),
        )
    )


@staff_view(["POST"])
def template_preview_view(
    request: HttpRequest, ctx: TenantContext, key: str
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        communication.preview(
            ctx, key=key, subject=body.get("subject"), body_text=body.get("body_text")
        )
    )


@staff_view(["POST"])
def template_test_view(
    request: HttpRequest, ctx: TenantContext, key: str
) -> JsonResponse:
    blocked = limited(request, "communication_test", user=request.user)
    if blocked is not None:
        return blocked
    communication.send_test(ctx, key=key, recipient_email=request.user.email)
    return JsonResponse({"queued": True, "to": request.user.email}, status=202)


@staff_view(["GET", "PUT"])
def sender_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(communication.sender_settings(ctx))
    body = _body(request)
    return JsonResponse(
        communication.update_sender_settings(
            ctx,
            sender_name=body.get("sender_name"),
            reply_to=body.get("reply_to"),
            expected_version=_version(body) or 0,
        )
    )


# --- platform settings (Phase 9) ------------------------------------------------------


@staff_view(["GET", "PUT"])
def platform_settings_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    from workeasy360.platform.administration import site

    if request.method == "GET":
        return JsonResponse(site.settings_view(ctx))
    body = _body(request)
    return JsonResponse(
        site.update_settings(ctx, body, expected_version=_version(body) or 0)
    )


# --- network break-glass ---------------------------------------------------------------


@staff_view(["POST"])
def network_reset_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    """Turn off a customer's IP allow-list that locked the customer out."""
    from workeasy360.platform.administration.security import reset_network_as_staff

    body = _body(request)
    return JsonResponse(
        reset_network_as_staff(ctx, organization_id, reason=str(body.get("reason") or ""))
    )
