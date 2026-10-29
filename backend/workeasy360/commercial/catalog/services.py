"""Catalog application services - the only sanctioned way to write the catalog.

Every function here is a use case, owns its transaction, enforces
authorization and audits. Views and the admin call these; nothing calls the
models directly.

Authorization is two-factor for every mutation:

    authz.require(ctx, "commercial.catalog.manage")      # the permission
    authz.require_platform_staff(ctx, operation=..., permission=...)
                                                         # held via a StaffGrant

Either alone is one misconfiguration away from letting a customer edit the
global catalog, which would change what *every* tenant is sold. The second
reads the actor's live control-plane grants, so a customer owner's role -
whose ``*`` carries ``commercial.catalog.manage`` - never suffices, and
Django's ``is_staff`` flag confers nothing on its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.catalog.domain import (
    MAX_MEDIA_CAPTION,
    MAX_SCREENSHOTS,
    MAX_STORE_SUMMARY,
    MAX_STORE_TITLE,
    assert_catalog_transition,
    assert_product_transition,
    slug_for_code,
    validate_category,
    validate_feature_key,
    validate_feature_value,
    validate_icon,
    validate_link_url,
    validate_marketing_url,
    validate_media,
    validate_product_code,
    validate_slug,
    validate_store_text,
    validate_tagline,
    validate_trial_terms,
)
from workeasy360.contracts.enums import (
    AuditOutcome,
    BundleKind,
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    PlanBillingType,
    PlanKind,
    ProductListing,
    ProductMediaKind,
    ProductStatus,
)
from workeasy360.contracts.errors import (
    CatalogValidationError,
    CommercialRecordNotFound,
    ImmutableRecord,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

MANAGE = "commercial.catalog.manage"


def _authorise(ctx: TenantContext, operation: str) -> None:
    authz.require(ctx, MANAGE)
    authz.require_platform_staff(ctx, operation=operation, permission=MANAGE)


def _refresh_public_catalog() -> None:
    """Once this transaction commits, move the public catalog's cache to a new
    generation (ADR-052): a product just published shows at once, one just
    hidden disappears at once, rather than up to a cache lifetime later. A
    cache outage only means the old entry expires on its own."""
    from django.core.cache import cache

    from workeasy360.commercial.catalog.selectors import PUBLIC_CATALOG_GENERATION_KEY

    def bump() -> None:
        try:
            try:
                cache.incr(PUBLIC_CATALOG_GENERATION_KEY)
            except ValueError:  # the key does not exist yet
                cache.set(PUBLIC_CATALOG_GENERATION_KEY, 1, None)
        except Exception as exc:
            logger.warning(
                "catalog.public.generation_unavailable", error_type=type(exc).__name__
            )

    transaction.on_commit(bump)


def _one(queryset: Any, **lookup: Any) -> Any:
    """The single row ``lookup`` names, or a 404.

    Every id reaching these services comes from a request body or path, so a
    miss is the caller's mistake - a ``CommercialRecordNotFound`` (404) - and
    never an unhandled ``DoesNotExist`` that surfaces as a 500 (Phase 7 final
    audit: the admin catalog routes reached these with arbitrary ids).
    """
    row = queryset.filter(**lookup).first()
    if row is None:
        raise CommercialRecordNotFound(
            f"No such {queryset.model._meta.verbose_name}.",
            **{key: str(value) for key, value in lookup.items()},
        )
    return row


# --- products -------------------------------------------------------------


def create_product(
    ctx: TenantContext,
    *,
    code: str,
    name: str,
    description: str = "",
    licensing_mode: LicensingMode = LicensingMode.PER_SEAT,
    sort_order: int = 100,
    slug: str | None = None,
    tagline: str = "",
    marketing_url: str = "",
    listing: ProductListing = ProductListing.LISTED,
    category: str = "",
    icon: str = "",
    listing_details: dict[str, Any] | None = None,
) -> Any:
    """Register a sellable SKU. Starts in ``draft`` - never sellable on
    creation, because a product with no plan and no price is not a product.

    ``listing_details`` (ADR-051) carries the Product Manager's store-listing
    and availability fields (``LISTING_DETAIL_FIELDS``), validated exactly as
    an edit validates them."""
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.create")
    validated = validate_product_code(code)
    clean_slug = validate_slug(slug) if slug else slug_for_code(str(validated))
    clean_tagline = validate_tagline(tagline)
    clean_url = validate_marketing_url(marketing_url)
    clean_category = validate_category(category)
    clean_icon = validate_icon(icon)
    details = listing_details or {}
    unknown = sorted(set(details) - set(LISTING_DETAIL_FIELDS))
    if unknown:
        raise CatalogValidationError(
            f"These product fields cannot be set here: {', '.join(unknown)}.",
            field=unknown[0],
        )
    clean_details = _clean_product_changes(details)

    with transaction.atomic():
        if Product.objects.filter(code=str(validated)).exists():
            raise CatalogValidationError(
                f"Product code {code!r} is already in use. Codes are stable "
                f"machine identifiers and are never reused.",
                field="code",
            )
        if Product.objects.filter(slug=clean_slug).exists():
            raise CatalogValidationError(
                f"The slug {clean_slug!r} is already in use.", field="slug"
            )
        product = Product.objects.create(
            code=str(validated),
            name=name,
            description=description,
            licensing_mode=licensing_mode.value,
            sort_order=sort_order,
            slug=clean_slug,
            tagline=clean_tagline,
            marketing_url=clean_url,
            listing=listing.value,
            category=clean_category,
            icon=clean_icon,
            **clean_details,
        )
        audit.record(
            ctx,
            "commercial.product.created",
            resource_type="catalog.product",
            resource_id=product.id,
            changes={
                "code": product.code,
                "licensing_mode": product.licensing_mode,
                "slug": product.slug,
                "listing": product.listing,
                **{k: v for k, v in clean_details.items() if k in _CHANNEL_FLAGS},
            },
        )
    return product


#: The product fields ``update_product`` may change. ``code`` is absent on
#: purpose: it is the stable identity entitlements, integrations and every
#: historical subscription name the product by. ``licensing_mode`` is absent
#: too - changing it would change how seats are counted for every customer.
#: ADR-051: the Product Manager's store listing and where the product may be
#: offered. Settable at creation as well as by an edit.
LISTING_DETAIL_FIELDS = (
    "store_title",
    "store_summary",
    "application_url",
    "documentation_url",
    "support_url",
    "customer_store_enabled",
    "reseller_enabled",
    "affiliate_enabled",
    "white_label_enabled",
)
_CHANNEL_FLAGS = frozenset(
    {
        "customer_store_enabled",
        "reseller_enabled",
        "affiliate_enabled",
        "white_label_enabled",
    }
)
_LINK_FIELDS = ("application_url", "documentation_url", "support_url")

EDITABLE_PRODUCT_FIELDS = (
    "name",
    "description",
    "tagline",
    "slug",
    "marketing_url",
    "listing",
    "sort_order",
    "category",
    "icon",
    *LISTING_DETAIL_FIELDS,
)


def _clean_product_changes(changes: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(changes) - set(EDITABLE_PRODUCT_FIELDS))
    if unknown:
        raise CatalogValidationError(
            f"These product fields cannot be changed: {', '.join(unknown)}.",
            field=unknown[0],
        )
    clean: dict[str, Any] = {}
    if "name" in changes:
        name = " ".join(str(changes["name"] or "").split())
        if not name or len(name) > 128:
            raise CatalogValidationError("The name is 1 to 128 characters.", field="name")
        clean["name"] = name
    if "description" in changes:
        description = str(changes["description"] or "")
        if len(description) > 4000:
            raise CatalogValidationError(
                "The description is at most 4,000 characters.", field="description"
            )
        clean["description"] = description
    if "tagline" in changes:
        clean["tagline"] = validate_tagline(changes["tagline"])
    if "slug" in changes:
        clean["slug"] = validate_slug(changes["slug"])
    if "marketing_url" in changes:
        clean["marketing_url"] = validate_marketing_url(changes["marketing_url"])
    if "category" in changes:
        clean["category"] = validate_category(changes["category"])
    if "icon" in changes:
        clean["icon"] = validate_icon(changes["icon"])
    if "listing" in changes:
        try:
            clean["listing"] = ProductListing(str(changes["listing"])).value
        except ValueError as exc:
            raise CatalogValidationError(
                "listing is listed, coming_soon or hidden.", field="listing"
            ) from exc
    if "sort_order" in changes:
        order = changes["sort_order"]
        if (
            isinstance(order, bool)
            or not isinstance(order, int)
            or not 0 <= order <= 10000
        ):
            raise CatalogValidationError(
                "sort_order is a whole number from 0 to 10,000.", field="sort_order"
            )
        clean["sort_order"] = order
    if "store_title" in changes:
        clean["store_title"] = validate_store_text(
            changes["store_title"], field="store_title", maximum=MAX_STORE_TITLE
        )
    if "store_summary" in changes:
        clean["store_summary"] = validate_store_text(
            changes["store_summary"], field="store_summary", maximum=MAX_STORE_SUMMARY
        )
    for key in _LINK_FIELDS:
        if key in changes:
            clean[key] = validate_link_url(changes[key], field=key)
    for key in sorted(_CHANNEL_FLAGS):
        if key in changes:
            if not isinstance(changes[key], bool):
                raise CatalogValidationError(f"{key} is true or false.", field=key)
            clean[key] = changes[key]
    return clean


def update_product(
    ctx: TenantContext, *, product_id: UUID, changes: dict[str, Any]
) -> Any:
    """Edit how a product is presented and listed (Phase 9): publish it to the
    public site, announce it as coming soon, or hide it. Only the keys in
    ``EDITABLE_PRODUCT_FIELDS`` are accepted; every value is validated before
    the row is touched, and the audit entry records what changed."""
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.update")
    clean = _clean_product_changes(changes)

    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        if (
            "slug" in clean
            and Product.objects.filter(slug=clean["slug"]).exclude(id=product.id).exists()
        ):
            raise CatalogValidationError(
                f"The slug {clean['slug']!r} is already in use.", field="slug"
            )
        changed = {
            key: value for key, value in clean.items() if getattr(product, key) != value
        }
        if not changed:
            return product
        if "slug" in changed and product.activated_at is not None:
            # ADR-051: the slug is the product's public address. Once it has
            # been on sale, links, bookmarks and partner pages use it.
            raise CatalogValidationError(
                "The slug is fixed once a product has been on sale: the public "
                "site, links and partner pages use it.",
                field="slug",
            )
        if changed.get("listing") == ProductListing.LISTED.value:
            _assert_publishable(product, overrides=changed)
        before = {key: getattr(product, key) for key in changed}
        for key, value in changed.items():
            setattr(product, key, value)
        product.save(update_fields=[*changed, "updated_at"])
        _refresh_public_catalog()
        audit.record(
            ctx,
            "commercial.product.updated",
            resource_type="catalog.product",
            resource_id=product.id,
            # A description can be long; the audit records only that it changed.
            changes={
                "fields": sorted(changed),
                "before": {k: v for k, v in before.items() if k != "description"},
                "after": {k: v for k, v in changed.items() if k != "description"},
            },
        )
    return product


# --- ADR-051: the Product Manager's lifecycle ------------------------------------------


def _assert_publishable(product: Any, *, overrides: dict[str, Any] | None = None) -> None:
    from workeasy360.commercial.catalog.selectors import publish_readiness

    missing = [
        i["label"] for i in publish_readiness(product, overrides=overrides) if not i["ok"]
    ]
    if missing:
        raise CatalogValidationError(
            "This product cannot be published yet. Missing: " + ", ".join(missing) + ".",
            field="listing",
            missing=missing,
        )


def _move(
    product: Any,
    *,
    status: ProductStatus | None = None,
    listing: ProductListing | None = None,
) -> dict[str, Any] | None:
    """Move one locked product row to ``status`` and/or ``listing``. Returns
    the audit change set (before and after), or ``None`` when nothing moved -
    a retry is then a no-op that records nothing. The caller audits."""
    now = timezone.now()
    before = {"status": product.status, "listing": product.listing}
    fields = ["updated_at"]
    if status is not None and product.status_enum is not status:
        assert_product_transition(product.status_enum, status)
        product.status = status.value
        fields.append("status")
        stamp = {
            ProductStatus.AVAILABLE: "activated_at",
            ProductStatus.DEPRECATED: "deprecated_at",
            ProductStatus.RETIRED: "retired_at",
        }.get(status)
        if stamp is not None and getattr(product, stamp) is None:
            setattr(product, stamp, now)
            fields.append(stamp)
    if listing is not None and product.listing != listing.value:
        product.listing = listing.value
        fields.append("listing")
    if len(fields) == 1:
        return None
    product.save(update_fields=fields)
    return {
        "code": product.code,
        "before": before,
        "after": {"status": product.status, "listing": product.listing},
    }


def publish_product(ctx: TenantContext, *, product_id: UUID) -> Any:
    """Show the product in the store: active and listed. Refused unless every
    publish requirement is met. Publishing grants nobody anything - access is
    an entitlement, made only by a trial or a purchase."""
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.publish")
    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        _assert_publishable(product)
        changes = _move(
            product, status=ProductStatus.AVAILABLE, listing=ProductListing.LISTED
        )
        if changes is not None:
            _refresh_public_catalog()
            audit.record(
                ctx,
                "commercial.product.published",
                resource_type="catalog.product",
                resource_id=product.id,
                changes=changes,
            )
    return product


def unpublish_product(ctx: TenantContext, *, product_id: UUID) -> Any:
    """Hide the product from the store. Its status, holders and partners are
    untouched: hidden products are still sold by staff, partners or contract."""
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.unpublish")
    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        changes = _move(product, listing=ProductListing.HIDDEN)
        if changes is not None:
            _refresh_public_catalog()
            audit.record(
                ctx,
                "commercial.product.unpublished",
                resource_type="catalog.product",
                resource_id=product.id,
                changes=changes,
            )
    return product


def archive_product(ctx: TenantContext, *, product_id: UUID) -> Any:
    """Stop selling the product and hide it, keeping every record: existing
    subscriptions continue (``deprecated``), nothing is deleted."""
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.archive")
    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        if product.status_enum is ProductStatus.RETIRED:
            return product
        changes = _move(
            product, status=ProductStatus.DEPRECATED, listing=ProductListing.HIDDEN
        )
        if changes is not None:
            _refresh_public_catalog()
            audit.record(
                ctx,
                "commercial.product.archived",
                resource_type="catalog.product",
                resource_id=product.id,
                changes=changes,
            )
    return product


def restore_product(ctx: TenantContext, *, product_id: UUID) -> Any:
    """Bring an archived product back as active but hidden; publishing it again
    is a separate, checked step. A retired product cannot return (its code is
    spent - see ``PRODUCT_TRANSITIONS``)."""
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.restore")
    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        if product.status_enum is not ProductStatus.DEPRECATED:
            raise CatalogValidationError(
                "Only an archived product can be restored.", field="status"
            )
        changes = _move(product, status=ProductStatus.AVAILABLE)
        if changes is not None:
            _refresh_public_catalog()
            audit.record(
                ctx,
                "commercial.product.restored",
                resource_type="catalog.product",
                resource_id=product.id,
                changes=changes,
            )
    return product


def delete_product(
    ctx: TenantContext, *, product_id: UUID, outside_references: list[str]
) -> str:
    """Delete a product that was never used. ``outside_references`` names what
    other modules still hold for it (the caller asks them); this adds what the
    catalog itself knows. Anything at all, and the answer is Archive.
    Returns the deleted product's code."""
    from django.db.models import ProtectedError

    from workeasy360.commercial.catalog.models import Plan, Product

    _authorise(ctx, "catalog.product.delete")
    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        reasons = list(outside_references)
        if (
            product.activated_at is not None
            or product.status_enum is not ProductStatus.DRAFT
        ):
            reasons.append("it has been active")
        if (
            Plan.objects.filter(product_id=product.id).exists()
            or Plan.objects.filter(addon_for_id=product.id).exists()
        ):
            reasons.append("it has plans")
        if product.features.exists():
            reasons.append("it has features")
        if reasons:
            raise CatalogValidationError(
                "This product cannot be deleted because "
                + "; ".join(reasons)
                + ". Archive it instead: its history is kept.",
                field="product_id",
            )
        code = product.code
        try:
            product.delete()
        except ProtectedError:
            raise CatalogValidationError(
                "This product is still referenced. Archive it instead.",
                field="product_id",
            ) from None
        audit.record(
            ctx,
            "commercial.product.deleted",
            resource_type="catalog.product",
            resource_id=product_id,
            changes={"code": code},
        )
    return code


