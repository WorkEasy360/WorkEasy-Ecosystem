"""Catalog read queries, returning DTOs.

Selectors are the catalog's contract; models are an implementation detail.
``commercial/subscriptions`` and ``commercial/provisioning`` read through
these and never touch catalog tables, so a schema change here breaks in this
module's tests rather than silently in theirs (F-6).

DTOs are frozen dataclasses rather than model instances for a second reason:
handing out a live model lets a caller save it, which would put catalog writes
outside the services that enforce immutability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Prefetch

from workeasy360.contracts.enums import (
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    PlanBillingType,
    PlanKind,
)
from workeasy360.contracts.errors import CatalogValidationError


@dataclass(frozen=True, slots=True)
class FeatureGrantDTO:
    """One feature and the value a plan version grants for it."""

    key: str
    kind: FeatureKind
    value: Any


@dataclass(frozen=True, slots=True)
class PlanVersionDTO:
    """Everything provisioning needs about a purchased plan version.

    Flattened on purpose: this is read once per subscription item during
    entitlement computation, and a DTO that required further lookups would
    turn one reconciliation into N queries.
    """

    id: UUID
    plan_id: UUID
    plan_code: str
    plan_name: str
    plan_status: CatalogStatus
    product_code: str
    product_name: str
    licensing_mode: LicensingMode
    version: int
    published_at: datetime | None
    effective_from: datetime
    effective_to: datetime | None
    included_seats: int
    max_seats: int | None
    trial_days: int
    features: tuple[FeatureGrantDTO, ...]
    #: Phase 8 extension - how the plan is sold (defaults keep older
    #: constructors valid).
    billing_type: PlanBillingType = PlanBillingType.PAID
    plan_kind: PlanKind = PlanKind.STANDARD
    #: For an add-on, the product the subscription must already hold.
    addon_for_code: str | None = None

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    def feature_map(self) -> dict[str, Any]:
        return {f.key: f.value for f in self.features}


@dataclass(frozen=True, slots=True)
class BundleItemDTO:
    plan_version_id: UUID
    plan_code: str
    product_code: str
    quantity: int
    is_optional: bool
    seat_pool: str


@dataclass(frozen=True, slots=True)
class BundleVersionDTO:
    id: UUID
    bundle_id: UUID
    bundle_code: str
    bundle_name: str
    version: int
    published_at: datetime | None
    items: tuple[BundleItemDTO, ...]
    billing_type: PlanBillingType = PlanBillingType.PAID
    trial_days: int = 0


def _plan_version_queryset():
    """The one query shape used by every plan-version read.

    ``select_related`` for the two forward relations and a single prefetch
    for the features: without them, reading ten plan versions is thirty-plus
    queries, and the cost lands inside entitlement provisioning where it is
    least visible. Pinned by ``test_plan_version_lookup_is_bounded``.
    """
    from workeasy360.commercial.catalog.models import PlanFeature, PlanVersion

    return PlanVersion.objects.select_related(
        "plan", "plan__product", "plan__addon_for"
    ).prefetch_related(
        Prefetch(
            "plan_features",
            queryset=PlanFeature.objects.select_related("feature").order_by(
                "feature__key"
            ),
        )
    )


def _to_plan_version_dto(row: Any) -> PlanVersionDTO:
    return PlanVersionDTO(
        id=row.id,
        plan_id=row.plan_id,
        plan_code=row.plan.code,
        plan_name=row.plan.name,
        plan_status=CatalogStatus(row.plan.status),
        product_code=row.plan.product.code,
        product_name=row.plan.product.name,
        licensing_mode=LicensingMode(row.plan.product.licensing_mode),
        version=row.version,
        published_at=row.published_at,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        included_seats=row.included_seats,
        max_seats=row.max_seats,
        trial_days=row.trial_days,
        features=tuple(
            FeatureGrantDTO(
                key=pf.feature.key,
                kind=FeatureKind(pf.feature.kind),
                value=pf.value,
            )
            for pf in row.plan_features.all()
        ),
        billing_type=PlanBillingType(row.plan.billing_type),
        plan_kind=PlanKind(row.plan.kind),
        addon_for_code=row.plan.addon_for.code if row.plan.addon_for_id else None,
    )


def plan_version(plan_version_id: UUID) -> PlanVersionDTO | None:
    row = _plan_version_queryset().filter(id=plan_version_id).first()
    return _to_plan_version_dto(row) if row is not None else None


def plan_versions(plan_version_ids: list[UUID]) -> dict[UUID, PlanVersionDTO]:
    """Batch read, keyed by id.

    Batch-only because the caller is always provisioning, which holds a
    subscription's worth of ids at once. A per-id API here would read as
    innocent and produce an N+1 inside the reconciliation loop (D7).
    """
    if not plan_version_ids:
        return {}
    rows = _plan_version_queryset().filter(id__in=set(plan_version_ids))
    return {row.id: _to_plan_version_dto(row) for row in rows}


def current_plan_version(plan_code: str, *, at: datetime) -> PlanVersionDTO | None:
    """The published version of a plan in force at ``at``.

    Only published versions are candidates: a draft exists so that it can be
    reviewed before it binds anyone, and selling one would defeat that.
    """
    from django.db.models import Q

    row = (
        _plan_version_queryset()
        .filter(plan__code=plan_code, published_at__isnull=False, effective_from__lte=at)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        .order_by("-version")
        .first()
    )
    return _to_plan_version_dto(row) if row is not None else None


@dataclass(frozen=True, slots=True)
class TrialOfferDTO:
    """What staff configured for one product's trial (Phase 11), and whether
    the product is on sale. No defaults: a product with no configuration has no
    offer at all (``trial_offer`` returns ``None``)."""

    product_code: str
    product_name: str
    enabled: bool
    on_sale: bool
    duration_days: int
    seat_limit: int | None
    eligible_plan_codes: tuple[str, ...]


def trial_offer(product_code: str) -> TrialOfferDTO | None:
    """The trial configuration of one product, or ``None`` if it has none or
    the product does not exist. Two rows, one query."""
    from workeasy360.commercial.catalog.models import ProductTrialConfig
    from workeasy360.contracts.enums import ProductListing, ProductStatus

    row = (
        ProductTrialConfig.objects.select_related("product")
        .filter(product__code=str(product_code))
        .first()
    )
    if row is None:
        return None
    product = row.product
    return TrialOfferDTO(
        product_code=product.code,
        product_name=product.name,
        enabled=row.enabled,
        on_sale=(
            product.status == ProductStatus.AVAILABLE.value
            and product.listing == ProductListing.LISTED.value
        ),
        duration_days=row.duration_days,
        seat_limit=row.seat_limit,
        eligible_plan_codes=tuple(row.eligible_plan_codes),
    )


def public_plan_version(
    product_code: str, plan_code: str, *, at: datetime
) -> PlanVersionDTO | None:
    """The version in force of a **standard, public, available** plan of this
    product - the plans a visitor can see on the public site - or ``None``.

    The same test ``marketplace_offers`` applies, so a trial can never start on
    a plan the storefront would not show.
    """
    from django.db.models import Q

    row = (
        _plan_version_queryset()
        .filter(
            plan__code=plan_code,
            plan__product__code=str(product_code),
            plan__status=CatalogStatus.AVAILABLE.value,
            plan__is_public=True,
            plan__kind=PlanKind.STANDARD.value,
            published_at__isnull=False,
            effective_from__lte=at,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        .order_by("-version")
        .first()
    )
    return _to_plan_version_dto(row) if row is not None else None


def require_plan_version(plan_version_id: UUID) -> PlanVersionDTO:
    dto = plan_version(plan_version_id)
    if dto is None:
        raise CatalogValidationError(
            f"Plan version {plan_version_id} does not exist.", field="plan_version_id"
        )
    return dto


def bundle_version(bundle_version_id: UUID) -> BundleVersionDTO | None:
    """One bundle version with its items, in two queries.

    Bundle resolution happens on the purchase path, so its query count is
    pinned by ``test_bundle_resolution_is_bounded``.
    """
    from workeasy360.commercial.catalog.models import BundleItem, BundleVersion

    row = (
        BundleVersion.objects.select_related("bundle")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=BundleItem.objects.select_related(
                    "plan_version__plan__product"
                ).order_by("plan_version__plan__code"),
            )
        )
        .filter(id=bundle_version_id)
        .first()
    )
    if row is None:
        return None

    return BundleVersionDTO(
        id=row.id,
        bundle_id=row.bundle_id,
        bundle_code=row.bundle.code,
        bundle_name=row.bundle.name,
        version=row.version,
        published_at=row.published_at,
        billing_type=PlanBillingType(row.bundle.billing_type),
        trial_days=row.trial_days,
        items=tuple(
            BundleItemDTO(
                plan_version_id=item.plan_version_id,
                plan_code=item.plan_version.plan.code,
                product_code=item.plan_version.plan.product.code,
                quantity=item.quantity,
                is_optional=item.is_optional,
                seat_pool=item.seat_pool,
            )
            for item in row.items.all()
        ),
    )


def require_bundle_version(bundle_version_id: UUID) -> BundleVersionDTO:
    dto = bundle_version(bundle_version_id)
    if dto is None:
        raise CatalogValidationError(
            f"Bundle version {bundle_version_id} does not exist.",
            field="bundle_version_id",
        )
    return dto


def visible_products() -> list[dict[str, Any]]:
    """Every product, for the staff catalog screens - drafts, deprecated and
    retired included. Customers never read this: the public site and Explore
    read ``public_products`` / ``marketplace_offers`` (Phase 9 removed an
    ``include_unavailable`` switch no caller ever turned off)."""
    from workeasy360.commercial.catalog.models import Product

    return [
        product_row(p)
        for p in Product.objects.select_related("trial_config").order_by(
            "sort_order", "code"
        )
    ]


def trial_row(product: Any) -> dict[str, Any] | None:
    """One product's trial configuration as staff see it, or ``None`` when
    nothing was ever configured (no default exists). Read from the row the
    caller already joined (``select_related("trial_config")``)."""
    # A missing reverse one-to-one raises RelatedObjectDoesNotExist, which is
    # an AttributeError, so the default answers "never configured".
    config = getattr(product, "trial_config", None)
    if config is None:
        return None
    return {
        "enabled": config.enabled,
        "duration_days": config.duration_days,
        "seat_limit": config.seat_limit,
        "payment_method_required": config.payment_method_required,
        "eligible_plan_codes": list(config.eligible_plan_codes),
    }


def product_row(product: Any) -> dict[str, Any]:
    """One product as the catalog screens show it, with its derived public
    availability (Phase 9)."""
    return {
        "id": product.id,
        "code": product.code,
        "name": product.name,
        "description": product.description,
        "status": product.status,
        "licensing_mode": product.licensing_mode,
        "sort_order": product.sort_order,
        "slug": product.slug,
        "tagline": product.tagline,
        "marketing_url": product.marketing_url,
        "listing": product.listing,
        "availability": product.availability.value,
        "category": product.category,
        "icon": product.icon,
        "trial": trial_row(product),
    }


def product_presentations(codes: list[str]) -> dict[str, dict[str, str]]:
    """How each of these products draws a card: slug, tagline, category, icon.
    One query, for the app directory (which renders a card per product)."""
    from workeasy360.commercial.catalog.models import Product

    if not codes:
        return {}
    return {
        row["code"]: row
        for row in Product.objects.filter(code__in=set(codes)).values(
            "code", "slug", "tagline", "category", "icon"
        )
    }


def resolve_product_and_plan(product_code: str, plan_code: str | None) -> tuple[Any, Any]:
    """Resolve a product code (and optionally one of its plans) to ids.

    For a caller that holds a *code* from an operator and must store a
    reference. Raises ``CatalogValidationError`` - one shape for "no such
    product", "no such plan" and "that plan belongs to another product", so
    the caller learns nothing beyond "not that".
    """
    from workeasy360.commercial.catalog.models import Plan, Product
    from workeasy360.contracts.errors import CatalogValidationError

    product_id = (
        Product.objects.filter(code=product_code).values_list("id", flat=True).first()
    )
    if product_id is None:
        raise CatalogValidationError("No such product.", field="product_code")
    if not plan_code:
        return product_id, None
    plan_id = (
        Plan.objects.filter(code=plan_code, product_id=product_id)
        .values_list("id", flat=True)
        .first()
    )
    if plan_id is None:
        raise CatalogValidationError("No such plan for that product.", field="plan_code")
    return product_id, plan_id


def sellable_plans(product_codes: list[str], *, at: Any) -> list[dict[str, Any]]:
    """Live, published plan versions of the given products - for a price list.

    Each row carries the plan version id a purchase command takes. Draft,
    retired and unpublished versions are excluded, so a price list cannot
    offer what ``purchase_plan`` would refuse.
    """
    from django.db.models import Q

    from workeasy360.commercial.catalog.models import PlanVersion
    from workeasy360.contracts.enums import CatalogStatus

    if not product_codes:
        return []
    rows = (
        PlanVersion.objects.filter(
            plan__product__code__in=product_codes,
            plan__status=CatalogStatus.AVAILABLE.value,
            published_at__isnull=False,
            effective_from__lte=at,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        .select_related("plan", "plan__product")
        .order_by("plan__product__code", "plan__code", "-version")
    )
    seen: set[Any] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.plan_id in seen:
            continue
        seen.add(row.plan_id)
        result.append(
            {
                "plan_version_id": row.id,
                "plan_code": row.plan.code,
                "plan_name": row.plan.name,
                "product_code": row.plan.product.code,
                "product_name": row.plan.product.name,
                "version": row.version,
                "included_seats": row.included_seats,
                "trial_days": row.trial_days,
            }
        )
    return result


# --- Phase 8 extension: the plan builder and the marketplace -----------------


def _version_row(v: Any) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "version": v.version,
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "effective_from": v.effective_from.isoformat(),
        "effective_to": v.effective_to.isoformat() if v.effective_to else None,
        "included_seats": v.included_seats,
        "max_seats": v.max_seats,
        "trial_days": v.trial_days,
        "features": {pf.feature.key: pf.value for pf in v.plan_features.all()},
    }


def plan_listing(*, product_code: str | None = None) -> list[dict[str, Any]]:
    """Every plan with its versions - the plan builder's "product packages".
    Three queries whatever the number of plans."""
    from workeasy360.commercial.catalog.models import Plan, PlanFeature, PlanVersion

    plans = Plan.objects.select_related("product", "addon_for").prefetch_related(
        Prefetch(
            "versions",
            queryset=PlanVersion.objects.order_by("-version").prefetch_related(
                Prefetch(
                    "plan_features",
                    queryset=PlanFeature.objects.select_related("feature"),
                )
            ),
        )
    )
    if product_code:
        plans = plans.filter(product__code=product_code)
    return [
        {
            "id": str(p.id),
            "code": p.code,
            "name": p.name,
            "caption": p.caption,
            "description": p.description,
            "status": p.status,
            "is_public": p.is_public,
            "sort_order": p.sort_order,
            "billing_type": p.billing_type,
            "kind": p.kind,
            "product": {
                "id": str(p.product_id),
                "code": p.product.code,
                "name": p.product.name,
            },
            "addon_for": {"id": str(p.addon_for_id), "code": p.addon_for.code}
            if p.addon_for_id
            else None,
            "versions": [_version_row(v) for v in p.versions.all()],
        }
        for p in plans.order_by(
            "product__sort_order", "product__code", "sort_order", "code"
        )[:500]
    ]


def bundle_listing() -> list[dict[str, Any]]:
    """Every bundle with its versions and items - the plan builder's "plans"."""
    from workeasy360.commercial.catalog.models import Bundle, BundleItem, BundleVersion

    bundles = Bundle.objects.prefetch_related(
        Prefetch(
            "versions",
            queryset=BundleVersion.objects.order_by("-version").prefetch_related(
                Prefetch(
                    "items",
                    queryset=BundleItem.objects.select_related(
                        "plan_version__plan__product"
                    ).order_by("plan_version__plan__product__code"),
                )
            ),
        )
    )
    return [
        {
            "id": str(b.id),
            "code": b.code,
            "name": b.name,
            "caption": b.caption,
            "description": b.description,
            "status": b.status,
            "is_public": b.is_public,
            "kind": b.kind,
            "billing_type": b.billing_type,
            "versions": [
                {
                    "id": str(v.id),
                    "version": v.version,
                    "published_at": v.published_at.isoformat()
                    if v.published_at
                    else None,
                    "effective_to": v.effective_to.isoformat()
                    if v.effective_to
                    else None,
                    "trial_days": v.trial_days,
                    "items": [
                        {
                            "plan_version_id": str(i.plan_version_id),
                            "plan_code": i.plan_version.plan.code,
                            "plan_name": i.plan_version.plan.name,
                            "plan_version": i.plan_version.version,
                            "product_code": i.plan_version.plan.product.code,
                            "product_name": i.plan_version.plan.product.name,
                            "quantity": i.quantity,
                            "is_optional": i.is_optional,
                        }
                        for i in v.items.all()
                    ],
                }
                for v in b.versions.all()
            ],
        }
        for b in bundles.order_by("code")[:500]
    ]


