"""The Admin Panel programme's endpoints (2026-11-09).

Thin adapters, like ``admin_views``: authenticate, read the fields that matter, call one
service, shape the result. Every service authorizes for itself with an explicit
``platform.staff_*`` permission, so nothing here decides who may do what.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.controlplane import overview, partner_applications, search
from workeasy360.commercial.partners import kyc
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1.commercial_views import _body as read_json_body
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    partner_view,
    staff_view,
)
from workeasy360.platform.observability.client_ip import client_ip

# --- dashboard ---------------------------------------------------------------------------


@staff_view(["GET"])
def dashboard_overview_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        overview.overview(
            ctx,
            raw_from=request.GET.get("from") or None,
            raw_to=request.GET.get("to") or None,
            days=request.GET.get("days") or None,
            currency=request.GET.get("currency") or None,
        )
    )


@staff_view(["GET"])
def dashboard_growth_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(overview.growth_trend(ctx, months=request.GET.get("months")))


@staff_view(["GET"])
def dashboard_money_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        overview.money_trend(
            ctx,
            months=request.GET.get("months"),
            currency=request.GET.get("currency") or None,
        )
    )


# --- search ------------------------------------------------------------------------------


@staff_view(["GET"])
def search_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(search.search(ctx, request.GET.get("q")))


# --- KYC: the review -----------------------------------------------------------------------


@staff_view(["GET", "POST"])
def kyc_requirements_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse({"results": kyc.requirements(ctx)})
    return JsonResponse(kyc.create_requirement(ctx, read_json_body(request)), status=201)


@staff_view(["PUT"])
def kyc_requirement_view(
    request: HttpRequest, ctx: TenantContext, requirement_id: UUID
) -> JsonResponse:
    return JsonResponse(kyc.update_requirement(ctx, requirement_id, read_json_body(request)))


@staff_view(["GET"])
def kyc_applications_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        kyc.queue(
            ctx,
            status=request.GET.get("status") or None,
            search=request.GET.get("q") or None,
            limit=request.GET.get("limit"),
            offset=request.GET.get("offset"),
        )
    )


@staff_view(["GET"])
def kyc_application_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    return JsonResponse(kyc.detail(ctx, partner_id))


@staff_view(["POST"])
def kyc_start_review_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    return JsonResponse(kyc.start_review(ctx, partner_id))


@staff_view(["POST"])
def kyc_decision_view(request: HttpRequest, ctx: TenantContext, partner_id: UUID) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        kyc.decide(
            ctx,
            partner_id,
            decision=body.get("decision"),
            reason=body.get("reason"),
            flagged_requirements=body.get("flagged_requirements"),
            experience=body.get("experience"),
            expected_version=body.get("expected_version"),
        )
    )


def _document_response(data: bytes, content_type: str, filename: str) -> HttpResponse:
    """A restricted document, opened in place and never cached or framed. The type
    is what the bytes were found to be, never what was declared, and the sandbox
    stops anything in it from running as this site."""
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in filename)[:120]
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{safe or "document"}"'
    response["Cache-Control"] = "no-store, private"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; object-src 'self'"
    return response


@staff_view(["GET"])
@never_cache
def kyc_document_content_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID, document_id: UUID
) -> HttpResponse:
    from workeasy360.platform.identity.devices import network_prefix

    return _document_response(
        *kyc.document_content(
            ctx, partner_id, document_id, ip_prefix=network_prefix(client_ip(request))
        )
    )


# --- KYC: the partner's side ---------------------------------------------------------------


@partner_view(["GET", "PUT"])
def partner_kyc_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    assert ctx.partner_id is not None
    if request.method == "GET":
        return JsonResponse(kyc.application(ctx, ctx.partner_id))
    body = read_json_body(request)
    return JsonResponse(
        kyc.update_details(
            ctx,
            ctx.partner_id,
            body.get("details"),
            expected_version=body.get("expected_version"),
        )
    )


@partner_view(["POST"])
def partner_kyc_documents_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """One document as a raw request body, like a partner's logo: the type comes from
    ``Content-Type`` and is checked against the bytes by the files platform."""
    assert ctx.partner_id is not None
    return JsonResponse(
        kyc.upload_document(
            ctx,
            ctx.partner_id,
            requirement_key=request.GET.get("requirement"),
            filename=request.GET.get("filename"),
            content=request.body,
            content_type=request.headers.get("Content-Type", ""),
        ),
        status=201,
    )


@partner_view(["DELETE"])
def partner_kyc_document_view(
    request: HttpRequest, ctx: TenantContext, document_id: UUID
) -> JsonResponse:
    assert ctx.partner_id is not None
    kyc.remove_document(ctx, ctx.partner_id, document_id)
    return JsonResponse({"ok": True})


@partner_view(["GET"])
@never_cache
def partner_kyc_document_content_view(
    request: HttpRequest, ctx: TenantContext, document_id: UUID
) -> HttpResponse:
    assert ctx.partner_id is not None
    return _document_response(*kyc.document_content(ctx, ctx.partner_id, document_id))


@partner_view(["POST"])
def partner_kyc_submit_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    assert ctx.partner_id is not None
    return JsonResponse(kyc.submit(ctx, ctx.partner_id))


# --- the public application --------------------------------------------------------------


@require_http_methods(["POST"])
@never_cache
def partner_application_view(request: HttpRequest) -> JsonResponse:
    """The same answer for every outcome, as signup gives: nothing here says whether
    an address is registered."""
    body: dict[str, Any] = read_json_body(request)
    email = str(body.get("email") or "").strip()
    blocked = limited(request, "signup", account_email=email or None)
    if blocked is not None:
        return blocked
    partner_applications.apply(
        business_name=body.get("business_name"),
        first_name=body.get("first_name"),
        last_name=body.get("last_name"),
        email=email,
        password=body.get("password"),
        country=body.get("country"),
        phone=body.get("phone"),
        interest=body.get("interest"),
    )
    return JsonResponse({"status": "accepted"}, status=202)