# --- ADR-051: store-listing images -----------------------------------------------------

MEDIA_MODULE = "product_media"


def upload_product_media(
    ctx: TenantContext,
    *,
    product_id: UUID,
    kind: ProductMediaKind,
    content: bytes,
    content_type: str,
    caption: str = "",
) -> Any:
    """Store one listing image through the shared files platform and attach it.

    Type, byte size and pixel size are checked on the bytes here; the files
    platform checks the signature again, stores it under the platform
    organization's key prefix and scans it before it may be served. A logo or
    banner replaces the previous one; screenshots append, up to a limit.
    """
    import io

    from workeasy360.commercial.catalog.models import Product, ProductMedia
    from workeasy360.platform.files import services as files

    _authorise(ctx, "catalog.product.media")
    declared, width, height = validate_media(
        kind=kind, content_type=content_type, data=content
    )
    clean_caption = (
        validate_store_text(caption, field="caption", maximum=MAX_MEDIA_CAPTION)
        if kind is ProductMediaKind.SCREENSHOT
        else ""
    )

    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        existing = list(
            ProductMedia.objects.filter(product_id=product.id, kind=kind.value)
        )
        if kind is ProductMediaKind.SCREENSHOT and len(existing) >= MAX_SCREENSHOTS:
            raise CatalogValidationError(
                f"A product has at most {MAX_SCREENSHOTS} screenshots.", field="file"
            )
        file_id, _grant = files.register_upload(
            ctx,
            filename=f"{kind.value}.png"
            if declared == "image/png"
            else f"{kind.value}.jpg",
            content_type=declared,
            size_bytes=len(content),
            module=MEDIA_MODULE,
        )
        files.store_content(ctx, file_id, io.BytesIO(content))
        files.scan_now(file_id)
        replaced = None
        if kind is not ProductMediaKind.SCREENSHOT and existing:
            replaced = str(existing[0].file_id)
            ProductMedia.objects.filter(id=existing[0].id).delete()
        media = ProductMedia.objects.create(
            product=product,
            kind=kind.value,
            file_id=file_id,
            width=width,
            height=height,
            content_type=declared,
            size_bytes=len(content),
            caption=clean_caption,
            sort_order=max((m.sort_order for m in existing), default=-1) + 1
            if kind is ProductMediaKind.SCREENSHOT
            else 0,
            uploaded_by_id=ctx.user_id,
        )
        Product.objects.filter(id=product.id).update(updated_at=timezone.now())
        audit.record(
            ctx,
            "commercial.product.media_added",
            resource_type="catalog.product",
            resource_id=product.id,
            changes={
                "kind": kind.value,
                "media_id": str(media.id),
                "file_id": str(file_id),
                "replaced_file_id": replaced,
                "size_bytes": len(content),
                "dimensions": f"{width}x{height}",
            },
        )
    return media


