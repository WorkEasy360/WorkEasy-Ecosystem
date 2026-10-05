"""Commercial HTTP adapters.

Thin by rule (F-7, G4): parse input, call **one** application service, shape
output. Every authorization check, every state transition and every
transaction boundary lives in the service, so the task path, the admin and a
future Super Admin UI get identical behaviour without re-implementing any of
it.

**No serializer framework and no ``fields = "__all__"``.** Each handler names
the fields it accepts, so a client cannot set ``status``, ``price_id`` or
``organization`` by adding them to a request body. Mass assignment is the
defect a generic serializer makes easy and an explicit read makes
impossible.

**Every route sits under the tenant-scoped path shape**
``/api/v1/orgs/{org_slug}/...``, including the internal catalog ones. The
organization comes from the path and nowhere else (TB-1/TB-2), and the
internal endpoints resolve against the acting staff member's *own*
organization. What makes them internal is ``require_platform_staff`` inside
the service - never the URL, which is client-supplied.

The Super Admin frontend is Phase 7. These endpoints exist now so that when
it arrives it has services to call rather than business logic to embed.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.billing import services as billing
from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.pricing import services as pricing_services
from workeasy360.commercial.subscriptions import selectors as subscription_selectors
from workeasy360.commercial.subscriptions import services as subscription_services
from workeasy360.contracts.enums import (
    BillingInterval,
    BundleKind,
    CatalogStatus,
    ChangeTiming,
    FeatureKind,
    LicensingMode,
    PlanBillingType,
    PlanKind,
    PriceBookChannel,
    PriceModel,
    ProductListing,
    ProductStatus,
)
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values import json_input
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.authz import services as authz


def _body(request: HttpRequest) -> dict[str, Any]:
    """Parse a JSON body, refusing anything that is not an object.

    A list or a bare string reaching a handler that expects a mapping
    produces an obscure ``AttributeError`` several frames deep; refusing here
    gives the client the real reason.
    """
    if not request.body:
        return {}
    try:
        payload = json_input.loads(request.body)
    except json.JSONDecodeError as exc:
        raise CatalogValidationError(f"Malformed JSON body: {exc}", field="body") from exc
    if not isinstance(payload, dict):
        raise CatalogValidationError("Request body must be a JSON object.", field="body")
    return payload


def _required(body: dict[str, Any], field: str) -> Any:
    if field not in body or body[field] in (None, ""):
        raise CatalogValidationError(f"{field} is required.", field=field)
    return body[field]


def _uuid(body: dict[str, Any], field: str, *, required: bool = True) -> UUID | None:
    raw = body.get(field)
    if raw in (None, ""):
        if required:
            raise CatalogValidationError(f"{field} is required.", field=field)
        return None
    try:
        return UUID(str(raw))
    except ValueError as exc:
        raise CatalogValidationError(f"{field} is not a valid id.", field=field) from exc


def _enum(body: dict[str, Any], field: str, enum_cls: Any, default: Any = None) -> Any:
    raw = body.get(field)
    if raw in (None, ""):
        if default is None:
            raise CatalogValidationError(f"{field} is required.", field=field)
        return default
    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = sorted(member.value for member in enum_cls)
        raise CatalogValidationError(
            f"{field} must be one of {allowed}.", field=field
        ) from exc


def _int(body: dict[str, Any], field: str, default: int | None = None) -> int | None:
    raw = body.get(field, default)
    if raw is None:
        return None
    # bool is a subclass of int; a JSON ``true`` becoming a quantity of one is
    # the kind of coercion that only shows up on an invoice.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise CatalogValidationError(f"{field} must be an integer.", field=field)
    return raw


def _flag(body: dict[str, Any], field: str, default: bool = False) -> bool:
    """An optional JSON boolean. ``"false"`` is a non-empty string, so ``bool()``
    would read it as true: refusing it is what stops a stray string enabling a
    trial or making a cancellation immediate."""
    raw = body.get(field, default)
    if not isinstance(raw, bool):
        raise CatalogValidationError(f"{field} must be true or false.", field=field)
    return raw


def _idempotency_key(request: HttpRequest, body: dict[str, Any]) -> str:
    """The caller's command id.

    Required rather than generated: a key the server invents is unique per
    attempt, which makes every retry a new command and defeats the point.
    """
    key = request.headers.get("Idempotency-Key") or body.get("idempotency_key")
    if not key:
        raise CatalogValidationError(
            "An Idempotency-Key header is required. Commercial commands may be "
            "retried, and a command without a key would buy twice.",
            field="idempotency_key",
        )
    return str(key)[:128]


# --- catalog administration (internal) ------------------------------------


@require_http_methods(["GET", "POST"])
def products_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]

    if request.method == "GET":
        authz.require(ctx, "commercial.catalog.view")
        return JsonResponse({"results": catalog_selectors.visible_products()})

    body = _body(request)
    product = catalog_services.create_product(
        ctx,
        code=_required(body, "code"),
        name=_required(body, "name"),
        description=body.get("description", ""),
        licensing_mode=_enum(
            body, "licensing_mode", LicensingMode, LicensingMode.PER_SEAT
        ),
        sort_order=_int(body, "sort_order", 100) or 100,
        slug=str(body.get("slug") or "") or None,
        tagline=str(body.get("tagline") or ""),
        marketing_url=str(body.get("marketing_url") or ""),
        listing=_enum(body, "listing", ProductListing, ProductListing.LISTED),
    )
    return JsonResponse(
        {
            "id": str(product.id),
            "code": product.code,
            "status": product.status,
            "slug": product.slug,
        },
        status=201,
    )


@require_http_methods(["POST"])
def product_status_view(
    request: HttpRequest, org_slug: str, product_id: UUID
) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    product = catalog_services.set_product_status(
        ctx,
        product_id=product_id,
        status=_enum(body, "status", ProductStatus),
    )
    return JsonResponse({"id": str(product.id), "status": product.status})


@require_http_methods(["POST"])
def features_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    feature = catalog_services.create_feature(
        ctx,
        product_id=_uuid(body, "product_id"),
        key=_required(body, "key"),
        name=_required(body, "name"),
        kind=_enum(body, "kind", FeatureKind),
        unit=body.get("unit", ""),
        default_value=body.get("default_value", False),
        enum_ordering=body.get("enum_ordering"),
        is_public=_flag(body, "is_public", True),
    )
    return JsonResponse({"id": str(feature.id), "key": feature.key}, status=201)


@require_http_methods(["POST"])
def plans_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    plan = catalog_services.create_plan(
        ctx,
        product_id=_uuid(body, "product_id"),
        code=_required(body, "code"),
        name=_required(body, "name"),
        description=body.get("description", ""),
        is_public=_flag(body, "is_public", True),
        caption=body.get("caption", ""),
        billing_type=_enum(body, "billing_type", PlanBillingType, PlanBillingType.PAID),
        kind=_enum(body, "kind", PlanKind, PlanKind.STANDARD),
        addon_for_product_id=_uuid(body, "addon_for_product_id", required=False),
    )
    return JsonResponse({"id": str(plan.id), "code": plan.code}, status=201)


@require_http_methods(["POST"])
def plan_versions_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    """Draft a version, and publish it when asked in the same call.

    Two services, one endpoint, because "draft then publish" is one
    administrative act and splitting it across two round trips leaves a draft
    behind whenever the second fails.
    """
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    features = body.get("features") or {}
    if not isinstance(features, dict):
        raise CatalogValidationError("features must be an object.", field="features")

    version = catalog_services.draft_plan_version(
        ctx,
        plan_id=_uuid(body, "plan_id"),
        features=features,
        included_seats=_int(body, "included_seats", 1) or 1,
        max_seats=_int(body, "max_seats"),
        trial_days=_int(body, "trial_days", 0) or 0,
    )
    if body.get("publish"):
        version = catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
    return JsonResponse(
        {
            "id": str(version.id),
            "version": version.version,
            "published": version.is_published,
        },
        status=201,
    )


@require_http_methods(["POST"])
def plan_status_view(request: HttpRequest, org_slug: str, plan_id: UUID) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    plan = catalog_services.set_plan_status(
        ctx, plan_id=plan_id, status=_enum(body, "status", CatalogStatus)
    )
    return JsonResponse({"id": str(plan.id), "status": plan.status})


@require_http_methods(["POST"])
def bundles_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    bundle = catalog_services.create_bundle(
        ctx,
        code=_required(body, "code"),
        name=_required(body, "name"),
        kind=_enum(body, "kind", BundleKind, BundleKind.PREDEFINED),
        description=body.get("description", ""),
        is_public=_flag(body, "is_public", True),
        caption=body.get("caption", ""),
        billing_type=_enum(body, "billing_type", PlanBillingType, PlanBillingType.PAID),
    )
    return JsonResponse({"id": str(bundle.id), "code": bundle.code}, status=201)


@require_http_methods(["POST"])
def bundle_versions_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise CatalogValidationError("items must be a non-empty list.", field="items")

    version = catalog_services.draft_bundle_version(
        ctx,
        bundle_id=_uuid(body, "bundle_id"),
        items=items,
        trial_days=_int(body, "trial_days", 0) or 0,
    )
    if body.get("publish"):
        version = catalog_services.publish_bundle_version(
            ctx, bundle_version_id=version.id
        )
    return JsonResponse(
        {
            "id": str(version.id),
            "version": version.version,
            "published": version.is_published,
        },
        status=201,
    )


# --- pricing administration (internal) ------------------------------------


@require_http_methods(["POST"])
def price_books_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    book = pricing_services.create_price_book(
        ctx,
        code=_required(body, "code"),
        name=_required(body, "name"),
        currency=_enum(body, "currency", CurrencyCode),
        channel=_enum(body, "channel", PriceBookChannel, PriceBookChannel.DIRECT),
        region=body.get("region", ""),
        priority=_int(body, "priority", 100) or 100,
    )
    return JsonResponse({"id": str(book.id), "code": book.code}, status=201)


@require_http_methods(["POST"])
def prices_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    price = pricing_services.publish_price(
        ctx,
        price_book_id=_uuid(body, "price_book_id"),
        plan_version_id=_uuid(body, "plan_version_id", required=False),
        bundle_version_id=_uuid(body, "bundle_version_id", required=False),
        interval=_enum(body, "interval", BillingInterval),
        price_model=_enum(body, "price_model", PriceModel, PriceModel.FLAT),
        amount_minor=_int(body, "amount_minor"),
        tiers=body.get("tiers"),
        min_quantity=_int(body, "min_quantity", 1) or 1,
        max_quantity=_int(body, "max_quantity"),
    )
    return JsonResponse(
        {"id": str(price.id), "amount_minor": price.amount_minor}, status=201
    )


# --- customer-facing subscription surface ---------------------------------


@require_http_methods(["GET", "POST"])
def billing_account_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        # Phase 8 extension: the console's billing page reads it.
        authz.require(ctx, "platform.billing.view")
        existing = billing.billing_account_or_none(ctx.organization_id)
        if existing is None:
            return JsonResponse({"billing_account": None})
        return JsonResponse(
            {
                "billing_account": {
                    "id": str(existing.id),
                    "currency": existing.currency,
                    "collection_mode": existing.collection_mode,
                    "billing_email": existing.billing_email,
                    "billing_region": existing.billing_region,
                }
            }
        )
    body = _body(request)
    # Phase 9: collection mode and region are never read from the customer -
    # they decide whether and at what price the organization is invoiced.
    account = billing.open_billing_account(
        ctx,
        currency=_enum(body, "currency", CurrencyCode),
        billing_email=body.get("billing_email", ""),
        tax_reference=body.get("tax_reference", ""),
    )
    return JsonResponse(
        {
            "id": str(account.id),
            "currency": account.currency,
            "collection_mode": account.collection_mode,
        }
    )


@require_http_methods(["GET"])
def subscriptions_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    """The organization's subscription, as the customer and support see it."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    authz.require(ctx, "commercial.subscription.view")

    subscriptions = subscription_selectors.subscriptions_for_organization()
    names = _plan_names(subscriptions)
    return JsonResponse(
        {"results": [_subscription_payload(dto, names) for dto in subscriptions]}
    )


