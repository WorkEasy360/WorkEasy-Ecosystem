"""The single-writer boundary for organizations and memberships (D-032,
closed in the Phase 9 closure), at runtime.

The architecture test proves it statically. The suite-wide guard
(``tests/write_ownership.py``) proves it for every write the suite executes.
These tests prove the guard itself: a write from another module is refused
however it is made, and the boundary's own writes pass - with the chooser
projection kept in step, which is now the boundary's job rather than every
caller's.
"""

from __future__ import annotations

import textwrap

import pytest

from tests import write_ownership

pytestmark = pytest.mark.django_db

ROGUE = textwrap.dedent(
    """
    def rename(organization_id):
        Organization.objects.filter(id=organization_id).update(display_name="Hijacked")

    def suspend(member):
        member.status = "suspended"
        member.save(update_fields=["status"])

    def add(organization_id, user_id):
        Membership(organization_id=organization_id, user_id=user_id).save()

    def remove(member):
        member.delete()
    """
)


def _rogue_module():
    """Functions whose frames say they live in a production module outside
    the owner, which is all the guard looks at."""
    from workeasy360.platform.organizations.models import Membership, Organization

    namespace = {
        "__name__": "workeasy360.commercial.rogue",
        "Organization": Organization,
        "Membership": Membership,
    }
    exec(compile(ROGUE, "rogue.py", "exec"), namespace)
    return namespace


def _join(services, org_id, user_id):
    """As the invitation flow joins: before any tenant context exists, in the
    organization's database scope."""
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test joins a member as an invitee would", module="tests"),
        organization_db_scope(org_id),
    ):
        return services.join_member(org_id, user_id)


def test_the_guard_is_installed_for_the_whole_session():
    from django.db.models import QuerySet

    assert QuerySet.update.__module__ == write_ownership.__name__
    assert "organizations.Organization" in write_ownership.OWNERS


def test_a_queryset_update_from_another_module_is_refused(organization_factory):
    org, _membership, _user = organization_factory()
    rogue = _rogue_module()
    with write_ownership.expect_violation() as caught:
        with pytest.raises(AssertionError, match="single writer"):
            rogue["rename"](org.id)
    assert caught == [
        "organizations.Organization update from workeasy360.commercial.rogue; "
        "only workeasy360.platform.organizations.services writes it (single writer)"
    ]


def test_an_instance_save_from_another_module_is_refused(
    organization_factory, tenant_ctx_factory, in_tenant, user_factory
):
    """The case a static scan cannot follow: a row obtained elsewhere,
    changed and saved."""
    org, membership, user = organization_factory()
    rogue = _rogue_module()
    with in_tenant(tenant_ctx_factory(org, membership, user)):
        with write_ownership.expect_violation() as caught:
            with pytest.raises(AssertionError, match="single writer"):
                rogue["suspend"](membership)
            with pytest.raises(AssertionError, match="single writer"):
                rogue["add"](org.id, user_factory().id)
            with pytest.raises(AssertionError, match="single writer"):
                rogue["remove"](membership)
    assert [line.split(" from ")[0] for line in caught] == [
        "organizations.Membership save",
        "organizations.Membership save",
        "organizations.Membership delete",
    ]


def test_the_boundary_writes_and_keeps_the_projection_in_step(
    organization_factory, tenant_ctx_factory, in_tenant, user_factory
):
    """Suspend and reactivate a member, rename the organization - through the
    boundary, with nothing else called: the chooser follows each change."""
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations import services
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    org, owner, owner_user = organization_factory()
    ctx = tenant_ctx_factory(org, owner, owner_user)
    person = user_factory()
    with in_tenant(ctx):
        member, joined = _join(services, org.id, person.id)
        assert joined and member.status is MembershipStatus.ACTIVE
        assert UserOrganizationAccess.objects.filter(user_id=person.id).exists()

        suspended = services.set_member_status(
            org.id,
            member.membership_id,
            target=MembershipStatus.SUSPENDED,
            actor_membership_id=owner.id,
        )
        assert suspended.status is MembershipStatus.SUSPENDED
        assert not UserOrganizationAccess.objects.filter(user_id=person.id).exists()

        services.set_member_status(
            org.id,
            member.membership_id,
            target=MembershipStatus.ACTIVE,
            actor_membership_id=owner.id,
        )
        assert services.rename_organization(org.id, "Renamed Ltd") == (
            org.display_name,
            "Renamed Ltd",
        )
        row = UserOrganizationAccess.objects.get(user_id=person.id)
        assert row.organization_display_name == "Renamed Ltd"


def test_the_boundary_keeps_the_owner_in_control(
    organization_factory, tenant_ctx_factory, in_tenant, user_factory
):
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.contracts.errors import MembershipConflict
    from workeasy360.platform.organizations import services

    org, owner, owner_user = organization_factory()
    person = user_factory()
    with in_tenant(tenant_ctx_factory(org, owner, owner_user)):
        member, _ = _join(services, org.id, person.id)
        with pytest.raises(MembershipConflict, match="owner cannot be suspended"):
            services.set_member_status(
                org.id,
                owner.id,
                target=MembershipStatus.SUSPENDED,
                actor_membership_id=member.membership_id,
            )
        with pytest.raises(MembershipConflict, match="cannot suspend yourself"):
            services.set_member_status(
                org.id,
                member.membership_id,
                target=MembershipStatus.SUSPENDED,
                actor_membership_id=member.membership_id,
            )
        with pytest.raises(
            MembershipConflict, match="Only the owner can sign the owner out"
        ):
            services.cut_off_member_sessions(
                org.id,
                owner.id,
                actor_membership_id=member.membership_id,
                at=org.created_at,
            )