def _media_of(product_id: UUID, media_id: UUID) -> Any:
    from workeasy360.commercial.catalog.models import ProductMedia

    return _one(
        ProductMedia.objects.select_for_update(), id=media_id, product_id=product_id
    )


def delete_product_media(ctx: TenantContext, *, product_id: UUID, media_id: UUID) -> None:
    """Remove one listing image. The file itself stays with the files platform
    (its retention rules apply); the product simply no longer shows it."""
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.media")
    with transaction.atomic():
        media = _media_of(product_id, media_id)
        kind, file_id = media.kind, media.file_id
        media.delete()
        Product.objects.filter(id=product_id).update(updated_at=timezone.now())
        audit.record(
            ctx,
            "commercial.product.media_removed",
            resource_type="catalog.product",
            resource_id=product_id,
            changes={"kind": kind, "media_id": str(media_id), "file_id": str(file_id)},
        )


def update_media_caption(
    ctx: TenantContext, *, product_id: UUID, media_id: UUID, caption: Any
) -> Any:
    _authorise(ctx, "catalog.product.media")
    clean = validate_store_text(caption, field="caption", maximum=MAX_MEDIA_CAPTION)
    with transaction.atomic():
        media = _media_of(product_id, media_id)
        if media.kind != ProductMediaKind.SCREENSHOT.value:
            raise CatalogValidationError(
                "Only a screenshot has a caption.", field="caption"
            )
        if media.caption != clean:
            media.caption = clean
            media.save(update_fields=["caption"])
            audit.record(
                ctx,
                "commercial.product.media_captioned",
                resource_type="catalog.product",
                resource_id=product_id,
                changes={"media_id": str(media_id)},
            )
    return media