def features_for_product(product_id: UUID) -> list[dict[str, Any]]:
    from workeasy360.commercial.catalog.models import Feature

    return [
        {
            "id": str(f.id),
            "key": f.key,
            "name": f.name,
            "kind": f.kind,
            "unit": f.unit,
            "default_value": f.default_value,
            "enum_ordering": f.enum_ordering,
        }
        for f in Feature.objects.filter(product_id=product_id).order_by("key")
    ]


def marketplace_offers(*, at: Any) -> dict[str, list[dict[str, Any]]]:
    """What a customer can buy right now: *public, available* plans and
    bundles of sellable, **listed** products, each at its version in force.
    Draft, private, grandfathered and retired offers are never listed, and
    neither is a bundle any of whose products is no longer on sale or no
    longer listed (Phase 9) - the same set a self-serve order accepts."""
    from django.db.models import Q

    from workeasy360.commercial.catalog.models import BundleVersion, PlanVersion
    from workeasy360.contracts.enums import ProductListing, ProductStatus

    in_force = Q(published_at__isnull=False, effective_from__lte=at) & (
        Q(effective_to__isnull=True) | Q(effective_to__gt=at)
    )
    plan_rows = (
        PlanVersion.objects.filter(in_force)
        .filter(
            plan__status=CatalogStatus.AVAILABLE.value,
            plan__is_public=True,
            plan__product__status=ProductStatus.AVAILABLE.value,
            plan__product__listing=ProductListing.LISTED.value,
        )
        .select_related("plan", "plan__product", "plan__addon_for")
        .order_by(
            "plan__product__sort_order", "plan__sort_order", "plan__code", "-version"
        )
    )
    plans: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for v in plan_rows:
        if v.plan_id in seen:
            continue
        seen.add(v.plan_id)
        plans.append(
            {
                "plan_version_id": str(v.id),
                "plan_code": v.plan.code,
                "name": v.plan.name,
                "caption": v.plan.caption,
                "description": v.plan.description,
                "billing_type": v.plan.billing_type,
                "kind": v.plan.kind,
                "addon_for": v.plan.addon_for.code if v.plan.addon_for_id else None,
                "product_code": v.plan.product.code,
                "product_name": v.plan.product.name,
                "product_slug": v.plan.product.slug,
                "included_seats": v.included_seats,
                "max_seats": v.max_seats,
                "trial_days": v.trial_days,
            }
        )
    bundle_rows = (
        BundleVersion.objects.filter(in_force)
        .filter(bundle__status=CatalogStatus.AVAILABLE.value, bundle__is_public=True)
        .select_related("bundle")
        .prefetch_related("items__plan_version__plan__product")
        .order_by("bundle__code", "-version")
    )
    bundles: list[dict[str, Any]] = []
    seen = set()
    for bv in bundle_rows:
        if bv.bundle_id in seen:
            continue
        seen.add(bv.bundle_id)
        products = [i.plan_version.plan.product for i in bv.items.all()]
        if not products or any(
            p.status != ProductStatus.AVAILABLE.value
            or p.listing != ProductListing.LISTED.value
            for p in products
        ):
            # A bundle that would sell a withdrawn or private product is not
            # an offer, whatever its own status says.
            continue
        bundles.append(
            {
                "bundle_version_id": str(bv.id),
                "bundle_code": bv.bundle.code,
                "name": bv.bundle.name,
                "caption": bv.bundle.caption,
                "description": bv.bundle.description,
                "billing_type": bv.bundle.billing_type,
                "trial_days": bv.trial_days,
                "includes": [
                    {
                        "product_code": i.plan_version.plan.product.code,
                        "product_name": i.plan_version.plan.product.name,
                        "plan_name": i.plan_version.plan.name,
                        "quantity": i.quantity,
                    }
                    for i in bv.items.all()
                ],
            }
        )
    return {"plans": plans, "bundles": bundles}


