"""Authorization: deny by default, one decision path, no name-branching."""

from __future__ import annotations

import pytest

from workeasy360.contracts.enums import OrganizationStatus
from workeasy360.contracts.errors import (
    PermissionDenied,
    UnknownPermission,
)
from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.models import (
    Permission,
    Role,
    RoleAssignment,
    RolePermission,
)
from workeasy360.platform.authz.registry import permission_registry

pytestmark = [pytest.mark.django_db, pytest.mark.security]


@pytest.fixture
def sync_permissions(db):
    """Mirror the code-declared registry into the database.

    In production this is a deploy step. A permission that is not declared in
    code cannot be granted, so the registry is the source of truth and this
    table is its projection.
    """
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(
        reason="Permission sync is control-plane reference data",
        module="tests.authz",
    ):
        for declaration in permission_registry.all():
            Permission.objects.update_or_create(
                key=str(declaration.key),
                defaults={
                    "label": declaration.label,
                    "product": declaration.product,
                    "category": declaration.category,
                    "dangerous": declaration.dangerous,
                    "implies": list(declaration.implies),
                },
            )
    return Permission.objects.count()


@pytest.fixture
def grant_role(db, sync_permissions):
    """Create a tenant-owned role with permissions and assign it."""
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    def make(org, membership, keys, *, key="tester", expires_at=None):
        # authz_role carries the D-001 asymmetric RLS policy: writes must
        # match the current tenant, so the whole fixture runs inside this
        # organization's database scope.
        with (
            unscoped_access(
                reason="Test fixture seeds a custom role",
                module="tests.authz",
            ),
            organization_db_scope(org.id),
        ):
            role = Role.objects.create(
                organization_id=org.id, key=key, name=key.title(), is_system=False
            )
            for permission_key in keys:
                RolePermission.objects.create(
                    role=role, permission_id=str(permission_key)
                )
            assignment = RoleAssignment(
                organization_id=org.id,
                membership_id=membership.id,
                role=role,
                expires_at=expires_at,
            )
            assignment.save()
        authz.bump_permission_version(org.id)
        return role, assignment

    return make


# --- deny by default ------------------------------------------------------


def test_member_with_no_roles_is_denied_everything(
    two_tenants, in_tenant, sync_permissions
):
    """The default is deny. A membership grants presence, not permission."""
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        for permission in permission_registry.all_keys():
            decision = authz.check(a["ctx"], permission)
            assert not decision.allowed, f"{permission} was allowed with no roles"
            assert decision.reason == "permission_not_granted"


def test_require_raises_when_denied(two_tenants, in_tenant, sync_permissions):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        with pytest.raises(PermissionDenied) as exc:
            authz.require(a["ctx"], "platform.member.view")
    assert exc.value.http_status == 403
    assert exc.value.context["permission"] == "platform.member.view"


def test_unknown_permission_raises_rather_than_denying(two_tenants, in_tenant):
    """A typo must not become a silent, permanent denial.

    Returning False for an unregistered key would hide the bug until a
    customer complained that a feature never worked.
    """
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        with pytest.raises(UnknownPermission):
            authz.check(a["ctx"], "platform.nonexistent.action")


def test_granted_permission_is_allowed(two_tenants, in_tenant, grant_role):
    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.member.view"])

    with in_tenant(a["ctx"]):
        assert authz.check(a["ctx"], "platform.member.view").allowed


def test_grant_does_not_leak_to_other_permissions(two_tenants, in_tenant, grant_role):
    """Granting one permission grants exactly one permission."""
    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.member.view"])

    with in_tenant(a["ctx"]):
        assert authz.check(a["ctx"], "platform.member.view").allowed
        assert not authz.check(a["ctx"], "platform.member.remove").allowed
        assert not authz.check(a["ctx"], "platform.billing.view").allowed