def reorder_screenshots(ctx: TenantContext, *, product_id: UUID, order: Any) -> list[Any]:
    """Put the product's screenshots in exactly the order given: every one of
    them, each once - never a partial list that silently drops one."""
    from workeasy360.commercial.catalog.models import Product, ProductMedia

    _authorise(ctx, "catalog.product.media")
    if not isinstance(order, list) or not all(isinstance(i, str) for i in order):
        raise CatalogValidationError("order is a list of screenshot ids.", field="order")
    with transaction.atomic():
        _one(Product.objects.select_for_update(), id=product_id)
        shots = {
            str(m.id): m
            for m in ProductMedia.objects.select_for_update().filter(
                product_id=product_id, kind=ProductMediaKind.SCREENSHOT.value
            )
        }
        if sorted(order) != sorted(shots) or len(set(order)) != len(order):
            raise CatalogValidationError(
                "order must name every screenshot of this product exactly once.",
                field="order",
            )
        for position, media_id in enumerate(order):
            media = shots[media_id]
            if media.sort_order != position:
                media.sort_order = position
                media.save(update_fields=["sort_order"])
        audit.record(
            ctx,
            "commercial.product.media_reordered",
            resource_type="catalog.product",
            resource_id=product_id,
            changes={"order": list(order)},
        )
    return [shots[i] for i in order]


def set_product_status(
    ctx: TenantContext, *, product_id: UUID, status: ProductStatus
) -> Any:
    """Move a product through its lifecycle.

    Retiring a product does **not** touch subscriptions. A subscription pins
    an immutable ``PlanVersion``; the live product row's status is read only
    when deciding whether a *new* subscription may be created. That is what
    makes "a retired product must not invalidate historical records" a
    structural property rather than a promise.
    """
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.status")

    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        current = product.status_enum
        assert_product_transition(current, status)
        if current is status:
            return product

        now = timezone.now()
        product.status = status.value
        stamps = {
            ProductStatus.AVAILABLE: "activated_at",
            ProductStatus.DEPRECATED: "deprecated_at",
            ProductStatus.RETIRED: "retired_at",
        }
        fields = ["status", "updated_at"]
        stamp = stamps.get(status)
        if stamp is not None and getattr(product, stamp) is None:
            setattr(product, stamp, now)
            fields.append(stamp)
        product.save(update_fields=fields)

        _refresh_public_catalog()
        audit.record(
            ctx,
            "commercial.product.status_changed",
            resource_type="catalog.product",
            resource_id=product.id,
            changes={"from": current.value, "to": status.value},
        )
    return product


def configure_product_trial(
    ctx: TenantContext,
    *,
    product_id: UUID,
    enabled: Any,
    duration_days: Any,
    seat_limit: Any = None,
    payment_method_required: Any = False,
    eligible_plan_codes: Any = None,
) -> Any:
    """Set how a product may be trialled (Phase 11). One row per product.

    Shape and bounds are ``validate_trial_terms``; here the eligible plans are
    checked against the catalog (each must be a standard plan of *this*
    product) and the change is audited with before and after. It changes what
    a **future** trial gets; a running trial keeps the terms it started with.
    """
    from workeasy360.commercial.catalog.models import Plan, Product, ProductTrialConfig

    _authorise(ctx, "catalog.product.trial")
    terms = validate_trial_terms(
        enabled=enabled,
        duration_days=duration_days,
        seat_limit=seat_limit,
        payment_method_required=payment_method_required,
        eligible_plan_codes=[] if eligible_plan_codes is None else eligible_plan_codes,
    )

    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        known = set(
            Plan.objects.filter(
                product_id=product.id,
                code__in=terms["eligible_plan_codes"],
                kind=PlanKind.STANDARD.value,
            ).values_list("code", flat=True)
        )
        unknown = sorted(set(terms["eligible_plan_codes"]) - known)
        if unknown:
            raise CatalogValidationError(
                "These are not standard plans of this product: "
                + ", ".join(unknown)
                + ".",
                field="eligible_plan_codes",
            )
        existing = ProductTrialConfig.objects.filter(product_id=product.id).first()
        before = (
            None
            if existing is None
            else {
                "enabled": existing.enabled,
                "duration_days": existing.duration_days,
                "seat_limit": existing.seat_limit,
                "eligible_plan_codes": list(existing.eligible_plan_codes),
            }
        )
        config, _ = ProductTrialConfig.objects.update_or_create(
            product_id=product.id, defaults=terms
        )
        after = {
            "enabled": config.enabled,
            "duration_days": config.duration_days,
            "seat_limit": config.seat_limit,
            "eligible_plan_codes": list(config.eligible_plan_codes),
        }
        if before != after:
            _refresh_public_catalog()
            audit.record(
                ctx,
                "commercial.product.trial_configured",
                resource_type="catalog.product",
                resource_id=product.id,
                changes={"code": product.code, "before": before, "after": after},
            )
    return config


