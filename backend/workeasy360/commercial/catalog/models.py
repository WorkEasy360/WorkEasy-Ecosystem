"""The product catalog - control plane, versioned, immutable once published.

Every table here is **global reference data**: no ``organization_id``, no RLS,
no tenant manager. Anything customer-specific is a separate, org-scoped entity
in ``commercial/pricing`` (``ContractPrice``) or ``commercial/subscriptions``.
That plane separation is what makes it impossible for a bug in catalog
querying to leak one customer's negotiated terms to another
(00-system-context.md, 05-commercial-model.md section 2).

Two invariants carry most of the weight:

**Identity is a code, never a name.** ``Product.code`` and ``Plan.code`` are
the stable machine identifiers. Names are display strings and change freely.
Nothing in the system may key off a name.

**Published versions are immutable.** Editing a live plan retroactively
changes what every existing customer bought, with no record of the original
agreement. Changes publish a new version; existing subscriptions keep the
version they pinned. Enforced by database triggers as well as by the
services, because the application guard is bypassed by a data-fix script
(F5, ADR-006).
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models

from workeasy360.contracts.enums import (
    BundleKind,
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    ProductStatus,
)


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(member.value, member.name.replace("_", " ").title()) for member in enum_cls]


class Product(models.Model):
    """A sellable SKU. Not a code module.

    ``code`` is a validated string that *resolves to* a product module at
    runtime, never a foreign key into one. An FK would make catalog depend on
    every product package and would turn adding a product into a catalog
    migration (01-domain-boundaries.md section 4). It also lets commercial
    sell a product before its code is deployed, which is how launches work.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: ``crm``, ``books``. Validated against ``ProductCode`` in the service.
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=16,
        choices=_choices(ProductStatus),
        default=ProductStatus.DRAFT.value,
        db_index=True,
    )
    licensing_mode = models.CharField(
        max_length=20,
        choices=_choices(LicensingMode),
        default=LicensingMode.PER_SEAT.value,
    )
    sort_order = models.IntegerField(default=100)

    #: Lifecycle metadata as timestamps rather than inferred from ``status``,
    #: so "when did this become sellable?" survives a later status change and
    #: is answerable without reading the audit log.
    activated_at = models.DateTimeField(null=True, blank=True)
    deprecated_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_product"
        ordering = ["sort_order", "code"]
        indexes = [
            models.Index(fields=["status", "sort_order"], name="ix_product_status")
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.code} ({self.status})"

    @property
    def status_enum(self) -> ProductStatus:
        return ProductStatus(self.status)


class Feature(models.Model):
    """A sellable capability - the unit the runtime actually checks.

    ``key`` is ``<product>.<area>.<name>``, and its leading segment must equal
    the owning product's code. A mistyped feature key silently grants or
    denies paid access, so the format is validated in one place and asserted
    at the service boundary.

    ``key`` and ``kind`` are immutable after creation. A published plan
    references a feature and states a value for it; changing the feature's
    kind underneath would change what every such plan means without touching
    a single plan row.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="features"
    )
    key = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")

    kind = models.CharField(max_length=16, choices=_choices(FeatureKind))
    #: ``pipelines``, ``GB``, ``emails/month``. Display only.
    unit = models.CharField(max_length=32, blank=True, default="")
    #: The value when **not** entitled. Makes "off" explicit rather than
    #: leaving every caller to invent a default.
    default_value = models.JSONField(default=bool)
    #: Enum features declare their ordering, least to most generous, so
    #: most-generous-wins resolution has something to rank by.
    enum_ordering = models.JSONField(default=list, blank=True)
    is_public = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_feature"
        ordering = ["key"]
        indexes = [models.Index(fields=["product", "key"], name="ix_feature_product")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.key

    @property
    def kind_enum(self) -> FeatureKind:
        return FeatureKind(self.kind)


class Plan(models.Model):
    """Stable plan identity. All commercial terms live on its versions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="plans")
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=16,
        choices=_choices(CatalogStatus),
        default=CatalogStatus.DRAFT.value,
        db_index=True,
    )
    #: False = partner or enterprise only. Not a security control: it governs
    #: listing, and purchase is authorised separately.
    is_public = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_plan"
        ordering = ["product", "sort_order", "code"]
        indexes = [models.Index(fields=["product", "status"], name="ix_plan_prod_status")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.code} ({self.status})"

    @property
    def status_enum(self) -> CatalogStatus:
        return CatalogStatus(self.status)


