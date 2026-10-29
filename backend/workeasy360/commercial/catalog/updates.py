"""Product updates: feature announcements and releases (ADR-054).

Staff (``commercial.catalog.manage``) write an update as a draft, publish it
and later archive it. A published update of a Store product has a public page
on the platform's own origin, and authorized affiliates may make a referral
link to it. Nothing here grants anyone a product: an update is marketing
content, read by visitors and promoted by partners.

**An update is never deleted** - a referral link names it, so its history must
keep its target. Archiving stops new links and takes the public page down; an
existing link then opens the product page instead, with its attribution intact.

**The landing page is shown, never redirected to.** ``landing_url`` must be an
https page on an approved host - a platform domain or one of its subdomains
(``W360_AFFILIATE_LANDING_HOSTS``) or the exact host of the product's own
marketing page, which staff approved when they set it.
"""

from __future__ import annotations

import re
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.catalog.domain import validate_link_url, validate_store_text
from workeasy360.commercial.catalog.services import _authorise, _one
from workeasy360.contracts.enums import (
    ProductMediaKind,
    ProductStatus,
    ProductUpdateKind,
    ProductUpdateStatus,
)
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

_SLUG: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_TITLE: Final = 120
MAX_SUMMARY: Final = 300
MAX_BODY: Final = 4000
MAX_UPDATES_PER_PRODUCT: Final = 200
_FIELDS: Final = frozenset(
    {"kind", "slug", "title", "summary", "body", "banner_id", "landing_url"}
)


def approved_landing_hosts(product: Any) -> frozenset[str]:
    """What an update of ``product`` may name as its landing page: each
    configured domain (it and its subdomains) and the product's own marketing
    host (exactly)."""
    hosts = {h.lower() for h in getattr(settings, "W360_AFFILIATE_LANDING_HOSTS", ())}
    own = urlsplit(product.marketing_url or "").hostname
    if own:
        hosts.add(own.lower())
    return frozenset(hosts)


def _host_allowed(host: str, product: Any) -> bool:
    domains = {h.lower() for h in getattr(settings, "W360_AFFILIATE_LANDING_HOSTS", ())}
    own = (urlsplit(product.marketing_url or "").hostname or "").lower()
    return bool(host) and (
        host == own or any(host == d or host.endswith(f".{d}") for d in domains)
    )


def _landing_url(raw: Any, product: Any) -> str:
    value = validate_link_url(raw, field="landing_url")
    if value and not _host_allowed((urlsplit(value).hostname or "").lower(), product):
        raise CatalogValidationError(
            "The landing page must be on an approved host: the Workeasy360 website "
            "or the product's own marketing page.",
            field="landing_url",
        )
    return value


def _body_text(raw: Any) -> str:
    """Plain text with line breaks kept; no other control characters."""
    text = str(raw or "").replace("\r\n", "\n").strip()
    if len(text) > MAX_BODY or any(ord(ch) < 32 and ch != "\n" for ch in text):
        raise CatalogValidationError(
            f"The promotional text is at most {MAX_BODY} characters of plain text.",
            field="body",
        )
    return text


