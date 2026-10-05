"""Organization creation and membership removal (Phase 5, Tasks 2-13, 20, 30-32).

Plain ``pytest.mark.django_db`` (rollback mode) throughout: these assert
business behaviour, not commit timing - that is
``tests/integration/test_deferred_fk_commit_safety.py``'s job, and it is
what actually found the two RLS/tenant-context bugs this module's happy-path
tests would not have caught in rollback mode (rollback never reaches the
point a real WITH CHECK is evaluated against a truly separate transaction).
"""

from __future__ import annotations

import pytest

from workeasy360.contracts.errors import (
    IdempotencyConflict,
    OrganizationValidationError,
)
from workeasy360.platform.onboarding.services import create_organization, remove_member

pytestmark = pytest.mark.django_db


def test_create_organization_bootstraps_owner_with_full_permissions(
    user_factory, in_tenant
):
    """Tasks 4, 30: the creator becomes owner, and the owner role actually
    grants permissions - the exact gap the Phase 4 audit found ("a fresh
    owner currently holds no permissions")."""
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    user = user_factory()
    result = create_organization(user_id=user.id, name="ABC Company")

    assert result.created is True
    assert result.display_name == "ABC Company"
    assert result.slug  # server-generated

    with (
        unscoped_access(reason="Test reads the membership back directly", module="tests"),
        organization_db_scope(result.organization_id),
    ):
        membership = Membership.unscoped.get(id=result.membership_id)
        assert membership.is_owner is True
        assert membership.user_id == user.id

    ctx = TenantContext(
        organization_id=result.organization_id,
        membership_id=result.membership_id,
        user_id=user.id,
        aal=2,  # dangerous permissions need it; irrelevant ones ignore it
    )
    with in_tenant(ctx):
        # A representative sample, not every permission: ordinary
        # administration (Task 30's "approved owner role" - view org, manage
        # members, manage seats) must work...
        assert authz.check(ctx, "platform.organization.view")
        assert authz.check(ctx, "platform.member.invite")
        assert authz.check(ctx, "platform.seat.assign")
        # ...but platform staff / control-plane actions must not, even
        # though "*" formally grants the permission - the second barrier
        # (require_platform_staff) is what actually stops it, proven here
        # by is_platform_staff being false for an ordinary customer owner.
        assert not authz.is_platform_staff(ctx)


def test_create_organization_sets_active_status_not_the_model_default(user_factory):
    """Task 8: PROVISIONING (the model's own default) permits neither read
    nor write - a fresh organization left there would be unusable by the
    owner it was just created for."""
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.platform.organizations.models import Organization

    user = user_factory()
    result = create_organization(user_id=user.id, name="Status Co")

    org = Organization.objects.get(id=result.organization_id)
    assert org.status == OrganizationStatus.ACTIVE.value
    assert org.allows_read and org.allows_write


