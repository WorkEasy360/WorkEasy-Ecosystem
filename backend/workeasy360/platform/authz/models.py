"""RBAC: Role, Permission, RolePermission, RoleAssignment.

Role-based permissions, scoped by organization, product or resource, with a
narrow object-rule extension evaluated by the owning module (ADR-004).

Pure RBAC cannot express "may edit deals they own"; full ABAC produces
decisions nobody can explain to a customer admin and is slow at list-query
scale. This shape sits between them deliberately.
"""

from __future__ import annotations

import uuid

from django.db import models

from workeasy360.contracts.enums import RoleScopeType
from workeasy360.platform.tenancy.models import TenantScopedModel


class Permission(models.Model):
    """The database mirror of a code declaration.

    Control-plane reference data: global, no ``organization_id``. Rows are
    synced from ``permission_registry`` at deploy, and the registry - not this
    table - remains the source of truth. The table exists so that
    ``RolePermission`` can hold a foreign key and so the admin UI can join to
    a label without importing the registry.
    """

    key = models.CharField(max_length=128, primary_key=True)
    label = models.CharField(max_length=255)
    product = models.CharField(max_length=32, db_index=True)
    category = models.CharField(max_length=64, default="general")
    dangerous = models.BooleanField(default=False)
    #: Denormalised from the registry's transitive closure, so a role compile
    #: does not need the registry loaded.
    implies = models.JSONField(default=list)

    class Meta:
        db_table = "authz_permission"
        indexes = [models.Index(fields=["product", "category"], name="ix_perm_prod_cat")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.key


class Role(models.Model):
    """A named bundle of permissions.

    **The D-001 exception.** This is the one table with a nullable
    ``organization_id``: system templates are global (NULL) and custom roles
    are tenant-owned. The alternative - two tables with an identical shape and
    a polymorphic join from RoleAssignment - is materially worse for every
    query.

    Made safe by an asymmetric RLS policy (``enable_system_template_rls``):
    reads match own-tenant rows OR system templates; writes match own-tenant
    rows only, so the application role can never write a NULL-tenant row.
    System roles are therefore readable by all tenants and writable by none.

    Not a ``TenantScopedModel`` for the same reason, and exempted from
    W360.E001 explicitly rather than by omission.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="roles",
        help_text="NULL for a global system template.",
    )
    key = models.SlugField(max_length=64)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    product = models.CharField(
        max_length=32, blank=True, default="", help_text="Empty = cross-product."
    )
    is_system = models.BooleanField(default=False)
    is_assignable = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "authz_role"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "key"], name="uq_role_org_key"
            ),
            models.UniqueConstraint(
                fields=["key"],
                condition=models.Q(organization__isnull=True),
                name="uq_system_role_key",
            ),
            # A system role must have no tenant, and a tenant role must not
            # claim to be one. Without this, a custom role marked is_system
            # would be readable by every tenant.
            models.CheckConstraint(
                condition=models.Q(is_system=True, organization__isnull=True)
                | models.Q(is_system=False, organization__isnull=False),
                name="ck_role_system_xor_tenant",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "key"], name="ix_role_org_key"),
            models.Index(fields=["is_system"], name="ix_role_system"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.key} ({'system' if self.is_system else self.organization_id})"


class RolePermission(models.Model):
    """Join between a role and a permission."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="role_permissions"
    )
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_permissions"
    )

    class Meta:
        db_table = "authz_role_permission"
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"], name="uq_role_permission"
            )
        ]
        indexes = [models.Index(fields=["role"], name="ix_roleperm_role")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.role_id} grants {self.permission_id}"


class RoleAssignment(TenantScopedModel):
    """Binds a membership to a role, within a scope.

    Scope is where the real power is: the same role means something different
    org-wide, within one product, or within one resource subtree.
    """

    membership = models.ForeignKey(
        "organizations.Membership",
        on_delete=models.CASCADE,
        related_name="role_assignments",
    )
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="assignments")

    scope_type = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in RoleScopeType],
        default=RoleScopeType.ORGANIZATION.value,
    )
    scope_product = models.CharField(max_length=32, blank=True, default="")
    #: A soft reference ("projects.project:<uuid>"), not a foreign key:
    #: platform must not hold FKs into products (F-6).
    scope_ref = models.CharField(max_length=255, blank=True, default="")

    granted_by = models.ForeignKey(
        "organizations.Membership",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_assignments",
    )
    #: Present from day one. Temporary elevation that expires automatically is
    #: the difference between a contractor with two weeks of access and a
    #: contractor with permanent access nobody remembers granting.
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "authz_role_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "role", "scope_type", "scope_product", "scope_ref"],
                name="uq_assignment_membership_role_scope",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "membership"], name="ix_assign_org_member"
            ),
            models.Index(fields=["organization", "role"], name="ix_assign_org_role"),
            models.Index(
                fields=["organization", "expires_at"], name="ix_assign_org_expiry"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.membership_id} -> {self.role_id} ({self.scope_type})"

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone

        return self.expires_at is not None and self.expires_at <= timezone.now()


class PermissionSetVersion(TenantScopedModel):
    """Per-organization cache version counter.

    Computing effective permissions by joining assignments to roles to
    permissions on every check is unaffordable. The flattened set is computed
    once and cached under a version-keyed key; any change to roles or
    assignments bumps this counter, invalidating the whole organization's
    cache without enumerating members (04-identity-authz.md section 4.5).
    """

    version = models.BigIntegerField(default=1)

    class Meta:
        db_table = "authz_permission_set_version"
        constraints = [
            models.UniqueConstraint(
                fields=["organization"], name="uq_permission_version_org"
            )
        ]


class StaffGrant(models.Model):
    """One control-plane permission held by one Workeasy360 staff member.

    **Global control-plane data**: no ``organization_id``, no RLS, and not
    reachable through any tenant path. It is written only by
    ``authz.staff`` (never by a customer administrator, who has no route to
    it) and read by the decision function when a ``STAFF`` principal asks
    what it may do (ADR-027).

    Rows are never deleted. A revocation stamps ``revoked_at`` and who did it
    and why, so "who could approve payouts on 3 March?" is answerable from
    this table alone. The partial unique index below makes a duplicate
    *active* grant unrepresentable, so a grant/grant race resolves in the
    database rather than in a check-then-insert.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    #: A registered ``platform.staff_*`` key. A string rather than a foreign
    #: key to ``Permission``: the registry is the source of truth and the
    #: table is its deploy-time mirror, so a grant must not depend on the
    #: mirror having been synced. The service validates against the registry.
    permission = models.CharField(max_length=128)
    granted_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    granted_at = models.DateTimeField()
    reason = models.TextField()
    #: Optional time box. A grant confers nothing at or after this instant -
    #: read on every check, like revocation, so no job has to run for it to
    #: end. Frozen with the grant's other terms: to extend one, grant anew.
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    revoke_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "authz_staff_grant"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "permission"],
                condition=models.Q(revoked_at__isnull=True),
                name="uq_staff_grant_active",
            ),
            models.CheckConstraint(
                condition=models.Q(revoked_at__isnull=True, revoked_by__isnull=True)
                | models.Q(revoked_at__isnull=False, revoked_by__isnull=False),
                name="ck_staff_grant_revocation_complete",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "revoked_at"], name="ix_staff_grant_user_live"),
            models.Index(
                fields=["permission", "revoked_at"], name="ix_staff_grant_perm_live"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.user_id} holds {self.permission}"
