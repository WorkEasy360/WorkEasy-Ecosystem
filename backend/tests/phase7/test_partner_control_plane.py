"""Super Admin boundary and the partner capability model (Phase 7).

Real database, real RLS. Each test states the property it defends.
"""

from __future__ import annotations

import pytest

from workeasy360.commercial.partners import capabilities, selectors, services
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import (
    ControlPlaneInvariantViolation,
    InvalidStateTransition,
    PartnerRecordNotFound,
    PartnerValidationError,
)
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz import staff
from workeasy360.platform.authz.control_plane import platform_context

pytestmark = pytest.mark.django_db


# --- the staff boundary ---------------------------------------------------


def test_a_staff_member_holds_only_the_grants_they_were_given(staff_factory):
    _user, ctx = staff_factory("platform.staff_customer.view")
    assert authz.check(ctx, "platform.staff_customer.view").allowed
    assert not authz.check(ctx, "platform.staff_partner.manage").allowed


def test_django_superuser_confers_nothing(user_factory):
    user = user_factory(is_staff=True, is_superuser=True)
    ctx = platform_context(user_id=user.id)
    assert not authz.check(ctx, "platform.staff_partner.manage").allowed


def test_a_grant_on_a_non_staff_account_is_inert(user_factory, staff_factory):
    root, _ = staff_factory()
    plain = user_factory()  # not staff
    with pytest.raises(ControlPlaneInvariantViolation):
        staff.create_grant(
            user_id=plain.id,
            permission="platform.staff_customer.view",
            granted_by_id=root.id,
            reason="Should be refused outright",
        )


def test_staff_permissions_cannot_be_granted_by_role_or_to_customers(db):
    from workeasy360.platform.authz.models import RolePermission
    from workeasy360.platform.authz.registry import permission_registry

    control_plane = {str(d.key) for d in permission_registry.all() if d.control_plane}
    assert control_plane, "no control-plane permissions declared"
    granted = set(
        RolePermission.objects.filter(permission_id__in=control_plane).values_list(
            "permission_id", flat=True
        )
    )
    assert granted == set(), f"a role grants control-plane permissions: {granted}"


def test_every_staff_write_permission_is_dangerous():
    from workeasy360.platform.authz.registry import permission_registry

    for declaration in permission_registry.all():
        key = str(declaration.key)
        if key.startswith("platform.staff_") and key.endswith(".manage"):
            assert declaration.dangerous, key
        if key.startswith("platform.staff_payout.approve"):
            assert declaration.dangerous


def test_a_revoked_staff_grant_stops_working_at_once(staff_factory, user_factory):
    security_user, security_ctx = staff_factory("platform.staff_security.manage")
    other, other_ctx = staff_factory()
    grant, _ = staff.create_grant(
        user_id=other.id,
        permission="platform.staff_customer.view",
        granted_by_id=security_user.id,
        reason="Temporary access for a test",
    )
    assert authz.check(other_ctx, "platform.staff_customer.view").allowed
    staff.end_grant(
        grant_id=grant.id,
        revoked_by_id=security_user.id,
        reason="Access no longer needed",
    )
    assert not authz.check(other_ctx, "platform.staff_customer.view").allowed


def test_the_last_security_administrator_cannot_be_removed(staff_factory):
    admin, _ = staff_factory("platform.staff_security.manage")
    grant = staff.live_grants(user_id=admin.id)[0]
    with pytest.raises(ControlPlaneInvariantViolation):
        staff.end_grant(
            grant_id=grant["id"],
            revoked_by_id=admin.id,
            reason="Trying to lock everyone out",
        )


def test_grants_reject_non_control_plane_and_unknown_keys(staff_factory):
    root, _ = staff_factory()
    target, _ = staff_factory()
    for bad in (
        "platform.member.invite",
        "made.up.permission",
        "commercial.catalog.manage",
    ):
        with pytest.raises(ControlPlaneInvariantViolation):
            staff.create_grant(
                user_id=target.id,
                permission=bad,
                granted_by_id=root.id,
                reason="Should not be grantable",
            )


# --- partner lifecycle ----------------------------------------------------


def test_a_partner_starts_in_draft_and_cannot_act(staff_factory, user_factory):
    _u, sctx = staff_factory("platform.staff_partner.manage")
    partner = services.create_partner(
        sctx, code="draft-co", display_name="Draft Co", payout_currency="GBP"
    )
    assert partner.status == "draft"
    member = user_factory()
    services.add_member(sctx, partner.id, email=member.email, role="admin")
    services.grant_capability(
        sctx, partner.id, capability="customer.provision", reason="Fixture capability"
    )
    from workeasy360.commercial.partners.access import partner_actor

    actor = partner_actor(user_id=member.id, partner_id=partner.id)
    assert not authz.check(actor, "platform.partner_customer.provision").allowed


