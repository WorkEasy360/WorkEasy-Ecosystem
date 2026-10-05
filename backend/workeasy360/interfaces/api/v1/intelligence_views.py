"""The organization's and the Super Admin's views of the AI and analytics
platform (Phase 8, Tasks 87-90).

Organization routes live under the tenant path (``/orgs/<slug>/...``), so the
tenancy middleware has resolved the caller's organization from a verified
membership; nothing here reads an organization id from a body or a query
string. Session-authenticated and CSRF-protected like every other tenant
write. Staff routes live under ``/admin/`` behind ``staff_view``.

Thin (F-7): parse, call one service, shape. Authorization, validation and
D-016 write ordering live in the services. Nothing returned here carries a
provider credential, a prompt, a model response or a document's content -
none is read (Task 91).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.contracts.errors import AIInvalidRequest
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1.control_plane_http import staff_view

# --- the organization's AI --------------------------------------------------------------


@require_http_methods(["GET", "PATCH"])
def ai_settings_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.intelligence.ai import policy

    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        return JsonResponse(policy.settings_for(ctx))
    body = _body(request)
    updated = policy.update_settings(
        ctx,
        expected_revision=body.get("revision"),
        ai_enabled=body.get("ai_enabled"),
        monthly_budget_micros=body.get("monthly_budget_micros", ...),
        allowed_providers=body.get("allowed_providers"),
        allow_provider_fallback=body.get("allow_provider_fallback"),
    )
    return JsonResponse({"policy": updated})


@require_http_methods(["GET"])
def ai_usage_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.intelligence.ai import selectors

    return JsonResponse(selectors.usage(request.tenant_context))  # type: ignore[attr-defined]


@require_http_methods(["GET"])
def ai_requests_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.intelligence.ai import selectors

    return JsonResponse(
        selectors.recent_executions(
            request.tenant_context,  # type: ignore[attr-defined]
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor"),
        )
    )


# --- the organization's analytics shell -----------------------------------------------


@require_http_methods(["GET"])
def analytics_metrics_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.intelligence.analytics import service

    return JsonResponse(
        {"results": service.organization_metrics(request.tenant_context)}  # type: ignore[attr-defined]
    )


@require_http_methods(["GET"])
def analytics_query_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.intelligence.analytics import service

    params: dict[str, Any] = {
        key: request.GET.get(key)
        for key in ("metric", "version", "start", "end", "granularity", "group_by")
        if request.GET.get(key) not in (None, "")
    }
    return JsonResponse(
        service.organization_query(request.tenant_context, params)  # type: ignore[attr-defined]
    )


# --- Super Admin ------------------------------------------------------------------------


@staff_view(["GET"])
def admin_ai_registry_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    from workeasy360.intelligence.ai import staff

    return JsonResponse(staff.registry_view(ctx))


@staff_view(["POST"])
def admin_ai_provider_status_view(
    request: HttpRequest, ctx: TenantContext, provider_key: str
) -> JsonResponse:
    from workeasy360.intelligence.ai import staff

    body = _body(request)
    return JsonResponse(
        staff.set_provider_status(
            ctx, provider_key, status=body.get("status"), reason=body.get("reason")
        )
    )


@staff_view(["POST"])
def admin_ai_models_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    from workeasy360.intelligence.ai import staff

    body = _body(request)
    fields = {
        key: body.get(key)
        for key in (
            "key",
            "provider",
            "model_id",
            "display_name",
            "tier",
            "capabilities",
            "context_window_tokens",
            "max_output_tokens",
            "embedding_dimensions",
            "input_micros_per_mtok",
            "output_micros_per_mtok",
            "priority",
        )
        if key in body
    }
    return JsonResponse(
        staff.create_model(ctx, reason=body.get("reason"), **fields), status=201
    )


@staff_view(["PATCH"])
def admin_ai_model_view(
    request: HttpRequest, ctx: TenantContext, model_key: str
) -> JsonResponse:
    from workeasy360.intelligence.ai import staff

    body = _body(request)
    return JsonResponse(
        staff.update_model(
            ctx,
            model_key,
            reason=body.get("reason"),
            status=body.get("status"),
            priority=body.get("priority"),
        )
    )


@staff_view(["POST"])
def admin_ai_model_prices_view(
    request: HttpRequest, ctx: TenantContext, model_key: str
) -> JsonResponse:
    from workeasy360.intelligence.ai import staff

    body = _body(request)
    return JsonResponse(
        staff.publish_price(
            ctx,
            model_key,
            input_micros_per_mtok=body.get("input_micros_per_mtok"),
            output_micros_per_mtok=body.get("output_micros_per_mtok"),
            reason=body.get("reason"),
        ),
        status=201,
    )


@staff_view(["GET"])
def admin_ai_organization_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    from workeasy360.intelligence.ai import staff

    return JsonResponse(staff.organization_ai(ctx, organization_id))


@staff_view(["POST"])
def admin_ai_organization_policy_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    from workeasy360.intelligence.ai import staff

    body = _body(request)
    return JsonResponse(
        staff.set_organization_policy(
            ctx,
            organization_id,
            reason=body.get("reason"),
            platform_suspended=body.get("platform_suspended"),
            platform_cap_micros=body.get("platform_cap_micros", ...),
            premium_models_allowed=body.get("premium_models_allowed"),
        )
    )


def _body(request: HttpRequest) -> dict[str, Any]:
    """A JSON object body, or a 400 raised before any service runs (D-016)."""
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise AIInvalidRequest("The request body must be valid JSON.") from None
    if not isinstance(payload, dict):
        raise AIInvalidRequest("The request body must be a JSON object.")
    return payload
