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
from workeasy360.commercial.catalog.domain import MAX_VIDEO_BYTES
from workeasy360.commercial.controlplane import product_manager, product_publisher
from workeasy360.contracts.enums import ProductMediaKind
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1 import video_http
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


@staff_view(["GET", "POST"])
def publisher_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """ADR-068, the one-page Product Publisher: what an empty page needs (GET),
    or a new product with its plans, in one transaction (POST)."""
    if request.method == "POST":
        return JsonResponse(product_publisher.save(ctx, None, _body(request)), status=201)
    return JsonResponse(product_publisher.bootstrap(ctx))


@staff_view(["GET", "PUT"])
def publisher_product_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    """One product as the Publisher edits it (GET), or saved from it (PUT)."""
    if request.method == "PUT":
        return JsonResponse(product_publisher.save(ctx, product_id, _body(request)))
    return JsonResponse(product_publisher.state(ctx, product_id))


@staff_view(["POST"])
def publisher_publish_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    """Publish to the Store: the visible, priced plans and the product, through
    the catalog's publish checklist, in one transaction."""
    return JsonResponse(product_publisher.publish(ctx, product_id))


@staff_view(["GET"])
def store_preview_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    """The Store product page as it will look once published - a draft too -
    for staff only; nothing is made public."""
    return JsonResponse(product_publisher.preview(ctx, product_id))


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
            "kind is logo, banner, screenshot or video.", field="kind"
        ) from None
    if kind is ProductMediaKind.VIDEO:
        # Spooled to disk before any transaction opens (video_http).
        spool, size = video_http.spool_body(request, max_bytes=MAX_VIDEO_BYTES)
        with spool, acting_scope(ctx):
            media = catalog_services.upload_product_video(
                ctx,
                product_id=product_id,
                content=spool,
                size_bytes=size,
                content_type=request.headers.get("Content-Type", ""),
            )
        return JsonResponse(_media_json(media), status=201)
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
    video = product_manager.video_file_id(ctx, product_id, media_id)
    if video is not None:
        from workeasy360.platform.files import selectors as file_selectors

        # The stream opens inside the scope; sending it needs no database.
        # A streamed (ranged) response; ``staff_view`` is typed for HttpResponse.
        with acting_scope(ctx):
            return video_http.ranged_response(  # type: ignore[return-value]
                request,
                facts=file_selectors.servable_size(video),
                open_range=lambda start, length: file_selectors.open_servable_range(
                    video, start=start, length=length
                ),
                cache_control="private, max-age=300",
            )
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


# --- ADR-054: product updates (features and releases) --------------------------


@staff_view(["GET", "POST"])
def updates_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID
) -> JsonResponse:
    """A product's updates; or write a draft one."""
    from workeasy360.commercial.catalog import updates

    if request.method == "POST":
        row = updates.create_update(ctx, product_id=product_id, body=_body(request))
        return JsonResponse({"id": str(row.id), "status": row.status}, status=201)
    return JsonResponse(updates.list_updates(ctx, product_id=product_id))


@staff_view(["PATCH"])
def update_detail_view(
    request: HttpRequest, ctx: TenantContext, product_id: UUID, update_id: UUID
) -> JsonResponse:
    from workeasy360.commercial.catalog import updates

    updates.require_update_of(product_id, update_id)
    row = updates.edit_update(ctx, update_id=update_id, body=_body(request))
    return JsonResponse({"id": str(row.id), "status": row.status})


@staff_view(["POST"])
def update_lifecycle_view(
    request: HttpRequest,
    ctx: TenantContext,
    product_id: UUID,
    update_id: UUID,
    action: str,
) -> JsonResponse:
    """Publish or archive an update."""
    from workeasy360.commercial.catalog import updates

    service = {"publish": updates.publish_update, "archive": updates.archive_update}.get(
        action
    )
    if service is None:
        return JsonResponse(
            {"code": "not_found", "detail": "No such action."}, status=404
        )
    updates.require_update_of(product_id, update_id)
    row = service(ctx, update_id=update_id)
    return JsonResponse({"id": str(row.id), "status": row.status})


# --- the Super Admin's product categories (2026-11-16) ----------------------


@staff_view(["GET", "POST"])
def categories_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """The category list with each one's product count (GET), or add one (POST)."""
    from workeasy360.commercial.catalog import categories

    if request.method == "POST":
        row = categories.create_category(ctx, name=_body(request).get("name"))
        return JsonResponse(row, status=201)
    return JsonResponse(categories.list_categories(ctx))


@staff_view(["PATCH", "DELETE"])
def category_view(
    request: HttpRequest, ctx: TenantContext, category_id: UUID
) -> JsonResponse:
    """Rename a category and its products (PATCH), or delete it (DELETE),
    moving its products to ``?move_to=<another category's id>``."""
    from workeasy360.commercial.catalog import categories

    if request.method == "DELETE":
        return JsonResponse(
            categories.delete_category(
                ctx, category_id=category_id, move_to=request.GET.get("move_to")
            )
        )
    return JsonResponse(
        categories.rename_category(
            ctx, category_id=category_id, name=_body(request).get("name")
        )
    )


@staff_view(["POST"])
def categories_order_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Put the list in the order given (every category's id, once)."""
    from workeasy360.commercial.catalog import categories

    return JsonResponse(
        categories.reorder_categories(ctx, order=_body(request).get("order"))
    )