def test_activation_needs_an_administrator(staff_factory):
    _u, sctx = staff_factory("platform.staff_partner.manage")
    partner = services.create_partner(
        sctx, code="no-admin", display_name="No Admin", payout_currency="GBP"
    )
    with pytest.raises(PartnerValidationError):
        services.set_partner_status(
            sctx, partner.id, target=PartnerStatus.ACTIVE, reason="Activating with nobody"
        )


def test_terminated_is_terminal_and_transitions_are_a_table(partner_factory):
    fx = partner_factory()
    sctx = fx["staff_ctx"]
    services.set_partner_status(
        sctx,
        fx["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Contract ended today",
    )
    with pytest.raises(InvalidStateTransition):
        services.set_partner_status(
            sctx,
            fx["partner"].id,
            target=PartnerStatus.ACTIVE,
            reason="Trying to come back",
        )


def test_stale_version_is_refused_not_overwritten(partner_factory):
    fx = partner_factory()
    sctx = fx["staff_ctx"]
    version = fx["partner"].version
    services.update_partner(
        sctx, fx["partner"].id, expected_version=version, display_name="One"
    )
    with pytest.raises(services.ConcurrentPartnerModification):
        services.update_partner(
            sctx, fx["partner"].id, expected_version=version, display_name="Two"
        )


def test_partner_code_is_validated_and_unique(staff_factory):
    _u, sctx = staff_factory("platform.staff_partner.manage")
    with pytest.raises(PartnerValidationError):
        services.create_partner(
            sctx, code="Bad Code!", display_name="X", payout_currency="GBP"
        )
    services.create_partner(
        sctx, code="unique-one", display_name="X", payout_currency="GBP"
    )
    with pytest.raises(PartnerValidationError):
        services.create_partner(
            sctx, code="unique-one", display_name="Y", payout_currency="GBP"
        )


# --- capabilities ---------------------------------------------------------


def test_capabilities_are_an_allowlist(partner_factory):
    fx = partner_factory()
    with pytest.raises(PartnerValidationError):
        services.grant_capability(
            fx["staff_ctx"],
            fx["partner"].id,
            capability="root.everything",
            reason="Nope nope nope",
        )


def test_every_partner_permission_is_reachable_from_a_capability():
    from workeasy360.commercial.partners.permissions import PARTNER_PERMISSIONS

    declared = {str(d.key) for d in PARTNER_PERMISSIONS}
    assert declared == set(capabilities.all_partner_permission_keys())


def test_partner_permissions_are_never_grantable_to_customer_roles():
    from workeasy360.platform.authz.registry import permission_registry

    for key in capabilities.all_partner_permission_keys():
        assert permission_registry.get(key).control_plane


def test_capability_combination_is_composable_not_typed(partner_factory):
    fx = partner_factory("customer.provision", "commission.earn")
    actor = fx["actor"]()
    assert authz.check(actor, "platform.partner_customer.provision").allowed
    assert not authz.check(actor, "platform.partner_subscription.sell").allowed
    assert not authz.check(actor, "platform.partner_branding.manage").allowed
    # Earning is passive: it unlocks no permission at all.
    assert capabilities.CAPABILITIES["commission.earn"].view == ()


def test_revoking_a_capability_applies_to_the_next_check(partner_factory):
    fx = partner_factory("branding.manage")
    actor = fx["actor"]()
    assert authz.check(actor, "platform.partner_branding.manage").allowed
    services.revoke_capability(
        fx["staff_ctx"],
        fx["partner"].id,
        capability="branding.manage",
        reason="Programme change",
    )
    assert not authz.check(actor, "platform.partner_branding.manage").allowed


def test_an_expired_capability_grants_nothing(partner_factory):
    import time
    from datetime import timedelta

    from django.utils import timezone

    fx = partner_factory()
    services.grant_capability(
        fx["staff_ctx"],
        fx["partner"].id,
        capability="customer.provision",
        reason="Time-boxed for the test",
        expires_at=timezone.now() + timedelta(seconds=1),
    )
    actor = fx["actor"]()
    assert authz.check(actor, "platform.partner_customer.provision").allowed
    time.sleep(1.3)
    assert not authz.check(actor, "platform.partner_customer.provision").allowed


def test_a_capability_grant_is_a_frozen_fact(partner_factory):
    from django.db import IntegrityError, transaction

    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    fx = partner_factory("customer.provision")
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerCapabilityGrant.objects.filter(partner=fx["partner"]).update(
            capability="branding.manage"
        )


def test_a_viewer_gets_only_the_read_tier(partner_factory):
    fx = partner_factory("customer.provision", "branding.manage", role="viewer")
    actor = fx["actor"]()
    assert authz.check(actor, "platform.partner_customer.view").allowed
    assert authz.check(actor, "platform.partner_branding.view").allowed
    assert not authz.check(actor, "platform.partner_customer.provision").allowed
    assert not authz.check(actor, "platform.partner_branding.manage").allowed


def test_suspension_removes_write_permissions_but_keeps_history_readable(partner_factory):
    fx = partner_factory("customer.provision", "commission.view")
    actor = fx["actor"]()
    services.set_partner_status(
        fx["staff_ctx"],
        fx["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Under review now",
    )
    assert not authz.check(actor, "platform.partner_customer.provision").allowed
    assert authz.check(actor, "platform.partner_customer.view").allowed
    assert authz.check(actor, "platform.partner_commission.view").allowed


def test_a_terminated_partner_keeps_only_money_visibility(partner_factory):
    fx = partner_factory("customer.provision", "commission.view", "payout.view")
    actor = fx["actor"]()
    services.set_partner_status(
        fx["staff_ctx"],
        fx["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Contract ended now",
    )
    assert not authz.check(actor, "platform.partner_customer.view").allowed
    assert authz.check(actor, "platform.partner_payout.view").allowed


def test_removing_a_partner_user_revokes_access_immediately(partner_factory):
    from workeasy360.commercial.partners.models import PartnerMember

    fx = partner_factory("customer.provision")
    actor = fx["actor"]()
    assert authz.check(actor, "platform.partner_customer.provision").allowed
    member = PartnerMember.objects.get(partner=fx["partner"], user=fx["user"])
    services.remove_member(fx["staff_ctx"], fx["partner"].id, member_id=member.id)
    assert not authz.check(actor, "platform.partner_customer.provision").allowed


def test_membership_of_a_customer_org_grants_no_partner_authority(
    partner_factory, organization_factory
):
    from workeasy360.commercial.partners.access import partner_actor

    fx = partner_factory("customer.provision")
    org, _membership, owner = organization_factory()
    # The owner of an unrelated customer is not a partner user of anyone.
    with pytest.raises(PartnerRecordNotFound):
        partner_actor(user_id=owner.id, partner_id=fx["partner"].id)


def test_a_guessed_partner_id_is_indistinguishable_from_no_partner(
    partner_factory, user_factory
):
    import uuid

    from workeasy360.commercial.partners.access import partner_actor

    fx = partner_factory("customer.provision")
    stranger = user_factory()
    with pytest.raises(PartnerRecordNotFound):
        partner_actor(user_id=stranger.id, partner_id=fx["partner"].id)
    with pytest.raises(PartnerRecordNotFound):
        partner_actor(user_id=stranger.id, partner_id=uuid.uuid4())


def test_partner_actions_do_not_reach_staff_permissions(partner_factory):
    fx = partner_factory("customer.provision")
    assert not authz.check(fx["actor"](), "platform.staff_partner.manage").allowed


def test_a_staff_context_does_not_hold_partner_permissions(staff_factory):
    _u, ctx = staff_factory("platform.staff_partner.manage")
    assert not authz.check(ctx, "platform.partner_customer.provision").allowed


def test_partner_directory_is_paginated_and_bounded(staff_factory):
    _u, sctx = staff_factory("platform.staff_partner.manage")
    for n in range(7):
        services.create_partner(
            sctx, code=f"page-{n}", display_name=f"P{n}", payout_currency="GBP"
        )
    first = selectors.partners_page(limit=3)
    assert len(first.items) == 3 and first.has_more
    second = selectors.partners_page(limit=3, cursor=first.next_cursor)
    assert {i["id"] for i in first.items}.isdisjoint({i["id"] for i in second.items})
    assert len(selectors.partners_page(limit=100000).items) <= 100


def test_partner_actions_are_audited_in_the_platform_log(partner_factory):
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    fx = partner_factory("customer.provision")
    with (
        unscoped_access(reason="Test reads the control-plane audit log", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        actions = set(
            AuditEvent.unscoped.filter(
                organization_id=PLATFORM_ORGANIZATION_ID
            ).values_list("action", flat=True)
        )
    assert {"partner.created", "partner.capability_granted", "partner.active"} <= actions
    assert fx