# --- features -------------------------------------------------------------


def create_feature(
    ctx: TenantContext,
    *,
    product_id: UUID,
    key: str,
    name: str,
    kind: FeatureKind,
    unit: str = "",
    default_value: Any = False,
    enum_ordering: list[str] | None = None,
    is_public: bool = True,
) -> Any:
    """Declare a sellable capability.

    The key's leading segment must equal the product's code. Validated here
    because a feature key is an authorization identifier in all but name: the
    runtime asks for it by string, and a typo is a silent grant or denial.
    """
    from workeasy360.commercial.catalog.models import Feature, Product

    _authorise(ctx, "catalog.feature.create")

    with transaction.atomic():
        product = _one(Product.objects, id=product_id)
        validated = validate_feature_key(key, product_code=product.code)

        if kind is FeatureKind.ENUM and not enum_ordering:
            raise CatalogValidationError(
                f"Enum feature {key!r} must declare enum_ordering, least to "
                f"most generous. Without an ordering, overlapping grants "
                f"cannot be resolved and the customer's value is arbitrary.",
                field="enum_ordering",
            )
        validate_feature_value(
            key=key,
            kind=kind,
            value=default_value,
            enum_ordering=enum_ordering,
        )

        feature = Feature.objects.create(
            product=product,
            key=str(validated),
            name=name,
            kind=kind.value,
            unit=unit,
            default_value=default_value,
            enum_ordering=enum_ordering or [],
            is_public=is_public,
        )
        audit.record(
            ctx,
            "commercial.feature.created",
            resource_type="catalog.feature",
            resource_id=feature.id,
            changes={"key": feature.key, "kind": feature.kind},
        )
    return feature


def update_feature(
    ctx: TenantContext,
    *,
    feature_id: UUID,
    name: str | None = None,
    description: str | None = None,
    unit: str | None = None,
    is_public: bool | None = None,
) -> Any:
    """Change a feature's **presentation only**.

    ``key`` and ``kind`` are deliberately absent from this signature. A
    published plan states a value for a feature; changing the feature's kind
    underneath would change what that plan means without touching a single
    plan row, and changing its key would silently detach every check in
    product code from the thing the customer bought.
    """
    from workeasy360.commercial.catalog.models import Feature

    _authorise(ctx, "catalog.feature.update")

    with transaction.atomic():
        feature = _one(Feature.objects.select_for_update(), id=feature_id)
        changed: dict[str, Any] = {}
        for field, value in (
            ("name", name),
            ("description", description),
            ("unit", unit),
            ("is_public", is_public),
        ):
            if value is not None and getattr(feature, field) != value:
                setattr(feature, field, value)
                changed[field] = value
        if changed:
            feature.save(update_fields=[*changed, "updated_at"])
            audit.record(
                ctx,
                "commercial.feature.updated",
                resource_type="catalog.feature",
                resource_id=feature.id,
                changes=changed,
            )
    return feature


# --- plans ----------------------------------------------------------------


def create_plan(
    ctx: TenantContext,
    *,
    product_id: UUID,
    code: str,
    name: str,
    description: str = "",
    is_public: bool = True,
    sort_order: int = 100,
    caption: str = "",
    billing_type: PlanBillingType = PlanBillingType.PAID,
    kind: PlanKind = PlanKind.STANDARD,
    addon_for_product_id: UUID | None = None,
) -> Any:
    from workeasy360.commercial.catalog.models import Plan, Product

    _authorise(ctx, "catalog.plan.create")
    _validate_texts(name=name, caption=caption, description=description)

    with transaction.atomic():
        product = _one(Product.objects, id=product_id)
        addon_for = _addon_target(kind, addon_for_product_id, product)
        if kind is PlanKind.ADD_ON and billing_type is PlanBillingType.FREE_TRIAL:
            raise CatalogValidationError(
                "An add-on cannot be a free trial: it extends a subscription "
                "that already exists.",
                field="billing_type",
            )
        if Plan.objects.filter(code=code).exists():
            raise CatalogValidationError(
                f"Plan code {code!r} is already in use.", field="code"
            )
        plan = Plan.objects.create(
            product=product,
            code=code,
            name=name,
            description=description,
            is_public=is_public,
            sort_order=sort_order,
            caption=caption,
            billing_type=billing_type.value,
            kind=kind.value,
            addon_for=addon_for,
        )
        audit.record(
            ctx,
            "commercial.plan.created",
            resource_type="catalog.plan",
            resource_id=plan.id,
            changes={"code": plan.code, "product": product.code},
        )
    return plan


#: Bounds on the plan builder's free text. Generous, but finite: a catalog row
#: is read on every price list and every purchase.
MAX_NAME = 128
MAX_CAPTION = 160
MAX_DESCRIPTION = 5_000


def _validate_texts(
    *, name: Any = None, caption: Any = "", description: Any = ""
) -> None:
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise CatalogValidationError("A name is required.", field="name")
    for field, value, limit in (
        ("name", name, MAX_NAME),
        ("caption", caption, MAX_CAPTION),
        ("description", description, MAX_DESCRIPTION),
    ):
        if value is None:
            continue
        if not isinstance(value, str):
            raise CatalogValidationError(f"{field} must be text.", field=field)
        if len(value) > limit:
            raise CatalogValidationError(
                f"{field} may be at most {limit:,} characters.", field=field
            )
        if "\x00" in value:
            raise CatalogValidationError(f"{field} contains a null byte.", field=field)
    if caption and ("\n" in caption or "\r" in caption):
        raise CatalogValidationError("The caption must be one line.", field="caption")


def _addon_target(kind: PlanKind, addon_for_product_id: UUID | None, product: Any) -> Any:
    from workeasy360.commercial.catalog.models import Product

    if kind is not PlanKind.ADD_ON:
        if addon_for_product_id is not None:
            raise CatalogValidationError(
                "Only an add-on names a product it extends.", field="addon_for"
            )
        return None
    if addon_for_product_id is None:
        raise CatalogValidationError(
            "An add-on must name the product it extends.", field="addon_for"
        )
    target = _one(Product.objects, id=addon_for_product_id)
    if target.id == product.id:
        raise CatalogValidationError(
            "An add-on extends a different product from its own.", field="addon_for"
        )
    return target