def test_grant_does_not_leak_across_tenants(two_tenants, in_tenant, grant_role):
    """A role in tenant A grants nothing in tenant B."""
    a, b = two_tenants["a"], two_tenants["b"]
    grant_role(a["org"], a["membership"], ["platform.member.view"])

    with in_tenant(a["ctx"]):
        assert authz.check(a["ctx"], "platform.member.view").allowed
    with in_tenant(b["ctx"]):
        assert not authz.check(b["ctx"], "platform.member.view").allowed


# --- implication ----------------------------------------------------------


def test_implications_are_expanded(two_tenants, in_tenant, grant_role):
    """``platform.member.invite`` implies ``platform.member.view``.

    Expanded at role-compile time, so a check stays a single set-membership
    test rather than a graph walk.
    """
    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.member.invite"])

    with in_tenant(a["ctx"]):
        assert authz.check(a["ctx"], "platform.member.invite").allowed
        assert authz.check(a["ctx"], "platform.member.view").allowed


def test_implication_is_one_directional(two_tenants, in_tenant, grant_role):
    """Holding the weaker permission must not confer the stronger one."""
    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.member.view"])

    with in_tenant(a["ctx"]):
        assert authz.check(a["ctx"], "platform.member.view").allowed
        assert not authz.check(a["ctx"], "platform.member.invite").allowed


def test_every_declared_implication_target_exists():
    """A typo in ``implies`` would silently expand to nothing."""
    assert permission_registry.validate_all() == []


# --- dangerous permissions -----------------------------------------------


def test_dangerous_permission_needs_the_grant_and_no_second_factor(
    two_tenants, in_tenant, grant_role, tenant_ctx_factory
):
    """A dangerous permission is allowed to whoever holds it: no second factor
    stands between the grant and the action (ADR-050)."""
    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.role.assign"])

    held = tenant_ctx_factory(a["org"], a["membership"], a["user"])
    with in_tenant(held):
        assert authz.check(held, "platform.role.assign").allowed


def test_dangerous_permission_blocked_under_impersonation(
    two_tenants, in_tenant, grant_role, tenant_ctx_factory
):
    """Impersonated sessions cannot perform anything marked dangerous.

    Blocked in the decision function rather than the UI, because the UI is
    not a security boundary (04-identity-authz.md section 6).
    """
    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.role.assign"])

    break_glass = tenant_ctx_factory(
        a["org"],
        a["membership"],
        a["user"],
        is_break_glass=True,
        reason="Investigating support ticket 1234",
    )
    with in_tenant(break_glass):
        decision = authz.check(break_glass, "platform.role.assign")
    assert not decision.allowed
    assert decision.reason == "blocked_under_impersonation"


def test_break_glass_context_requires_a_reason():
    """An unexplained privileged session is what the control exists to stop."""
    import uuid

    from workeasy360.contracts.values.tenant import TenantContext

    with pytest.raises(ValueError, match="reason"):
        TenantContext(
            organization_id=uuid.uuid4(),
            membership_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            is_break_glass=True,
        )


def test_dangerous_permission_denied_when_organization_read_only(
    two_tenants, in_tenant, grant_role, tenant_ctx_factory
):
    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.role.assign"])

    suspended = tenant_ctx_factory(a["org"], a["membership"], a["user"]).replace_status(
        OrganizationStatus.SUSPENDED
    )

    with in_tenant(a["ctx"]):
        decision = authz.check(suspended, "platform.role.assign")
    assert not decision.allowed
    assert decision.reason == "organization_read_only"


# --- expiry ---------------------------------------------------------------


