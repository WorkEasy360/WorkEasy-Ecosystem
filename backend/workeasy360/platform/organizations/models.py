"""Organization and Membership - the tenant, and the junction that grants access.

``Organization`` is the single unit of tenancy: the boundary for data
isolation, the subject of billing, the container for users and permissions,
and the holder of entitlements (ADR-002).

``Membership`` is the object that grants access. **Authorization always
resolves through a membership, never through a user.** ``user.has_perm(...)``
does not exist in this system; ``authz.check(ctx, permission, resource)`` does.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models
from django.utils import timezone

from workeasy360.contracts.enums import (
    MembershipStatus,
    MemberType,
    OrganizationStatus,
)
from workeasy360.platform.tenancy.models import TenantScopedModel


class OrganizationQuerySet(models.QuerySet):
    def active(self) -> OrganizationQuerySet:
        return self.filter(
            status=OrganizationStatus.ACTIVE.value, deleted_at__isnull=True
        )

    def readable(self) -> OrganizationQuerySet:
        return self.filter(
            status__in=[
                OrganizationStatus.ACTIVE.value,
                OrganizationStatus.SUSPENDED.value,
                OrganizationStatus.PENDING_DELETION.value,
            ]
        )


class Organization(models.Model):
    """The tenant.

    Deliberately **not** a ``TenantScopedModel``: an Organization does not
    belong to an organization, it *is* one. Exempted from W360.E001 for that
    reason, and protected instead by the fact that it is only ever reached
    through a verified membership.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: URL identity. Immutable after creation: changing it would break every
    #: bookmark, integration and audit record that references the path.
    slug = models.SlugField(max_length=63, unique=True)
    legal_name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)

    status = models.CharField(
        max_length=20,
        choices=[(s.value, s.name.title()) for s in OrganizationStatus],
        default=OrganizationStatus.PROVISIONING.value,
        db_index=True,
    )

    #: Reserved from day one, unused. Costs nothing now and preserves the
    #: residency and sharding options later without a data-model change
    #: (13-deployment-evolution.md section 5).
    residency_region = models.CharField(max_length=32, default="eu-west-1")
    shard_key = models.CharField(max_length=64, db_index=True, blank=True, default="")

    #: Present so the model can grow into group structures, but it grants
    #: **nothing** implicitly. RLS policies match on organization_id only,
    #: never on a descendant set: a recursive RLS predicate is a performance
    #: and correctness hazard that would silently widen every query
    #: (03-multi-tenancy.md section 3.2).
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    created_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )

    #: Soft delete, with a purge job after the retention window.
    deleted_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrganizationQuerySet.as_manager()

    class Meta:
        db_table = "organizations_organization"
        indexes = [
            models.Index(fields=["status"], name="ix_org_status"),
            models.Index(fields=["slug"], name="ix_org_slug"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.display_name} ({self.slug})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.shard_key:
            self.shard_key = str(self.id)
        super().save(*args, **kwargs)

    # --- status predicates -------------------------------------------------

    @property
    def status_enum(self) -> OrganizationStatus:
        return OrganizationStatus(self.status)

    @property
    def allows_write(self) -> bool:
        """R-10. Read via the enum so the policy lives in exactly one place."""
        return self.status_enum.allows_write and self.deleted_at is None

    @property
    def allows_read(self) -> bool:
        return self.status_enum.allows_read and self.deleted_at is None

    def suspend(self, *, reason: str = "") -> None:
        """Set read-only. Data stays visible and exportable.

        Suspension is not a lockout: a customer whose card expired must still
        be able to see their invoices, export their data and pay. Hard lockout
        maximises churn and generates data-protection complaints
        (03-multi-tenancy.md section 3.1).
        """
        self.status = OrganizationStatus.SUSPENDED.value
        self.suspended_at = timezone.now()
        self.suspension_reason = reason
        self.save(
            update_fields=["status", "suspended_at", "suspension_reason", "updated_at"]
        )

    def reactivate(self) -> None:
        """No data restoration is needed, because nothing was deleted."""
        self.status = OrganizationStatus.ACTIVE.value
        self.suspended_at = None
        self.suspension_reason = ""
        self.save(
            update_fields=["status", "suspended_at", "suspension_reason", "updated_at"]
        )


class MembershipQuerySet(models.QuerySet):
    def active(self) -> MembershipQuerySet:
        return self.filter(status=MembershipStatus.ACTIVE.value)


class Membership(TenantScopedModel):
    """A user's participation in one organization.

    Tenant-scoped, so a membership row is subject to RLS like any other
    tenant-owned record.
    """

    user = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="memberships"
    )

    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in MembershipStatus],
        default=MembershipStatus.INVITED.value,
        db_index=True,
    )

    #: Exactly one per organization, enforced by a partial unique constraint.
    #: The owner is the only persona that can delete the org or transfer
    #: ownership.
    is_owner = models.BooleanField(default=False)

    member_type = models.CharField(
        max_length=16,
        choices=[(t.value, t.name.title()) for t in MemberType],
        default=MemberType.INTERNAL.value,
    )

    joined_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "organizations_membership"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="uq_membership_org_user"
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(is_owner=True),
                name="uq_one_owner_per_org",
            ),
            # Target for composite foreign keys from child tenant tables, so
            # that a reference carries the organization and a cross-tenant
            # pointer becomes unrepresentable (Layer 4).
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_membership_id_org"
            ),
        ]
        indexes = [
            # organization leads, so the RLS predicate is index-served (B3).
            models.Index(
                fields=["organization", "status"], name="ix_membership_org_status"
            ),
            models.Index(fields=["organization", "user"], name="ix_membership_org_user"),
            models.Index(fields=["user"], name="ix_membership_user"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.user_id} in {self.organization_id}"

    @property
    def status_enum(self) -> MembershipStatus:
        return MembershipStatus(self.status)

    @property
    def grants_access(self) -> bool:
        return self.status_enum.grants_access


class Invitation(TenantScopedModel):
    """A pending invitation to join an organization.

    The token is stored **hashed**. A database dump must not yield working
    invitation links, which would be an account-takeover primitive against
    every pending invitee.
    """

    email = models.EmailField(max_length=254)
    token_hash = models.CharField(max_length=128, unique=True)
    invited_by = models.ForeignKey(
        "organizations.Membership",
        null=True,
        on_delete=models.SET_NULL,
        related_name="sent_invitations",
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "organizations_invitation"
        indexes = [
            models.Index(fields=["organization", "email"], name="ix_invite_org_email"),
            models.Index(fields=["organization", "expires_at"], name="ix_invite_org_exp"),
        ]

    @property
    def is_redeemable(self) -> bool:
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )
