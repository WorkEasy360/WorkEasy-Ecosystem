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

    # --- access policy (Phase 8 extension) --------------------------------
    #
    # On the organization row itself, not a side table: the tenant resolver
    # already loads this row (before any tenant scope exists, which a
    # tenant-scoped policy table could not be read under), so enforcing the
    # policy on every request costs no additional query. Applied by
    # ``contracts.values.access_policy.evaluate`` after membership is verified;
    # it can only deny. Written only by ``platform.administration``, with
    # ``platform.security.update`` (dangerous) and an audit entry.

    #: Unused since the second factor was removed (ADR-050). The column is kept
    #: so no data is dropped; nothing reads or writes it.
    require_mfa = models.BooleanField(default=False)
    #: Minutes after sign-in beyond which a session no longer reaches this
    #: organization. NULL = the platform's own session lifetime.
    session_max_age_minutes = models.PositiveIntegerField(null=True, blank=True)
    #: Raises the platform minimum (never lowers it). Length only: composition
    #: rules are deliberately not offered (12-security.md section 3).
    password_min_length = models.PositiveSmallIntegerField(null=True, blank=True)
    ip_allowlist_enabled = models.BooleanField(default=False)
    #: Normalised CIDR strings (``access_policy.normalise_allowlist``).
    ip_allowlist = models.JSONField(default=list, blank=True)
    #: Optimistic token for policy edits: two administrators saving at once
    #: must not silently overwrite each other's allow-list.
    security_policy_version = models.PositiveIntegerField(default=1)
    security_policy_updated_at = models.DateTimeField(null=True, blank=True)

    #: Soft delete, with a purge job after the retention window.
    deleted_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "organizations_organization"
        indexes = [
            models.Index(fields=["status"], name="ix_org_status"),
            models.Index(fields=["slug"], name="ix_org_slug"),
            # The staff directory pages "newest first" with a keyset cursor
            # on exactly this ordering (Phase 7); without it every page sorts
            # the whole table.
            models.Index(fields=["-created_at", "-id"], name="ix_org_created_id"),
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

    # Suspension, reactivation, renaming and policy edits are written by
    # ``platform.organizations.services`` - the one writer of this table.


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
    #: "Sign this member out of the organization" (Phase 8 extension): a
    #: session signed in before this moment no longer reaches this
    #: organization. Organization-scoped on purpose - the member's session is
    #: theirs, and ending it everywhere is not this organization's call.
    sessions_valid_after = models.DateTimeField(null=True, blank=True)

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

    # --- Phase 8 extension: the invitation system, finally written ---------
    #: The system role the invitee receives on acceptance. Never ``owner``:
    #: ownership moves by transfer, not by invitation.
    role_key = models.CharField(max_length=32, default="member")
    #: Each resend rotates the token (only its hash is kept, so the old link
    #: cannot be re-sent - and stops working).
    send_count = models.PositiveIntegerField(default=1)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "organizations_invitation"
        constraints = [
            # One open invitation per address per organization. Expired ones
            # are revoked by the service before a new one is issued.
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name="uq_invite_open_per_email",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "email"], name="ix_invite_org_email"),
            models.Index(fields=["organization", "expires_at"], name="ix_invite_org_exp"),
            models.Index(
                fields=["organization", "-created_at"], name="ix_invite_org_created"
            ),
        ]

    @property
    def is_redeemable(self) -> bool:
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )


class MemberSignIn(TenantScopedModel):
    """A member entering this organization, once per browser session.

    What the organization console's "signed-in users" and "most signed-in
    users" are built from. **Tenant-scoped and RLS-protected**, and written when
    the member's session first opens this organization - not at platform
    sign-in - so an administrator learns only how their own organization is
    used, never when a member signs in to work for another tenant.

    Idempotent per (membership, session): the unique constraint turns every
    later page load of the same session into a no-op insert.
    """

    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="sign_ins"
    )
    user = models.ForeignKey("identity.User", on_delete=models.CASCADE, related_name="+")
    #: The session's public id (``identity.UserSession.public_id``) - never
    #: the session key, which is the cookie's bearer secret.
    session_ref = models.UUIDField()
    occurred_at = models.DateTimeField(default=timezone.now)
    ip_prefix = models.CharField(max_length=64, blank=True, default="")
    device_label = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "organizations_member_sign_in"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "membership", "session_ref"],
                name="uq_member_sign_in_session",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-occurred_at"], name="ix_member_sign_in_time"
            ),
            models.Index(
                fields=["organization", "membership", "-occurred_at"],
                name="ix_member_sign_in_member",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.membership_id} @ {self.occurred_at}"


