"""The entitlement read model.

Derived, rebuildable, and optimised for the question products ask thousands of
times per second. ``commercial/provisioning`` is the only module permitted to
write these tables (ADR-005); the linter enforces that nothing else imports
``services.write``.

Nothing here knows about plans, prices, bundles or partners. That is the whole
point: a product asks "may this organization use this?" and the answer does
not leak how it was bought.
"""

from __future__ import annotations

from django.db import models

from workeasy360.contracts.enums import (
    EntitlementSourceKind,
    EntitlementStatus,
    FeatureKind,
)
from workeasy360.platform.tenancy.models import TenantScopedModel


class Entitlement(TenantScopedModel):
    """One organization's access to one product, from one source.

    An organization can hold the same product from several sources at once -
    a bundle, a standalone subscription and a support grant - so this is not
    unique on (org, product). Resolution across sources is "most generous
    wins" (06-entitlements.md section 4.1).
    """

    product_code = models.CharField(max_length=32, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in EntitlementStatus],
        default=EntitlementStatus.TRIAL.value,
    )
    source_kind = models.CharField(
        max_length=16,
        choices=[(k.value, k.name.title()) for k in EntitlementSourceKind],
    )
    #: A soft reference ("subscriptions.subscription:<uuid>"), never a foreign
    #: key: platform must not depend on commercial (F-4).
    source_ref = models.CharField(max_length=255)

    #: NULL means not seat-limited.
    seats_total = models.IntegerField(null=True, blank=True)

    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    grace_ends_at = models.DateTimeField(null=True, blank=True)

    computed_at = models.DateTimeField()
    #: Groups one reconciliation run, so a drift investigation can isolate
    #: exactly which pass produced a row.
    computation_id = models.UUIDField()

    class Meta:
        db_table = "entitlements_entitlement"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "product_code", "source_ref"],
                name="uq_entitlement_org_product_source",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "product_code"], name="ix_ent_org_product"
            ),
            models.Index(fields=["organization", "status"], name="ix_ent_org_status"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.organization_id}:{self.product_code}={self.status}"

    @property
    def status_enum(self) -> EntitlementStatus:
        return EntitlementStatus(self.status)

    def is_effective_at(self, moment) -> bool:
        if self.effective_from > moment:
            return False
        return self.effective_to is None or self.effective_to > moment


class FeatureEntitlement(TenantScopedModel):
    """A resolved feature value for one organization, from one source."""

    feature_key = models.CharField(max_length=128, db_index=True)
    #: ``true`` | ``50`` | ``-1`` (unlimited) | ``"priority"``
    value = models.JSONField()
    kind = models.CharField(
        max_length=16, choices=[(k.value, k.name.title()) for k in FeatureKind]
    )
    source_ref = models.CharField(max_length=255)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "entitlements_feature"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "feature_key", "source_ref"],
                name="uq_feature_org_key_source",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "feature_key"], name="ix_feat_org_key")
        ]


class EntitlementSnapshot(TenantScopedModel):
    """The materialised answer, one row per organization.

    This is what the cache is built from, and what a support engineer reads to
    answer "what does this customer actually have?" in one query rather than
    five joins.

    ``version`` is monotonic and bumped on every recompute. Caches are keyed by
    it, so invalidation is a counter increment with no key enumeration and no
    risk of a stale key surviving a flush.
    """

    version = models.BigIntegerField(default=1)
    #: {"crm": {"status": "active", "seats": 10, "write": true}}
    products = models.JSONField(default=dict)
    #: {"crm.pipelines.max": 50}
    features = models.JSONField(default=dict)
    computed_at = models.DateTimeField()

    class Meta:
        db_table = "entitlements_snapshot"
        constraints = [
            models.UniqueConstraint(fields=["organization"], name="uq_snapshot_org")
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"snapshot v{self.version} for {self.organization_id}"


class SeatGrant(TenantScopedModel):
    """A pool of seats.

    ``pool_key`` rather than ``product_code`` because a bundle may grant 10
    seats usable across CRM and Projects. Per-product seats are the common
    case (pool_key == product_code); shared pools are the enterprise case, and
    one model covers both.
    """

    pool_key = models.CharField(max_length=64, db_index=True)
    seats_total = models.IntegerField()
    source_ref = models.CharField(max_length=255)

    class Meta:
        db_table = "entitlements_seat_grant"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "pool_key", "source_ref"],
                name="uq_seat_grant_org_pool_source",
            ),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_seat_grant_id_org"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "pool_key"], name="ix_seat_org_pool")
        ]


class SeatAssignment(TenantScopedModel):
    """One member's claim on a seat.

    Assignment is a contention point: two admins assigning the last seat
    simultaneously must not both succeed. The service takes a row lock on the
    SeatGrant and counts inside the same transaction
    (06-entitlements.md section 6).
    """

    seat_grant = models.ForeignKey(
        SeatGrant, on_delete=models.CASCADE, related_name="assignments"
    )
    membership = models.ForeignKey(
        "organizations.Membership",
        on_delete=models.CASCADE,
        related_name="seat_assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        "organizations.Membership",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="seats_assigned",
    )
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "entitlements_seat_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["seat_grant", "membership"],
                condition=models.Q(released_at__isnull=True),
                name="uq_active_seat_per_member_per_pool",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "seat_grant"], name="ix_seatassign_org_grant"
            ),
            models.Index(
                fields=["organization", "membership"], name="ix_seatassign_org_member"
            ),
        ]


class EntitlementGrant(TenantScopedModel):
    """A manual grant issued by support.

    A first-class **input** to the computation, not an override applied
    afterwards. That is what makes "support fixes it manually, the nightly job
    undoes it" structurally impossible (06-entitlements.md section 5.4).
    """

    product_code = models.CharField(max_length=32, blank=True, default="")
    feature_key = models.CharField(max_length=128, blank=True, default="")
    value = models.JSONField(null=True, blank=True)
    reason = models.TextField()
    granted_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="entitlement_grants"
    )
    approved_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="entitlement_approvals",
    )
    #: Required unless a second person approved. Time-boxed by default so an
    #: emergency grant does not become permanent by forgetting.
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "entitlements_grant"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(product_code="") | ~models.Q(feature_key=""),
                name="ck_grant_targets_product_or_feature",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__isnull=False)
                | models.Q(approved_by__isnull=False),
                name="ck_grant_expires_or_approved",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "product_code"], name="ix_grant_org_product"
            )
        ]