def _clean(product: Any, body: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    from workeasy360.commercial.catalog.models import ProductMedia

    if not isinstance(body, dict):
        raise CatalogValidationError("The request body is an object.", field="body")
    unknown = sorted(set(body) - _FIELDS)
    if unknown:
        raise CatalogValidationError(f"Unknown field: {unknown[0]}.", field=unknown[0])
    out: dict[str, Any] = {}
    if creating or "kind" in body:
        try:
            out["kind"] = ProductUpdateKind(str(body.get("kind") or "")).value
        except ValueError:
            raise CatalogValidationError(
                "Choose feature or release.", field="kind"
            ) from None
    if creating or "slug" in body:
        slug = str(body.get("slug") or "").strip().lower()
        if not _SLUG.match(slug) or len(slug) > 64:
            raise CatalogValidationError(
                "The address is 1 to 64 lowercase letters, digits and dashes.",
                field="slug",
            )
        out["slug"] = slug
    if creating or "title" in body:
        title = validate_store_text(body.get("title"), field="title", maximum=MAX_TITLE)
        if not title:
            raise CatalogValidationError("Give the update a title.", field="title")
        out["title"] = title
    if "summary" in body:
        out["summary"] = validate_store_text(
            body.get("summary"), field="summary", maximum=MAX_SUMMARY
        )
    if "body" in body:
        out["body"] = _body_text(body.get("body"))
    if "landing_url" in body:
        out["landing_url"] = _landing_url(body.get("landing_url"), product)
    if "banner_id" in body:
        raw = body.get("banner_id")
        if raw in (None, ""):
            out["banner_id"] = None
        else:
            try:
                media_id = UUID(str(raw))
            except ValueError:
                raise CatalogValidationError(
                    "No such image.", field="banner_id"
                ) from None
            # Only one of this product's own approved banner or screenshot images.
            if not ProductMedia.objects.filter(
                id=media_id,
                product_id=product.id,
                kind__in=[
                    ProductMediaKind.BANNER.value,
                    ProductMediaKind.SCREENSHOT.value,
                ],
            ).exists():
                raise CatalogValidationError(
                    "Choose one of the product's banner or screenshot images.",
                    field="banner_id",
                )
            out["banner_id"] = media_id
    return out


def require_update_of(product_id: UUID, update_id: UUID) -> None:
    """The update must be this product's: a mismatched pair is a 404."""
    from workeasy360.commercial.catalog.models import ProductUpdate
    from workeasy360.contracts.errors import CommercialRecordNotFound

    if not ProductUpdate.objects.filter(id=update_id, product_id=product_id).exists():
        raise CommercialRecordNotFound("No such update.", update_id=str(update_id))


@in_scope
def list_updates(ctx: TenantContext, *, product_id: UUID) -> dict[str, Any]:
    """Every update of one product, for the Product Manager's Updates tab."""
    from workeasy360.commercial.catalog import selectors
    from workeasy360.commercial.catalog.models import Product

    authz.require(ctx, "platform.staff_catalog.view")
    product = _one(Product.objects.all(), id=product_id)
    return {
        "updates": selectors.product_updates(product.id),
        "approved_hosts": sorted(approved_landing_hosts(product)),
        "images": [
            m
            for m in selectors.media_rows([product.id])[str(product.id)]
            if m["kind"] != ProductMediaKind.LOGO.value
        ],
    }


@in_scope
def create_update(ctx: TenantContext, *, product_id: UUID, body: dict[str, Any]) -> Any:
    """Write a draft update of one product."""
    from workeasy360.commercial.catalog.models import Product, ProductUpdate

    _authorise(ctx, "catalog.product.update_created")
    with transaction.atomic():
        product = _one(Product.objects.select_for_update(), id=product_id)
        if product.status == ProductStatus.RETIRED.value:
            raise CatalogValidationError(
                "A retired product cannot announce updates.", field="product"
            )
        fields = _clean(product, body, creating=True)
        if ProductUpdate.objects.filter(product_id=product.id).count() >= (
            MAX_UPDATES_PER_PRODUCT
        ):
            raise CatalogValidationError(
                f"A product has at most {MAX_UPDATES_PER_PRODUCT} updates.",
                field="product",
            )
        try:
            with transaction.atomic():
                row = ProductUpdate.objects.create(
                    product_id=product.id, created_by_id=ctx.user_id, **fields
                )
        except IntegrityError:
            raise CatalogValidationError(
                "This product already has an update at that address.", field="slug"
            ) from None
        _audit(ctx, row, "commercial.product_update.created", fields)
    return row


@in_scope
def edit_update(ctx: TenantContext, *, update_id: UUID, body: dict[str, Any]) -> Any:
    """Change an update's content. A published update may be edited - visitors
    read the new copy - but its address (``slug``) is fixed once published,
    because partners have shared it."""
    from workeasy360.commercial.catalog.models import ProductUpdate

    _authorise(ctx, "catalog.product.update_edited")
    with transaction.atomic():
        row = _one(
            ProductUpdate.objects.select_for_update().select_related("product"),
            id=update_id,
        )
        if row.status == ProductUpdateStatus.ARCHIVED.value:
            raise CatalogValidationError(
                "An archived update cannot be edited.", field="status"
            )
        fields = _clean(row.product, body, creating=False)
        if (
            "slug" in fields
            and fields["slug"] != row.slug
            and row.status != ProductUpdateStatus.DRAFT.value
        ):
            raise CatalogValidationError(
                "The address of a published update cannot change.", field="slug"
            )
        changed = {k: v for k, v in fields.items() if getattr(row, k) != v}
        if not changed:
            return row
        for key, value in changed.items():
            setattr(row, key, value)
        try:
            with transaction.atomic():
                row.save(update_fields=[*changed, "updated_at"])
        except IntegrityError:
            raise CatalogValidationError(
                "This product already has an update at that address.", field="slug"
            ) from None
        _audit(ctx, row, "commercial.product_update.edited", changed)
    return row


@in_scope
def publish_update(ctx: TenantContext, *, update_id: UUID) -> Any:
    """Publish a draft: it gets its public page and becomes linkable."""
    return _move(
        ctx,
        update_id,
        frm=ProductUpdateStatus.DRAFT,
        to=ProductUpdateStatus.PUBLISHED,
        action="commercial.product_update.published",
    )


@in_scope
def archive_update(ctx: TenantContext, *, update_id: UUID) -> Any:
    """Archive an update: no new links, no public page. Existing links keep
    their attribution and open the product page instead."""
    return _move(
        ctx,
        update_id,
        frm=None,
        to=ProductUpdateStatus.ARCHIVED,
        action="commercial.product_update.archived",
    )


def _move(
    ctx: TenantContext,
    update_id: UUID,
    *,
    frm: ProductUpdateStatus | None,
    to: ProductUpdateStatus,
    action: str,
) -> Any:
    from workeasy360.commercial.catalog.models import ProductUpdate

    _authorise(ctx, action)
    with transaction.atomic():
        row = _one(ProductUpdate.objects.select_for_update(), id=update_id)
        if row.status == to.value:
            return row
        if (frm is not None and row.status != frm.value) or (
            row.status == ProductUpdateStatus.ARCHIVED.value
        ):
            raise CatalogValidationError(
                f"A {row.status} update cannot be {to.value}.", field="status"
            )
        now = timezone.now()
        row.status = to.value
        if to is ProductUpdateStatus.PUBLISHED:
            row.published_at = now
        else:
            row.archived_at = now
        row.save(update_fields=["status", "published_at", "archived_at", "updated_at"])
        _audit(ctx, row, action, {"status": to.value})
    return row


def _audit(ctx: TenantContext, row: Any, action: str, changes: dict[str, Any]) -> None:
    audit.record(
        ctx,
        action,
        resource_type="catalog.product_update",
        resource_id=row.id,
        changes={
            "product_id": str(row.product_id),
            **{k: str(v) if isinstance(v, UUID) else v for k, v in changes.items()},
            # The promotional text is content, not a fact: its size is enough.
            **(
                {"body": f"{len(changes['body'])} characters"}
                if "body" in changes
                else {}
            ),
        },
    )