def test_expired_assignment_grants_nothing(two_tenants, in_tenant, grant_role):
    """Time-boxed elevation must actually expire.

    This is the difference between a contractor with two weeks of access and
    a contractor with permanent access nobody remembers granting.
    """
    from datetime import timedelta

    from django.utils import timezone

    a = two_tenants["a"]
    grant_role(
        a["org"],
        a["membership"],
        ["platform.member.view"],
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    with in_tenant(a["ctx"]):
        assert not authz.check(a["ctx"], "platform.member.view").allowed


def test_future_expiry_still_grants(two_tenants, in_tenant, grant_role):
    from datetime import timedelta

    from django.utils import timezone

    a = two_tenants["a"]
    grant_role(
        a["org"],
        a["membership"],
        ["platform.member.view"],
        expires_at=timezone.now() + timedelta(days=1),
    )

    with in_tenant(a["ctx"]):
        assert authz.check(a["ctx"], "platform.member.view").allowed


# --- cache behaviour ------------------------------------------------------


def test_revocation_takes_effect_after_version_bump(two_tenants, in_tenant, grant_role):
    """A revoked permission must stop working promptly.

    Version-keyed invalidation is one counter increment for the whole
    organization, with no key enumeration.
    """
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    a = two_tenants["a"]
    role, assignment = grant_role(a["org"], a["membership"], ["platform.member.view"])

    with in_tenant(a["ctx"]):
        assert authz.check(a["ctx"], "platform.member.view").allowed

    # Both layers, as production requires. ``unscoped_access`` relaxes the ORM
    # manager; RLS is untouched by it, so without the database scope this
    # DELETE matches zero rows and the test would assert that a revocation
    # nobody performed had no effect.
    with (
        unscoped_access(
            reason="Test revokes a role assignment directly",
            module="tests.authz",
        ),
        organization_db_scope(a["org"].id),
    ):
        deleted, _ = RoleAssignment.unscoped.filter(id=assignment.id).delete()
        assert deleted == 1, "the revocation under test did not delete anything"
    authz.bump_permission_version(a["org"].id)

    with in_tenant(a["ctx"]):
        assert not authz.check(a["ctx"], "platform.member.view").allowed


def test_a_deploy_that_changes_system_role_grants_ignores_sets_cached_before_it(
    two_tenants, in_tenant, grant_role, monkeypatch
):
    """Phase 6 granted owner/admin new permissions by migration, for every
    organization at once, with no per-organization version bump. A set
    cached before the deploy - under the old key format, or under another
    catalog generation - must never be read, or every owner sees the new
    pages as 403 for up to the cache TTL (found by the upgrade rehearsal)."""
    from django.core.cache import cache

    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.member.view"])
    prefix = f"w360:{a['org'].id}:perms:{a['membership'].id}"

    with in_tenant(a["ctx"]):
        version = authz._permission_set_version(a["ctx"])
        cache.set(f"{prefix}:v{version}", [], 300)  # before the fingerprint existed
        cache.set(f"{prefix}:v{version}:{'0' * 12}", [], 300)  # another generation
        assert authz.check(a["ctx"], "platform.member.view").allowed

    generation = authz._catalog_generation()
    changed = {**authz.SYSTEM_ROLES["admin"], "permissions": ["platform.member.view"]}
    monkeypatch.setitem(authz.SYSTEM_ROLES, "admin", changed)
    authz._catalog_generation.cache_clear()
    try:
        assert authz._catalog_generation() != generation
    finally:
        monkeypatch.undo()
        authz._catalog_generation.cache_clear()
    assert authz._catalog_generation() == generation


def test_authorization_never_fails_open_when_cache_is_down(
    two_tenants, in_tenant, grant_role, monkeypatch
):
    """Redis unavailable means recompute from the database.

    Slower, still correct. Authorization never fails open
    (04-identity-authz.md section 4.5).
    """
    from django.core import cache as cache_module

    a = two_tenants["a"]
    grant_role(a["org"], a["membership"], ["platform.member.view"])

    def explode(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(cache_module.cache, "get", explode)
    monkeypatch.setattr(cache_module.cache, "set", explode)

    with in_tenant(a["ctx"]):
        assert authz.check(a["ctx"], "platform.member.view").allowed
        assert not authz.check(a["ctx"], "platform.billing.manage").allowed


# --- naming conventions ---------------------------------------------------


def test_every_permission_follows_the_naming_convention():
    """``<product>.<resource>.<action>``, so the privilege surface is
    greppable and reviewable."""
    for key in permission_registry.all_keys():
        parsed = PermissionKey(key)  # raises if malformed
        assert parsed.product
        assert parsed.resource
        assert parsed.action


#: Namespaces this repository may register permissions under. Both are
#: platform infrastructure, not products: ``platform`` is the kernel and
#: ``commercial`` sells the products rather than being one (widened to
#: include it in Phase 2, when it gained a real privilege surface -
#: catalog, pricing and subscription administration).
#:
#: The point of the list is that it does **not** contain a product code.
#: Under ADR-017 a product is a separate application, so the code enforcing
#: a product permission is not in this repository at all - registering one
#: here would make it grantable with nothing behind it, which is worse than
#: not having it.
NON_PRODUCT_PERMISSION_NAMESPACES = {"platform", "commercial"}


def test_only_non_product_namespaces_register_permissions():
    """No product permission is registered by the platform repository.

    Products declare their privilege surface to the platform through the
    registration contract (16-ecosystem-product-boundary.md section 7), which
    is data submitted at deploy time - not a tuple in this source tree.
    """
    for declaration in permission_registry.all():
        assert declaration.product in NON_PRODUCT_PERMISSION_NAMESPACES, (
            f"{declaration.key} is registered under {declaration.product!r}, "
            f"which is not one of {sorted(NON_PRODUCT_PERMISSION_NAMESPACES)}. "
            f"Product permissions belong to the product (ADR-017)."
        )


def test_no_system_role_grants_catalog_or_pricing_management():
    """The catalog applies to every tenant at once.

    A customer administrator must never be able to change what products
    exist, what a plan includes or what it costs. Two independent barriers
    enforce that; this asserts the first - that no role a customer can hold
    carries the permission at all. The second (an internal platform identity)
    is asserted in the commercial suite.
    """
    control_plane = {"commercial.catalog.manage", "commercial.pricing.manage"}
    for name, spec in authz.SYSTEM_ROLES.items():
        keys = spec["permissions"]
        if keys == "*":
            # The owner role is explicitly the organization's own superuser.
            # It is scoped to that organization and its assignments are
            # tenant rows; it cannot reach a control-plane table, because the
            # services additionally require internal staff.
            continue
        granted = control_plane & set(keys)
        assert not granted, (
            f"System role {name!r} grants control-plane permission(s) "
            f"{sorted(granted)}, which would let a customer edit the global "
            f"catalog."
        )


def test_example_product_permissions_remain_unregistered():
    """``EXAMPLE_PRODUCT_PERMISSIONS`` is a naming convention, not a grant.

    Every key in it belongs to a product built outside this repository.
    Registering one would make it grantable with nothing enforcing it, which
    is worse than not having it at all.
    """
    from workeasy360.platform.authz.permissions import EXAMPLE_PRODUCT_PERMISSIONS

    for key in EXAMPLE_PRODUCT_PERMISSIONS:
        assert not permission_registry.exists(key), (
            f"{key} is registered but its product does not exist yet."
        )


def test_admin_role_excludes_billing():
    """In most customers the person administering users is not the person
    holding the card. Bundling them forces over-privileging."""
    admin = authz.SYSTEM_ROLES["admin"]["permissions"]
    assert "platform.billing.manage" not in admin
    assert "platform.billing.view" not in admin
    assert "platform.member.invite" in admin


def test_system_roles_reference_only_declared_permissions():
    for name, spec in authz.SYSTEM_ROLES.items():
        keys = spec["permissions"]
        if keys == "*":
            continue
        for key in keys:
            assert permission_registry.exists(key), (
                f"System role {name!r} grants undeclared permission {key!r}."
            )