def public_products() -> list[dict[str, Any]]:
    """The products a visitor may see (Phase 9): listed ones on sale and
    announced ones marked coming soon, in display order, each with its public
    features. Two queries whatever the catalog's size. Private, draft,
    deprecated and retired products are never returned."""
    from django.db.models import Prefetch, Q

    from workeasy360.commercial.catalog.models import Feature, Product
    from workeasy360.contracts.enums import ProductListing, ProductStatus

    rows = (
        Product.objects.filter(
            Q(
                listing=ProductListing.LISTED.value,
                status=ProductStatus.AVAILABLE.value,
            )
            | Q(
                listing=ProductListing.COMING_SOON.value,
                status__in=[ProductStatus.DRAFT.value, ProductStatus.AVAILABLE.value],
            )
        )
        .select_related("trial_config")
        .prefetch_related(
            Prefetch(
                "features",
                queryset=Feature.objects.filter(is_public=True).order_by("name"),
                to_attr="public_features",
            )
        )
        .order_by("sort_order", "code")
    )
    return [
        {
            "code": p.code,
            "slug": p.slug,
            "name": p.name,
            "tagline": p.tagline,
            "description": p.description,
            "marketing_url": p.marketing_url,
            "availability": p.availability.value,
            "category": p.category,
            "icon": p.icon,
            "trial_config": trial_row(p),
            "features": [
                {"name": f.name, "description": f.description} for f in p.public_features
            ],
        }
        for p in rows
    ]