class OrganizationCreationRequest(models.Model):
    """Idempotency ledger for ``create_organization`` (Phase 5, Task 6).

    **Why this cannot be ``integration.idempotency.IdempotencyRecord``.**
    That ledger is tenant-scoped (``organization_id`` required), and
    ``integration/`` is L2 while ``platform/organizations`` is L1 - importing
    it would be an upward edge (F-4) even if the shape fit. Organization
    creation is also the one command that structurally *cannot* be scoped to
    a tenant: there is no organization until the command succeeds. Scoped
    instead by the verified ``user_id`` of the caller, which is the only
    stable identity available before an organization exists - the same
    substitution ``organizations_for_user`` makes for the read side.

    Control-plane, like ``Organization`` itself: not a ``TenantScopedModel``,
    no RLS. Safe because every write and read goes through
    ``platform.organizations.services``, which always supplies the caller's
    *own*, server-verified ``user_id`` - never a client-supplied one.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "identity.User",
        on_delete=models.CASCADE,
        related_name="organization_creation_requests",
    )
    #: Caller-supplied idempotency key (e.g. a client-generated request id).
    key = models.CharField(max_length=128)
    #: SHA-256 of the normalised request arguments - same purpose and shape
    #: as ``integration.idempotency``'s fingerprint: a replay of this key
    #: with different arguments is refused rather than served.
    fingerprint = models.CharField(max_length=64)
    #: Set once the command completes. ``NULL`` while another request holds
    #: the claim (mid-transaction) or, in the pathological crash-after-claim
    #: case, forever - which is why this must be inserted in the *same*
    #: transaction as the ``Organization`` row it will point to: a rollback
    #: undoes the claim with it, and a claim can never outlive its result.
    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="creation_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "organizations_creation_request"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "key"], name="uq_org_creation_user_key"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.user_id}:{self.key}"


class UserOrganizationAccess(models.Model):
    """Access-directory projection (Phase 5, Task 16-24; ADR-022).

    Answers **"which organizations should appear in this user's chooser"** -
    a fast, denormalised read model. It is explicitly **not** the
    authorization authority: ``Membership`` remains that, and every request
    that acts within an organization re-resolves membership from scratch
    (``platform.tenancy.resolution``), independent of what this table says.
    A stale or even maliciously-poisoned row here can make an organization
    wrongly *appear* to a user; it cannot make an organization wrongly
    *act upon* for them, because nothing reads this table on that path.

    One read here does steer a sign-in (ADR-044 item 16): a product sign-in
    that names no organization is for the person's only organization, and
    "only" is counted here (``resolver.sole_organization_id``). That picks
    which organization is *checked*, never what is granted - the answer is
    then resolved against ``Membership`` exactly like a named organization.
    A missing row can at worst choose among the person's real memberships
    without asking; the drift checker reports missing rows.

    **Why not a ``TenantScopedModel``.** The one query this table exists to
    answer - "every organization for user X" - is a cross-tenant read by
    definition. RLS keyed on ``organization_id`` (Layer 3) would force this
    table right back into the one-organization-at-a-time scan D-013 exists to
    remove; that is precisely what "keep RLS off, protect by verified
    user_id" bought ``organizations_for_user``'s control-plane read of
    ``Organization`` on the write side (D-008 is the same shape: a table that
    is not a tenant's own data, protected by an explicit narrow condition
    rather than the tenant isolation layer meant for tenant-owned tables).

    **Trust model, stated plainly (Task 18).** Every legitimate read is
    filtered by ``user_id`` taken from the authenticated session - never
    from a client-supplied value - by ``platform.organizations.selectors``.
    There is no endpoint that accepts a caller-supplied ``user_id`` for this
    table. An IDOR would require the application code itself to pass another
    user's id, which the selector layer's signature makes structurally
    unreachable from an HTTP parameter.

    Kept deliberately narrow: display metadata only, no permission set, no
    subscription, no entitlement. A wider table is more that can drift.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "identity.User", on_delete=models.CASCADE, related_name="organization_access_rows"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="user_access_rows"
    )
    #: Denormalised display metadata the chooser needs, so listing it costs
    #: no join. Kept in sync by the same service call that changes the
    #: source (Task 20); the drift checker (Task 24) is what a divergence
    #: would show.
    organization_slug = models.SlugField(max_length=63)
    organization_display_name = models.CharField(max_length=255)
    organization_status = models.CharField(max_length=20)
    #: Soft reference, not a foreign key: ``Membership`` is tenant-scoped and
    #: RLS-protected, and this table is deliberately read without a tenant
    #: context. Selection always re-resolves the real membership row
    #: (Task 19); this id is display/continuity metadata only.
    membership_id = models.UUIDField()
    is_owner = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "organizations_user_access"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"], name="uq_user_org_access"
            )
        ]
        indexes = [
            # The lookup this table exists for: every organization for one
            # user, newest-membership-first, index-served rather than
            # scanned (Task 63).
            models.Index(
                fields=["user", "-updated_at", "id"], name="ix_user_org_access_user"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.user_id} -> {self.organization_id}"