def update_plan(
    ctx: TenantContext,
    *,
    plan_id: UUID,
    name: Any = None,
    caption: Any = None,
    description: Any = None,
    is_public: Any = None,
    sort_order: Any = None,
    billing_type: PlanBillingType | None = None,
) -> Any:
    """Change a plan's presentation - name, caption, description, visibility,
    order. Never its commercial terms (those live on immutable versions).

    ``billing_type`` may change only while the plan is a draft: flipping a
    plan customers already bought between "free trial" and "paid" would change
    what they signed up for.
    """
    from workeasy360.commercial.catalog.models import Plan

    _authorise(ctx, "catalog.plan.update")
    _validate_texts(name=name, caption=caption, description=description)
    changes: dict[str, Any] = {}
    with transaction.atomic():
        plan = _one(Plan.objects.select_for_update(), id=plan_id)
        for field, value in (
            ("name", name),
            ("caption", caption),
            ("description", description),
        ):
            if value is not None and getattr(plan, field) != value:
                changes[field] = {
                    "from_length": len(getattr(plan, field)),
                    "to_length": len(value),
                }
                setattr(plan, field, value)
        if is_public is not None:
            if not isinstance(is_public, bool):
                raise CatalogValidationError(
                    "is_public must be true or false.", field="is_public"
                )
            if plan.is_public != is_public:
                changes["is_public"] = {"from": plan.is_public, "to": is_public}
                plan.is_public = is_public
        if sort_order is not None:
            if isinstance(sort_order, bool) or not isinstance(sort_order, int):
                raise CatalogValidationError(
                    "sort_order must be an integer.", field="sort_order"
                )
            plan.sort_order = sort_order
        if billing_type is not None and billing_type.value != plan.billing_type:
            if plan.status_enum is not CatalogStatus.DRAFT:
                raise CatalogValidationError(
                    "The type can only change while the plan is a draft.",
                    field="billing_type",
                )
            if (
                plan.kind == PlanKind.ADD_ON.value
                and billing_type is PlanBillingType.FREE_TRIAL
            ):
                raise CatalogValidationError(
                    "An add-on cannot be a free trial.", field="billing_type"
                )
            changes["billing_type"] = {
                "from": plan.billing_type,
                "to": billing_type.value,
            }
            plan.billing_type = billing_type.value
        plan.save()
        audit.record(
            ctx,
            "commercial.plan.updated",
            resource_type="catalog.plan",
            resource_id=plan.id,
            changes=changes or {"unchanged": True},
        )
    return plan


def set_plan_status(ctx: TenantContext, *, plan_id: UUID, status: CatalogStatus) -> Any:
    from workeasy360.commercial.catalog.models import Plan

    _authorise(ctx, "catalog.plan.status")

    with transaction.atomic():
        plan = _one(Plan.objects.select_for_update(), id=plan_id)
        current = plan.status_enum
        assert_catalog_transition(current, status)
        if current is status:
            return plan
        plan.status = status.value
        plan.save(update_fields=["status", "updated_at"])
        audit.record(
            ctx,
            "commercial.plan.status_changed",
            resource_type="catalog.plan",
            resource_id=plan.id,
            changes={"from": current.value, "to": status.value},
        )
    return plan


