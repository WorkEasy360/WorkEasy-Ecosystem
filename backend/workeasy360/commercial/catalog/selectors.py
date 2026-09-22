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

from workeasy360.contracts.enums import CatalogStatus, FeatureKind, LicensingMode
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


def _plan_version_queryset():
    """The one query shape used by every plan-version read.

    ``select_related`` for the two forward relations and a single prefetch
    for the features: without them, reading ten plan versions is thirty-plus
    queries, and the cost lands inside entitlement provisioning where it is
    least visible. Pinned by ``test_plan_version_lookup_is_bounded``.
    """
    from workeasy360.commercial.catalog.models import PlanFeature, PlanVersion

    return PlanVersion.objects.select_related("plan", "plan__product").prefetch_related(
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


def visible_products(*, include_unavailable: bool = False) -> list[dict[str, Any]]:
    """Catalog listing. Starts from a predicate, never ``.objects.all()`` (G5).

    ``include_unavailable`` is for internal tooling; the customer-facing path
    never passes it, so a draft SKU cannot appear on a pricing page because
    somebody forgot a filter.
    """
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.contracts.enums import ProductStatus

    queryset = Product.objects.all()
    if not include_unavailable:
        queryset = queryset.filter(
            status__in=[ProductStatus.AVAILABLE.value, ProductStatus.DEPRECATED.value]
        )
    return list(
        queryset.values("id", "code", "name", "status", "licensing_mode", "sort_order")
    )
