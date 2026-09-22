"""Catalog application services - the only sanctioned way to write the catalog.

Every function here is a use case, owns its transaction, enforces
authorization and audits. Views and the admin call these; nothing calls the
models directly.

Authorization is two-factor for every mutation:

    authz.require(ctx, "commercial.catalog.manage")      # role
    authz.require_platform_staff(ctx, operation=...)     # internal identity

Either alone is one misconfiguration away from letting a customer edit the
global catalog, which would change what *every* tenant is sold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.catalog.domain import (
    assert_catalog_transition,
    assert_product_transition,
    validate_feature_key,
    validate_feature_value,
    validate_product_code,
)
from workeasy360.contracts.enums import (
    AuditOutcome,
    BundleKind,
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    ProductStatus,
)
from workeasy360.contracts.errors import CatalogValidationError, ImmutableRecord
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

MANAGE = "commercial.catalog.manage"


def _authorise(ctx: TenantContext, operation: str) -> None:
    authz.require(ctx, MANAGE)
    authz.require_platform_staff(ctx, operation=operation)


# --- products -------------------------------------------------------------


def create_product(
    ctx: TenantContext,
    *,
    code: str,
    name: str,
    description: str = "",
    licensing_mode: LicensingMode = LicensingMode.PER_SEAT,
    sort_order: int = 100,
) -> Any:
    """Register a sellable SKU. Starts in ``draft`` - never sellable on
    creation, because a product with no plan and no price is not a product."""
    from workeasy360.commercial.catalog.models import Product

    _authorise(ctx, "catalog.product.create")
    validated = validate_product_code(code)

    with transaction.atomic():
        if Product.objects.filter(code=str(validated)).exists():
            raise CatalogValidationError(
                f"Product code {code!r} is already in use. Codes are stable "
                f"machine identifiers and are never reused.",
                field="code",
            )
        product = Product.objects.create(
            code=str(validated),
            name=name,
            description=description,
            licensing_mode=licensing_mode.value,
            sort_order=sort_order,
        )
        audit.record(
            ctx,
            "commercial.product.created",
            resource_type="catalog.product",
            resource_id=product.id,
            changes={"code": product.code, "licensing_mode": product.licensing_mode},
        )
    return product


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
        product = Product.objects.select_for_update().get(id=product_id)
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

        audit.record(
            ctx,
            "commercial.product.status_changed",
            resource_type="catalog.product",
            resource_id=product.id,
            changes={"from": current.value, "to": status.value},
        )
    return product


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
        product = Product.objects.get(id=product_id)
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
        feature = Feature.objects.select_for_update().get(id=feature_id)
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
) -> Any:
    from workeasy360.commercial.catalog.models import Plan, Product

    _authorise(ctx, "catalog.plan.create")

    with transaction.atomic():
        product = Product.objects.get(id=product_id)
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
        )
        audit.record(
            ctx,
            "commercial.plan.created",
            resource_type="catalog.plan",
            resource_id=plan.id,
            changes={"code": plan.code, "product": product.code},
        )
    return plan


def set_plan_status(ctx: TenantContext, *, plan_id: UUID, status: CatalogStatus) -> Any:
    from workeasy360.commercial.catalog.models import Plan

    _authorise(ctx, "catalog.plan.status")

    with transaction.atomic():
        plan = Plan.objects.select_for_update().get(id=plan_id)
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
        plan = Plan.objects.select_for_update().select_related("product").get(id=plan_id)

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
        version = (
            PlanVersion.objects.select_for_update()
            .select_related("plan", "plan__product")
            .get(id=plan_version_id)
        )
        if version.is_published:
            raise ImmutableRecord(
                f"Plan version {version.plan.code} v{version.version} is "
                f"already published. Publish a new version instead.",
                resource="catalog.plan_version",
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
) -> Any:
    from workeasy360.commercial.catalog.models import Bundle

    _authorise(ctx, "catalog.bundle.create")

    with transaction.atomic():
        if Bundle.objects.filter(code=code).exists():
            raise CatalogValidationError(
                f"Bundle code {code!r} is already in use.", field="code"
            )
        bundle = Bundle.objects.create(
            code=code,
            name=name,
            kind=kind.value,
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

    with transaction.atomic():
        bundle = Bundle.objects.select_for_update().get(id=bundle_id)

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
        version = (
            BundleVersion.objects.select_for_update()
            .select_related("bundle")
            .get(id=bundle_version_id)
        )
        if version.is_published:
            raise ImmutableRecord(
                f"Bundle version {version.bundle.code} v{version.version} is "
                f"already published.",
                resource="catalog.bundle_version",
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
        bundle = Bundle.objects.select_for_update().get(id=bundle_id)
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
