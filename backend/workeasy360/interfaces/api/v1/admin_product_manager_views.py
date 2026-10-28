"""The Super Admin Product Manager over HTTP (ADR-051): ``/api/v1/admin/products``.

Thin, like every view (F-7): parse, call one service, shape the response.
Authorization, validation and audit live in the services -
``controlplane.product_manager`` and, beneath it, the catalog's own services.
Editing a product's fields is the existing ``PATCH admin/catalog/products/<id>``
and its trial the existing ``PUT .../trial``; its integration the existing
``admin/products/<code>/integration`` routes. There is no second write path.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.controlplane import product_manager
from workeasy360.contracts.enums import ProductMediaKind
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    page_response,
    staff_view,
)
from workeasy360.platform.authz.control_plane import acting_scope

_body = legacy._body

#: Lifecycle actions, each one catalog service.
_LIFECYCLE = {
    "publish": catalog_services.publish_product,
    "unpublish": catalog_services.unpublish_product,
    "archive": catalog_services.archive_product,
    "restore": catalog_services.restore_product,
}


def _media_json(media: Any) -> dict[str, Any]:
    return {
        "id": str(media.id),
        "kind": media.kind,
        "width": media.width,
        "height": media.height,
        "content_type": media.content_type,
        "size_bytes": media.size_bytes,
        "caption": media.caption,
        "sort_order": media.sort_order,
    }


@staff_view(["POST"])
def create_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """The Add Product wizard: a draft, hidden product with its integration
    and trial, in one transaction."""
    return JsonResponse(product_manager.create(ctx, _body(request)), status=201)


@staff_view(["GET", "DELETE"])
def detail_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    if request.method == "DELETE":
        return JsonResponse(product_manager.delete(ctx, product_id))
    return JsonResponse(product_manager.detail(ctx, product_id))


@staff_view(["POST"])
def lifecycle_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID, action: str
) -> JsonResponse:
    """Publish, unpublish, archive or restore."""
    service = _LIFECYCLE.get(action)
    if service is None:
        return JsonResponse(
            {"code": "not_found", "detail": "No such action."}, status=404
        )
    with acting_scope(ctx):
        service(ctx, product_id=product_id)
    return JsonResponse(product_manager.detail(ctx, product_id))


@staff_view(["GET"])
def activity_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    return page_response(
        product_manager.activity(
            ctx, product_id, cursor=request.GET.get("cursor") or None
        )
    )


@staff_view(["POST"])
def media_upload_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    """One image as the raw request body (as partner logos are sent): the type
    comes from ``Content-Type`` and is checked against the bytes; ``kind`` and
    an optional ``caption`` are query parameters."""
    blocked = limited(request, "product_media_upload", user=request.user)
    if blocked is not None:
        return blocked
    try:
        kind = ProductMediaKind(request.GET.get("kind", ""))
    except ValueError:
        raise CatalogValidationError(
            "kind is logo, banner or screenshot.", field="kind"
        ) from None
    with acting_scope(ctx):
        media = catalog_services.upload_product_media(
            ctx,
            product_id=product_id,
            kind=kind,
            content=request.body,
            content_type=request.headers.get("Content-Type", ""),
            caption=request.GET.get("caption", ""),
        )
    return JsonResponse(_media_json(media), status=201)


@staff_view(["GET", "PATCH", "DELETE"])
def media_item_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID, media_id: UUID
) -> HttpResponse:
    """``GET`` the image itself (same-origin, for the preview), ``PATCH`` its
    caption, ``DELETE`` it."""
    if request.method == "DELETE":
        with acting_scope(ctx):
            catalog_services.delete_product_media(
                ctx, product_id=product_id, media_id=media_id
            )
        return JsonResponse({"deleted": True})
    if request.method == "PATCH":
        with acting_scope(ctx):
            media = catalog_services.update_media_caption(
                ctx,
                product_id=product_id,
                media_id=media_id,
                caption=_body(request).get("caption", ""),
            )
        return JsonResponse(_media_json(media))
    found = product_manager.media_content(ctx, product_id, media_id)
    if found is None:
        return HttpResponse(status=404)
    data, content_type = found
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Content-Disposition"] = "inline"
    return response


@staff_view(["POST"])
def media_reorder_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    with acting_scope(ctx):
        catalog_services.reorder_screenshots(
            ctx, product_id=product_id, order=_body(request).get("order")
        )
    return JsonResponse(
        {"media": catalog_selectors.media_rows([product_id])[str(product_id)]}
    )