def draft_plan_version(
    ctx: TenantContext,
    *,
    plan_id: UUID,
    features: dict[str, Any],
    included_seats: int = 1,
    max_seats: int | None = None,
    trial_days: int = 0,
    effective_from: datetime | None = None,
) -> Any:
    """Create the next draft version of a plan.

    Always the next integer version of *this* plan, allocated under a row
    lock on the plan: two administrators drafting simultaneously must not
    both produce "v3".
    """
    from workeasy360.commercial.catalog.models import Plan, PlanFeature, PlanVersion

    _authorise(ctx, "catalog.plan_version.draft")

    with transaction.atomic():
        plan = _one(
            Plan.objects.select_for_update().select_related("product"), id=plan_id
        )

        resolved = _resolve_features(plan.product.code, features)

        latest = (
            PlanVersion.objects.filter(plan=plan)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        version = PlanVersion.objects.create(
            plan=plan,
            version=(latest or 0) + 1,
            effective_from=effective_from or timezone.now(),
            included_seats=included_seats,
            max_seats=max_seats,
            trial_days=trial_days,
        )
        PlanFeature.objects.bulk_create(
            [
                PlanFeature(plan_version=version, feature=feature, value=value)
                for feature, value in resolved
            ]
        )
        audit.record(
            ctx,
            "commercial.plan_version.drafted",
            resource_type="catalog.plan_version",
            resource_id=version.id,
            changes={
                "plan": plan.code,
                "version": version.version,
                "features": sorted(features),
            },
        )
    return version


def _resolve_features(product_code: str, features: dict[str, Any]) -> list[tuple]:
    """Resolve feature keys to rows and validate each stated value.

    One query for the whole set rather than one per key: drafting a plan with
    twenty features should not be twenty round trips.
    """
    from workeasy360.commercial.catalog.models import Feature

    if not features:
        return []

    rows = {f.key: f for f in Feature.objects.filter(key__in=list(features))}
    missing = sorted(set(features) - set(rows))
    if missing:
        raise CatalogValidationError(
            f"Unknown feature keys: {missing}. Commercial can only sell "
            f"features that are declared in the catalog - this is what stops "
            f"the catalog and the runtime drifting apart.",
            field="features",
        )

    resolved = []
    for key, value in features.items():
        feature = rows[key]
        validate_feature_key(key, product_code=product_code)
        if feature.product.code != product_code:
            raise CatalogValidationError(
                f"Feature {key!r} belongs to product "
                f"{feature.product.code!r}, not {product_code!r}. A plan may "
                f"only grant features of its own product.",
                field="features",
            )
        validate_feature_value(
            key=key,
            kind=feature.kind_enum,
            value=value,
            enum_ordering=feature.enum_ordering,
        )
        resolved.append((feature, value))
    return resolved


def publish_plan_version(ctx: TenantContext, *, plan_version_id: UUID) -> Any:
    """Publish a draft. A one-way door.

    Publishing supersedes the previously-current published version by setting
    its ``effective_to``, so "which version is in force?" has exactly one
    answer at any instant. Existing subscriptions are untouched: they pinned a
    version id, not "whatever is current".
    """
    from workeasy360.commercial.catalog.models import PlanVersion

    _authorise(ctx, "catalog.plan_version.publish")

    with transaction.atomic():
        version = _one(
            PlanVersion.objects.select_for_update().select_related(
                "plan", "plan__product"
            ),
            id=plan_version_id,
        )
        if version.is_published:
            raise ImmutableRecord(
                f"Plan version {version.plan.code} v{version.version} is "
                f"already published. Publish a new version instead.",
                resource="catalog.plan_version",
            )
        if (
            version.plan.billing_type == PlanBillingType.FREE_TRIAL.value
            and version.trial_days < 1
        ):
            raise CatalogValidationError(
                "A free-trial plan must give at least one trial day.",
                field="trial_days",
            )

        now = timezone.now()
        superseded = (
            PlanVersion.objects.select_for_update()
            .filter(
                plan_id=version.plan_id, published_at__isnull=False, effective_to=None
            )
            .exclude(id=version.id)
        )
        # ``effective_to`` is the one column a published version may still
        # change - it is how supersession is recorded - so the immutability
        # trigger permits exactly this write and nothing else.
        for previous in superseded:
            previous.effective_to = max(now, version.effective_from)
            previous.save(update_fields=["effective_to", "updated_at"])

        version.published_at = now
        version.save(update_fields=["published_at", "updated_at"])

        audit.record(
            ctx,
            "commercial.plan_version.published",
            resource_type="catalog.plan_version",
            resource_id=version.id,
            changes={
                "plan": version.plan.code,
                "version": version.version,
                "superseded": [str(p.id) for p in superseded],
            },
        )
    logger.info(
        "catalog.plan_version.published",
        plan_code=version.plan.code,
        version=version.version,
    )
    return version


# --- bundles --------------------------------------------------------------


def create_bundle(
    ctx: TenantContext,
    *,
    code: str,
    name: str,
    kind: BundleKind = BundleKind.PREDEFINED,
    description: str = "",
    is_public: bool = True,
    caption: str = "",
    billing_type: PlanBillingType = PlanBillingType.PAID,
) -> Any:
    from workeasy360.commercial.catalog.models import Bundle

    _authorise(ctx, "catalog.bundle.create")
    _validate_texts(name=name, caption=caption, description=description)

    with transaction.atomic():
        if Bundle.objects.filter(code=code).exists():
            raise CatalogValidationError(
                f"Bundle code {code!r} is already in use.", field="code"
            )
        bundle = Bundle.objects.create(
            code=code,
            name=name,
            kind=kind.value,
            caption=caption,
            billing_type=billing_type.value,
            description=description,
            # A custom bundle is enterprise-specific and must never be listed
            # publicly; the database check constraint agrees.
            is_public=is_public and kind is not BundleKind.CUSTOM,
        )
        audit.record(
            ctx,
            "commercial.bundle.created",
            resource_type="catalog.bundle",
            resource_id=bundle.id,
            changes={"code": bundle.code, "kind": bundle.kind},
        )
    return bundle


def draft_bundle_version(
    ctx: TenantContext,
    *,
    bundle_id: UUID,
    items: list[dict[str, Any]],
    effective_from: datetime | None = None,
    trial_days: int = 0,
) -> Any:
    """Create the next draft of a bundle.

    ``items`` is a list of ``{"plan_version_id", "quantity", "is_optional",
    "seat_pool"}``. Validation is deliberately strict here rather than at
    purchase time: a malformed bundle discovered during checkout is a lost
    sale, and discovered during expansion is a half-provisioned customer.
    """
    from workeasy360.commercial.catalog.models import (
        Bundle,
        BundleItem,
        BundleVersion,
        PlanVersion,
    )

    _authorise(ctx, "catalog.bundle_version.draft")

    if not items:
        raise CatalogValidationError(
            "A bundle version must contain at least one item.", field="items"
        )
    if (
        isinstance(trial_days, bool)
        or not isinstance(trial_days, int)
        or not (0 <= trial_days <= 365)
    ):
        raise CatalogValidationError(
            "trial_days must be between 0 and 365.", field="trial_days"
        )

    with transaction.atomic():
        bundle = _one(Bundle.objects.select_for_update(), id=bundle_id)

        requested = [UUID(str(item["plan_version_id"])) for item in items]
        duplicates = sorted({v for v in requested if requested.count(v) > 1})
        if duplicates:
            raise CatalogValidationError(
                f"Bundle {bundle.code!r} lists plan version(s) {duplicates} "
                f"more than once. A duplicate expands into two identical "
                f"subscription items and bills twice.",
                field="items",
            )

        versions = {
            v.id: v
            for v in PlanVersion.objects.select_related("plan", "plan__product").filter(
                id__in=requested
            )
        }
        missing = sorted(set(requested) - set(versions))
        if missing:
            raise CatalogValidationError(
                f"Unknown plan version(s): {missing}.", field="items"
            )

        seen_products: dict[str, str] = {}
        for plan_version_id in requested:
            version = versions[plan_version_id]
            if not version.is_published:
                raise CatalogValidationError(
                    f"Plan version {version.plan.code} v{version.version} is "
                    f"not published. A bundle may only grant published "
                    f"versions - a draft can still change under it.",
                    field="items",
                )
            if version.plan.status_enum is CatalogStatus.RETIRED:
                raise CatalogValidationError(
                    f"Plan {version.plan.code!r} is retired and cannot be "
                    f"added to a bundle.",
                    field="items",
                )
            if version.plan.kind == PlanKind.ADD_ON.value:
                raise CatalogValidationError(
                    f"Plan {version.plan.code!r} is an add-on. Add-ons are "
                    f"bought on top of a subscription, never inside a bundle.",
                    field="items",
                )
            if not version.plan.product.status_enum.is_sellable:
                raise CatalogValidationError(
                    f"Product {version.plan.product.code!r} is "
                    f"{version.plan.product.status} and is not sellable.",
                    field="items",
                )
            product_code = version.plan.product.code
            if product_code in seen_products:
                raise CatalogValidationError(
                    f"Bundle {bundle.code!r} grants two plans of product "
                    f"{product_code!r} ({seen_products[product_code]} and "
                    f"{version.plan.code}). Which tier the customer holds "
                    f"would be ambiguous.",
                    field="items",
                )
            seen_products[product_code] = version.plan.code

        latest = (
            BundleVersion.objects.filter(bundle=bundle)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        bundle_version = BundleVersion.objects.create(
            bundle=bundle,
            version=(latest or 0) + 1,
            effective_from=effective_from or timezone.now(),
            trial_days=trial_days,
        )
        BundleItem.objects.bulk_create(
            [
                BundleItem(
                    bundle_version=bundle_version,
                    plan_version_id=UUID(str(item["plan_version_id"])),
                    quantity=int(item.get("quantity", 1)),
                    is_optional=bool(item.get("is_optional", False)),
                    seat_pool=str(item.get("seat_pool", "")),
                )
                for item in items
            ]
        )
        audit.record(
            ctx,
            "commercial.bundle_version.drafted",
            resource_type="catalog.bundle_version",
            resource_id=bundle_version.id,
            changes={
                "bundle": bundle.code,
                "version": bundle_version.version,
                "plans": sorted(seen_products.values()),
            },
        )
    return bundle_version


def publish_bundle_version(ctx: TenantContext, *, bundle_version_id: UUID) -> Any:
    """Publish a bundle version, re-validating every item first.

    Re-validation matters: a plan may have been retired between drafting and
    publishing, and publishing a bundle that grants a withdrawn plan produces
    a purchase that cannot be provisioned.
    """
    from workeasy360.commercial.catalog.models import BundleVersion

    _authorise(ctx, "catalog.bundle_version.publish")

    with transaction.atomic():
        version = _one(
            BundleVersion.objects.select_for_update().select_related("bundle"),
            id=bundle_version_id,
        )
        if version.is_published:
            raise ImmutableRecord(
                f"Bundle version {version.bundle.code} v{version.version} is "
                f"already published.",
                resource="catalog.bundle_version",
            )

        if (
            version.bundle.billing_type == PlanBillingType.FREE_TRIAL.value
            and version.trial_days < 1
        ):
            raise CatalogValidationError(
                "A free-trial plan must give at least one trial day.",
                field="trial_days",
            )
        items = list(version.items.select_related("plan_version__plan__product").all())
        if not items:
            raise CatalogValidationError(
                f"Bundle version {version.bundle.code} v{version.version} has no items.",
                field="items",
            )
        for item in items:
            plan = item.plan_version.plan
            if plan.status_enum is CatalogStatus.RETIRED:
                raise CatalogValidationError(
                    f"Cannot publish: plan {plan.code!r} has been retired "
                    f"since this version was drafted.",
                    field="items",
                )
            if not item.plan_version.is_published:
                raise CatalogValidationError(
                    f"Cannot publish: plan version {plan.code} "
                    f"v{item.plan_version.version} is not published.",
                    field="items",
                )

        now = timezone.now()
        superseded = list(
            BundleVersion.objects.select_for_update()
            .filter(
                bundle_id=version.bundle_id,
                published_at__isnull=False,
                effective_to=None,
            )
            .exclude(id=version.id)
        )
        for previous in superseded:
            previous.effective_to = max(now, version.effective_from)
            previous.save(update_fields=["effective_to", "updated_at"])

        version.published_at = now
        version.save(update_fields=["published_at", "updated_at"])

        audit.record(
            ctx,
            "commercial.bundle_version.published",
            resource_type="catalog.bundle_version",
            resource_id=version.id,
            changes={"bundle": version.bundle.code, "version": version.version},
            outcome=AuditOutcome.SUCCESS,
        )
    return version


def set_bundle_status(
    ctx: TenantContext, *, bundle_id: UUID, status: CatalogStatus
) -> Any:
    from workeasy360.commercial.catalog.models import Bundle

    _authorise(ctx, "catalog.bundle.status")

    with transaction.atomic():
        bundle = _one(Bundle.objects.select_for_update(), id=bundle_id)
        current = bundle.status_enum
        assert_catalog_transition(current, status)
        if current is status:
            return bundle

        if status is CatalogStatus.AVAILABLE:
            _assert_bundle_is_activatable(bundle)

        bundle.status = status.value
        bundle.save(update_fields=["status", "updated_at"])
        audit.record(
            ctx,
            "commercial.bundle.status_changed",
            resource_type="catalog.bundle",
            resource_id=bundle.id,
            changes={"from": current.value, "to": status.value},
        )
    return bundle


def _assert_bundle_is_activatable(bundle: Any) -> None:
    """A bundle going on sale must have a published version of live plans.

    Checked at activation rather than only at draft time because the plans
    underneath can change status in between, and "available bundle containing
    a retired plan" is a purchase that cannot be fulfilled.
    """
    from workeasy360.commercial.catalog.models import BundleVersion

    published = (
        BundleVersion.objects.filter(bundle=bundle, published_at__isnull=False)
        .order_by("-version")
        .first()
    )
    if published is None:
        raise CatalogValidationError(
            f"Bundle {bundle.code!r} has no published version and cannot be "
            f"made available.",
            field="status",
        )

    offending = [
        item.plan_version.plan.code
        for item in published.items.select_related("plan_version__plan").all()
        if item.plan_version.plan.status_enum is CatalogStatus.RETIRED
    ]
    if offending:
        raise CatalogValidationError(
            f"Bundle {bundle.code!r} cannot be made available: it grants "
            f"retired plan(s) {sorted(offending)}.",
            field="status",
        )


def update_bundle(
    ctx: TenantContext,
    *,
    bundle_id: UUID,
    name: Any = None,
    caption: Any = None,
    description: Any = None,
    is_public: Any = None,
    billing_type: PlanBillingType | None = None,
) -> Any:
    """A bundle's presentation; the same rules as :func:`update_plan`."""
    from workeasy360.commercial.catalog.models import Bundle

    _authorise(ctx, "catalog.bundle.update")
    _validate_texts(name=name, caption=caption, description=description)
    changes: dict[str, Any] = {}
    with transaction.atomic():
        bundle = _one(Bundle.objects.select_for_update(), id=bundle_id)
        for field, value in (
            ("name", name),
            ("caption", caption),
            ("description", description),
        ):
            if value is not None and getattr(bundle, field) != value:
                changes[field] = {
                    "from_length": len(getattr(bundle, field)),
                    "to_length": len(value),
                }
                setattr(bundle, field, value)
        if is_public is not None:
            if not isinstance(is_public, bool):
                raise CatalogValidationError(
                    "is_public must be true or false.", field="is_public"
                )
            if is_public and bundle.kind == BundleKind.CUSTOM.value:
                raise CatalogValidationError(
                    "A custom bundle is never publicly listed.", field="is_public"
                )
            if bundle.is_public != is_public:
                changes["is_public"] = {"from": bundle.is_public, "to": is_public}
                bundle.is_public = is_public
        if billing_type is not None and billing_type.value != bundle.billing_type:
            if bundle.status_enum is not CatalogStatus.DRAFT:
                raise CatalogValidationError(
                    "The type can only change while the plan is a draft.",
                    field="billing_type",
                )
            changes["billing_type"] = {
                "from": bundle.billing_type,
                "to": billing_type.value,
            }
            bundle.billing_type = billing_type.value
        bundle.save()
        audit.record(
            ctx,
            "commercial.bundle.updated",
            resource_type="catalog.bundle",
            resource_id=bundle.id,
            changes=changes or {"unchanged": True},
        )
    return bundle