def test_create_organization_populates_the_access_directory_projection(user_factory):
    """Task 16/29: the new organization is immediately visible in the
    creator's chooser - the projection is written in the same transaction,
    not asynchronously (ADR-022)."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.selectors import organizations_for_user

    user = user_factory()
    result = create_organization(user_id=user.id, name="Projected Co")

    row = UserOrganizationAccess.objects.get(
        user_id=user.id, organization_id=result.organization_id
    )
    assert row.is_owner is True
    assert row.organization_display_name == "Projected Co"

    page = organizations_for_user(user.id)
    assert [o["organization_id"] for o in page.items] == [str(result.organization_id)]


def test_create_organization_rejects_empty_and_overlong_names(user_factory):
    user = user_factory()
    with pytest.raises(OrganizationValidationError):
        create_organization(user_id=user.id, name="")
    with pytest.raises(OrganizationValidationError):
        create_organization(user_id=user.id, name="   ")
    with pytest.raises(OrganizationValidationError):
        create_organization(user_id=user.id, name="x" * 256)


def test_create_organization_does_not_require_email_verification_or_product_choice(
    user_factory,
):
    """Task 3/9/10: minimal input, no billing/product coupling. Only
    ``name`` is accepted; nothing about email verification is asserted
    because this platform does not gate it anywhere else either (Task 10 -
    "review... if required", and it is not, stated here rather than
    silently assumed)."""
    from workeasy360.contracts.enums import UserStatus
    from workeasy360.platform.identity.models import User

    user = User.objects.create_user(email="unverified@example.com", password="pw")
    assert user.email_verified_at is None
    assert UserStatus(user.status).can_authenticate

    result = create_organization(user_id=user.id, name="No Verify Co")
    assert result.created is True


def test_create_organization_refuses_a_disabled_account(user_factory):
    from workeasy360.contracts.enums import UserStatus
    from workeasy360.contracts.errors import PermissionDenied
    from workeasy360.platform.identity.models import User

    user = user_factory()
    User.objects.filter(pk=user.id).update(status=UserStatus.DISABLED.value)

    with pytest.raises(PermissionDenied):
        create_organization(user_id=user.id, name="Should Not Exist Co")


# --- idempotency (Task 6) ---------------------------------------------------


def test_same_idempotency_key_twice_returns_the_same_organization(user_factory):
    user = user_factory()
    key = "client-request-1"

    first = create_organization(
        user_id=user.id, name="Idempotent Co", idempotency_key=key
    )
    second = create_organization(
        user_id=user.id, name="Idempotent Co", idempotency_key=key
    )

    assert first.organization_id == second.organization_id
    assert first.created is True
    assert second.created is False  # a replay, not a new organization

    from workeasy360.platform.organizations.models import Organization

    assert Organization.objects.filter(display_name="Idempotent Co").count() == 1


def test_same_idempotency_key_with_a_different_name_is_refused(user_factory):
    """Reusing a key for a different command is refused, not silently
    served - otherwise one authorisation could perform two different
    operations."""
    user = user_factory()
    key = "client-request-2"

    create_organization(user_id=user.id, name="First Name Co", idempotency_key=key)
    with pytest.raises(IdempotencyConflict):
        create_organization(
            user_id=user.id, name="Different Name Co", idempotency_key=key
        )


def test_different_keys_same_name_create_two_organizations(user_factory):
    """Task 6: organization names are deliberately not made globally unique
    to obtain idempotency - two companies may legitimately share a name."""
    user = user_factory()

    first = create_organization(
        user_id=user.id, name="Common Name Co", idempotency_key="a"
    )
    second = create_organization(
        user_id=user.id, name="Common Name Co", idempotency_key="b"
    )

    assert first.organization_id != second.organization_id
    assert first.slug != second.slug

    from workeasy360.platform.organizations.models import Organization

    assert Organization.objects.filter(display_name="Common Name Co").count() == 2


def test_no_idempotency_key_creates_a_fresh_organization_every_call(user_factory):
    """Idempotency is opt-in: a caller that supplies no key gets ordinary
    create-every-time behaviour, matching a plain POST with no
    Idempotency-Key header."""
    user = user_factory()
    first = create_organization(user_id=user.id, name="No Key Co")
    second = create_organization(user_id=user.id, name="No Key Co")
    assert first.organization_id != second.organization_id


# --- slug generation (Task 7) ----------------------------------------------


def test_slug_collisions_are_resolved_server_side(user_factory):
    user_a = user_factory()
    user_b = user_factory()

    first = create_organization(user_id=user_a.id, name="Acme")
    second = create_organization(user_id=user_b.id, name="Acme")

    assert first.slug != second.slug
    assert first.slug.startswith("acme")
    assert second.slug.startswith("acme")


def test_reserved_slugs_are_never_issued(user_factory):
    user = user_factory()
    result = create_organization(user_id=user.id, name="Admin")
    assert result.slug != "admin"


# --- remove_member (Task 20/32/67's minimal lifecycle mutation) ------------


def test_remove_member_deletes_the_projection_row(
    organization_factory, user_factory, tenant_ctx_factory, grant_permissions, in_tenant
):
    from workeasy360.platform.onboarding.services import remove_member
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import (
        Membership,
        UserOrganizationAccess,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org, owner_membership, owner_user = organization_factory()
    other_user = user_factory()

    with (
        unscoped_access(reason="Test seeds a second member directly", module="tests"),
        organization_db_scope(org.id),
    ):
        member = Membership(
            organization_id=org.id,
            user_id=other_user.id,
            status="active",
            member_type="internal",
        )
        member.save()
        projection.sync_access_for_membership(
            user_id=other_user.id, membership=member, organization=org
        )

    assert UserOrganizationAccess.objects.filter(
        user_id=other_user.id, organization_id=org.id
    ).exists()

    grant_permissions(org, owner_membership, ["platform.member.remove"])
    ctx = tenant_ctx_factory(org, owner_membership, owner_user, aal=2)

    with in_tenant(ctx):
        remove_member(ctx, member.id)

    assert not UserOrganizationAccess.objects.filter(
        user_id=other_user.id, organization_id=org.id
    ).exists()
    with (
        unscoped_access(reason="Test reads the membership back directly", module="tests"),
        organization_db_scope(org.id),
    ):
        assert Membership.unscoped.get(id=member.id).status == "removed"


def test_remove_member_refuses_to_remove_the_owner(
    organization_factory, tenant_ctx_factory, grant_permissions, in_tenant
):
    org, membership, user = organization_factory()
    grant_permissions(org, membership, ["platform.member.remove"])
    ctx = tenant_ctx_factory(org, membership, user, aal=2)

    with in_tenant(ctx), pytest.raises(OrganizationValidationError):
        remove_member(ctx, membership.id)


def test_remove_member_requires_the_permission(
    organization_factory, tenant_ctx_factory, in_tenant
):
    from workeasy360.contracts.errors import PermissionDenied

    org, membership, user = organization_factory()
    ctx = tenant_ctx_factory(org, membership, user, aal=2)

    with in_tenant(ctx), pytest.raises(PermissionDenied):
        remove_member(ctx, membership.id)
