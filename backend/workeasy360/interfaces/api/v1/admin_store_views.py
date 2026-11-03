"""Super Admin -> Store over HTTP (ADR-056): ``/api/v1/admin/store/...``.

Thin, like every view (F-7): parse, call one service, shape the response.
Authorization, validation, versioning and audit live in the services -
``controlplane.store_admin``, ``pricing.services``, ``partners.price_ranges``.
"""

from __future__ import annotations

from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse

from workeasy360.commercial.controlplane import store_admin
from workeasy360.commercial.partners import price_ranges
from workeasy360.commercial.pricing import services as pricing
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.control_plane_http import staff_view
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import acting_scope

_body = legacy._body
_moment = legacy._moment


def _days(request: HttpRequest) -> int:
    raw = request.GET.get("days") or "30"
    try:
        return int(raw)
    except ValueError as exc:
        raise CatalogValidationError(
            "days must be a whole number.", field="days"
        ) from exc


@staff_view(["GET"])
def dashboard_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(store_admin.dashboard(ctx, days=_days(request)))


@staff_view(["GET"])
def commercial_rules_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        store_admin.commercial_rules(ctx, currency=request.GET.get("currency"))
    )


@staff_view(["POST"])
def product_commission_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Set the commission every partner earns on one product or plan."""
    body = _body(request)
    result = store_admin.set_product_commission(
        ctx,
        product_code=body.get("product_code"),
        plan_code=body.get("plan_code", ""),
        rate_bp=body.get("rate_bp", 0),
        fixed_amount_minor=body.get("fixed_amount_minor"),
        fixed_currency=body.get("fixed_currency", ""),
        recurring=body.get("recurring", False),
        recurring_months=body.get("recurring_months"),
        effective_from=_moment(body, "effective_from"),
        reason=body.get("reason"),
    )
    return JsonResponse(result, status=201)


@staff_view(["GET"])
def quote_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """The central engine's breakdown for one plan, cycle and channel."""
    params = request.GET
    return JsonResponse(
        store_admin.quote_preview(
            ctx,
            plan_version_id=params.get("plan_version_id"),
            interval=params.get("interval"),
            currency=params.get("currency"),
            channel=params.get("channel"),
            partner_id=params.get("partner_id") or None,
        )
    )


@staff_view(["GET"])
def plan_board_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    return JsonResponse(store_admin.plan_board(ctx, product_id=product_id))


@staff_view(["POST"])
def plan_duplicate_view(
    request: HttpRequest, ctx: TenantContext, plan_id: UUID
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        store_admin.duplicate_plan(
            ctx, plan_id=plan_id, code=body.get("code"), name=body.get("name")
        ),
        status=201,
    )


@staff_view(["POST"])
def price_close_view(
    request: HttpRequest, ctx: TenantContext, price_id: UUID
) -> JsonResponse:
    """End a running price now or at ``at`` ("effective until")."""
    body = _body(request)
    with acting_scope(ctx):
        price = pricing.close_price(ctx, price_id=price_id, at=_moment(body, "at"))
    return JsonResponse(
        {
            "id": str(price.id),
            "valid_to": price.valid_to.isoformat() if price.valid_to else None,
        }
    )


@staff_view(["POST"])
def price_cancel_view(
    request: HttpRequest, ctx: TenantContext, price_id: UUID
) -> HttpResponse:
    """Withdraw a price change that has not started yet."""
    with acting_scope(ctx):
        pricing.cancel_scheduled_price(ctx, price_id=price_id)
    return HttpResponse(status=204)


@staff_view(["GET", "POST"])
def price_ranges_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """GET: every partner price range. POST: set one (create or change)."""
    if request.method == "GET":
        authz.require(ctx, store_admin.CATALOG_VIEW)
        return JsonResponse({"results": price_ranges.ranges()})
    body = _body(request)
    row = price_ranges.set_range(
        ctx,
        experience=body.get("experience"),
        plan_id=body.get("plan_id"),
        interval=body.get("interval"),
        currency=body.get("currency"),
        min_minor=body.get("min_minor"),
        max_minor=body.get("max_minor"),
        partner_may_set=body.get("partner_may_set", True),
    )
    return JsonResponse(row, status=201)


@staff_view(["DELETE"])
def price_range_view(
    request: HttpRequest, ctx: TenantContext, range_id: UUID
) -> HttpResponse:
    price_ranges.remove_range(ctx, range_id=range_id)
    return HttpResponse(status=204)