class PlanVersion(models.Model):
    """Immutable once published. This is where versioning earns its keep.

    Without it, "raise the Professional pipeline limit from 10 to 25"
    silently changes every existing customer's entitlements - including
    customers on a grandfathered arrangement - with no record of what they
    originally bought. With it: publish v2, new subscriptions get v2,
    existing subscriptions keep v1 until an explicit, auditable migration.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()

    #: NULL while draft. Setting it is a one-way door enforced by a trigger.
    published_at = models.DateTimeField(null=True, blank=True)
    effective_from = models.DateTimeField()
    #: Set when a later version supersedes this one. The one field a published
    #: version may still change, because that is how supersession is recorded.
    effective_to = models.DateTimeField(null=True, blank=True)

    #: Seat entitlement. NULL ``max_seats`` means unbounded.
    included_seats = models.IntegerField(default=1)
    max_seats = models.IntegerField(null=True, blank=True)
    trial_days = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_plan_version"
        ordering = ["plan", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "version"], name="uq_plan_version"),
            models.CheckConstraint(
                condition=models.Q(included_seats__gte=0),
                name="ck_plan_version_seats_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(max_seats__isnull=True)
                | models.Q(max_seats__gte=models.F("included_seats")),
                name="ck_plan_version_max_seats_ge_included",
            ),
            models.CheckConstraint(
                condition=models.Q(trial_days__gte=0), name="ck_plan_version_trial_days"
            ),
        ]
        indexes = [
            models.Index(fields=["plan", "published_at"], name="ix_planver_published")
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.plan_id} v{self.version}"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None


class PlanFeature(models.Model):
    """What one plan version includes, per feature.

    A row per feature rather than boolean columns on the plan: columns cannot
    express a quota, cannot be compared across plans on a pricing page, and
    every new capability would be a migration.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    plan_version = models.ForeignKey(
        PlanVersion, on_delete=models.CASCADE, related_name="plan_features"
    )
    feature = models.ForeignKey(
        Feature, on_delete=models.PROTECT, related_name="plan_features"
    )
    #: ``true`` | ``50`` | ``-1`` (unlimited) | ``"priority"``. Validated
    #: against the feature's ``kind`` when the version is published.
    value = models.JSONField()

    class Meta:
        db_table = "catalog_plan_feature"
        constraints = [
            models.UniqueConstraint(
                fields=["plan_version", "feature"], name="uq_plan_feature"
            )
        ]
        indexes = [models.Index(fields=["plan_version"], name="ix_planfeature_version")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.plan_version_id}:{self.feature_id}={self.value}"


class Bundle(models.Model):
    """Composition, priced as one thing.

    Deliberately **tenant-free**, unlike the Phase 0 sketch's
    ``owner_organization`` field. A control-plane table holding a tenant
    reference reintroduces exactly the plane mixing the architecture exists
    to prevent. A custom enterprise bundle is a non-public bundle plus a
    ``ContractPrice`` for that organization - the org-scoped, RLS-protected
    side of the relationship - which is where customer-specific data belongs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")

    kind = models.CharField(
        max_length=16, choices=_choices(BundleKind), default=BundleKind.PREDEFINED.value
    )
    status = models.CharField(
        max_length=16,
        choices=_choices(CatalogStatus),
        default=CatalogStatus.DRAFT.value,
        db_index=True,
    )
    #: Custom bundles are invisible in public listings. Visibility, not
    #: authorisation: purchase is authorised separately.
    is_public = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_bundle"
        ordering = ["code"]
        constraints = [
            # A custom bundle by definition is not publicly listed. Without
            # this, an enterprise package appears on the pricing page.
            models.CheckConstraint(
                condition=~models.Q(kind=BundleKind.CUSTOM.value)
                | models.Q(is_public=False),
                name="ck_custom_bundle_not_public",
            )
        ]
        indexes = [models.Index(fields=["status", "kind"], name="ix_bundle_status_kind")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.code} ({self.status})"

    @property
    def status_enum(self) -> CatalogStatus:
        return CatalogStatus(self.status)


class BundleVersion(models.Model):
    """Immutable once published, for the same reason plan versions are."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    bundle = models.ForeignKey(Bundle, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    published_at = models.DateTimeField(null=True, blank=True)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_bundle_version"
        ordering = ["bundle", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["bundle", "version"], name="uq_bundle_version"
            )
        ]
        indexes = [
            models.Index(fields=["bundle", "published_at"], name="ix_bundlever_published")
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.bundle_id} v{self.version}"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None


class BundleItem(models.Model):
    """One plan version granted by a bundle.

    **Plan versions, not products.** "The Full Suite includes CRM" is
    ambiguous and unpriceable; "includes CRM Professional v3" is not.

    There is no bundle-to-bundle reference here and none on ``Bundle``:
    nested bundles are not supported. Phase 0 does not require them, and
    recursion turns expansion, pricing and commission into algorithms nobody
    can explain to a customer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    bundle_version = models.ForeignKey(
        BundleVersion, on_delete=models.CASCADE, related_name="items"
    )
    plan_version = models.ForeignKey(
        PlanVersion, on_delete=models.PROTECT, related_name="bundle_items"
    )
    quantity = models.IntegerField(default=1)
    #: The customer may decline this line within the bundle.
    is_optional = models.BooleanField(default=False)
    #: Names a shared seat pool, so a bundle can grant 10 seats usable across
    #: CRM and Projects rather than 10 each. Empty means per-product seats,
    #: which is the common case. Modelled now because retrofitting shared
    #: pools into a per-product seat model is a painful migration.
    seat_pool = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "catalog_bundle_item"
        constraints = [
            # A bundle granting the same plan version twice would expand into
            # two identical subscription items and bill twice.
            models.UniqueConstraint(
                fields=["bundle_version", "plan_version"], name="uq_bundle_item"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="ck_bundle_item_quantity"
            ),
        ]
        indexes = [models.Index(fields=["bundle_version"], name="ix_bundleitem_version")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.bundle_version_id} -> {self.plan_version_id}"