@require_http_methods(["GET"])
def subscription_detail_view(
    request: HttpRequest, org_slug: str, subscription_id: UUID
) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    authz.require(ctx, "commercial.subscription.view")

    dto = subscription_selectors.subscription_detail(subscription_id)
    if dto is None:
        # 404 whether it does not exist or belongs to another tenant.
        # Distinguishing the two is an enumeration oracle.
        return JsonResponse(
            {"code": "commercial_record_not_found", "detail": "No such subscription."},
            status=404,
        )
    return JsonResponse(_subscription_payload(dto, _plan_names([dto])))


def _plan_names(subscriptions: Any) -> dict[Any, str]:
    """Plan names for every line of these subscriptions - one catalog read
    (Phase 9: My Products shows "Plan: CRM Professional", not a code)."""
    ids = [
        item.plan_version_id
        for dto in subscriptions
        for item in dto.items
        if item.plan_version_id is not None
    ]
    return {
        pid: plan.plan_name for pid, plan in catalog_selectors.plan_versions(ids).items()
    }


def _subscription_payload(
    dto: Any, names: dict[Any, str] | None = None
) -> dict[str, Any]:
    """The wire shape.

    Deliberately narrow. ``price_id`` and the internal plan version ids are
    omitted: a customer-facing payload that carries the id of a pricing row
    invites a client to send one back.
    """
    return {
        "id": str(dto.id),
        "status": dto.status.value,
        "currency": dto.currency,
        "billing_interval": dto.billing_interval.value,
        "renewal_policy": dto.renewal_policy.value,
        "current_period_start": dto.current_period_start.isoformat(),
        "current_period_end": dto.current_period_end.isoformat(),
        "trial_ends_at": dto.trial_ends_at.isoformat() if dto.trial_ends_at else None,
        "cancel_at": dto.cancel_at.isoformat() if dto.cancel_at else None,
        "version": dto.version,
        "items": [
            {
                "id": str(item.id),
                "product_code": item.product_code,
                "plan_code": item.plan_code,
                "plan_name": (names or {}).get(item.plan_version_id, ""),
                "bundle_code": item.bundle_code,
                "quantity": item.quantity,
                "added_at": item.added_at.isoformat(),
                "removed_at": item.removed_at.isoformat() if item.removed_at else None,
            }
            for item in dto.items
        ],
    }


