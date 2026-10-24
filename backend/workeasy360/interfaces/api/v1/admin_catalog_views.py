"""The Super Admin plan builder API (Phase 8 extension): ``/api/v1/admin/catalog/...``.

The builder *reads* through ``commercial.controlplane.catalog`` and *writes*
through the same catalog and pricing services every other caller uses - the
existing ``admin/catalog/*`` POST routes create plans, versions, bundles and
prices; this module adds what the builder was missing: a combined listing,
presentation edits, bundle status, and publishing a version drafted earlier.

Every write authorizes in the service (``commercial.catalog.manage`` held via a
``platform.staff_catalog.manage`` grant, audited).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse

from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.controlplane import catalog as cp_catalog
from workeasy360.contracts.enums import CatalogStatus, PlanBillingType
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.control_plane_http import staff_view
from workeasy360.platform.authz.control_plane import acting_scope

_body = legacy._body
_enum = legacy._enum


def _plan_json(plan: Any) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "code": plan.code,
        "name": plan.name,
        "caption": plan.caption,
        "description": plan.description,
        "status": plan.status,
        "is_public": plan.is_public,
        "sort_order": plan.sort_order,
        "billing_type": plan.billing_type,
        "kind": plan.kind,
    }


def _bundle_json(bundle: Any) -> dict[str, Any]:
    return {
        "id": str(bundle.id),
        "code": bundle.code,
        "name": bundle.name,
        "caption": bundle.caption,
        "description": bundle.description,
        "status": bundle.status,
        "is_public": bundle.is_public,
        "billing_type": bundle.billing_type,
    }


def _optional_billing_type(body: dict[str, Any]) -> PlanBillingType | None:
    if body.get("billing_type") in (None, ""):
        return None
    return _enum(body, "billing_type", PlanBillingType)


@staff_view(["GET"])
def overview_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        cp_catalog.overview(ctx, product_code=request.GET.get("product") or None)
    )


@staff_view(["GET"])
def product_features_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    return JsonResponse({"results": cp_catalog.product_features(ctx, product_id)})


@staff_view(["PATCH"])
def product_detail_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    """Edit a product's presentation and listing (Phase 9)."""
    from workeasy360.commercial.catalog.selectors import product_row

    with acting_scope(ctx):
        product = catalog_services.update_product(
            ctx, product_id=product_id, changes=_body(request)
        )
    row = product_row(product)
    row["id"] = str(row["id"])
    return JsonResponse(row)


@staff_view(["PUT"])
def product_trial_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    """Set how one product may be trialled (Phase 11). The body is the whole
    configuration; every value is validated by the service."""
    body = _body(request)
    with acting_scope(ctx):
        config = catalog_services.configure_product_trial(
            ctx,
            product_id=product_id,
            enabled=body.get("enabled"),
            duration_days=body.get("duration_days"),
            seat_limit=body.get("seat_limit"),
            payment_method_required=body.get("payment_method_required", False),
            eligible_plan_codes=body.get("eligible_plan_codes", []),
        )
    return JsonResponse(
        {
            "enabled": config.enabled,
            "duration_days": config.duration_days,
            "seat_limit": config.seat_limit,
            "payment_method_required": config.payment_method_required,
            "eligible_plan_codes": list(config.eligible_plan_codes),
        }
    )


@staff_view(["PATCH"])
def plan_detail_view(
    request: HttpRequest, ctx: TenantContext, plan_id: UUID
) -> JsonResponse:
    body = _body(request)
    with acting_scope(ctx):
        plan = catalog_services.update_plan(
            ctx,
            plan_id=plan_id,
            name=body.get("name"),
            caption=body.get("caption"),
            description=body.get("description"),
            is_public=body.get("is_public"),
            sort_order=body.get("sort_order"),
            billing_type=_optional_billing_type(body),
        )
    return JsonResponse(_plan_json(plan))


@staff_view(["PATCH"])
def bundle_detail_view(
    request: HttpRequest, ctx: TenantContext, bundle_id: UUID
) -> JsonResponse:
    body = _body(request)
    with acting_scope(ctx):
        bundle = catalog_services.update_bundle(
            ctx,
            bundle_id=bundle_id,
            name=body.get("name"),
            caption=body.get("caption"),
            description=body.get("description"),
            is_public=body.get("is_public"),
            billing_type=_optional_billing_type(body),
        )
    return JsonResponse(_bundle_json(bundle))


@staff_view(["POST"])
def bundle_status_view(
    request: HttpRequest, ctx: TenantContext, bundle_id: UUID
) -> JsonResponse:
    body = _body(request)
    with acting_scope(ctx):
        bundle = catalog_services.set_bundle_status(
            ctx, bundle_id=bundle_id, status=_enum(body, "status", CatalogStatus)
        )
    return JsonResponse({"id": str(bundle.id), "status": bundle.status})


@staff_view(["POST"])
def plan_version_publish_view(
    request: HttpRequest, ctx: TenantContext, plan_version_id: UUID
) -> JsonResponse:
    with acting_scope(ctx):
        version = catalog_services.publish_plan_version(
            ctx, plan_version_id=plan_version_id
        )
    return JsonResponse(
        {"id": str(version.id), "version": version.version, "published": True}
    )


@staff_view(["POST"])
def bundle_version_publish_view(
    request: HttpRequest, ctx: TenantContext, bundle_version_id: UUID
) -> JsonResponse:
    with acting_scope(ctx):
        version = catalog_services.publish_bundle_version(
            ctx, bundle_version_id=bundle_version_id
        )
    return JsonResponse(
        {"id": str(version.id), "version": version.version, "published": True}
    )