@require_http_methods(["POST"])
def change_plan_view(
    request: HttpRequest, org_slug: str, subscription_item_id: UUID
) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    item = subscription_services.change_plan(
        ctx,
        subscription_item_id=subscription_item_id,
        to_plan_version_id=_uuid(body, "to_plan_version_id"),
        idempotency_key=_idempotency_key(request, body),
        timing=_enum(body, "timing", ChangeTiming, "") or None,
        expected_version=_int(body, "expected_version"),
    )
    return JsonResponse(
        {
            "subscription_item_id": str(item.id),
            "plan_code": item.plan_code,
            "effective_at": item.added_at.isoformat(),
        },
        status=201,
    )


@require_http_methods(["POST"])
def cancel_view(
    request: HttpRequest, org_slug: str, subscription_id: UUID
) -> HttpResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)
    subscription = subscription_services.cancel_subscription(
        ctx,
        subscription_id=subscription_id,
        idempotency_key=_idempotency_key(request, body),
        immediate=_flag(body, "immediate"),
        reason_code=str(body.get("reason_code", "customer_request"))[:64],
        expected_version=_int(body, "expected_version"),
    )
    return JsonResponse(
        {
            "id": str(subscription.id),
            "status": subscription.status,
            "cancel_at": subscription.cancel_at.isoformat()
            if subscription.cancel_at
            else None,
        }
    )


@require_http_methods(["GET"])
def marketplace_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    """Phase 8 extension: what this organization can buy, with list prices in
    its billing currency (``commercial.subscriptions.marketplace``)."""
    from workeasy360.commercial.subscriptions.marketplace import offers

    ctx = request.tenant_context  # type: ignore[attr-defined]
    return JsonResponse(offers(ctx))
