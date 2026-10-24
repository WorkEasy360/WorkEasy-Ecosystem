"""Members: the directory, roles, suspension, and signing a member out.

Every command authorizes in the service (never only in the view) and writes
its audit entry in the same transaction as the change. The membership row
itself is changed by ``platform.organizations.services`` - its one writer -
which locks it and refuses the changes that would leave the organization
without its owner's control:

- the owner's role, status and sessions are not the console's to change -
  ownership moves by transfer, which is a separate, owner-only flow;
- a member cannot change their own role or suspend themselves (the same
  mistake, made on oneself, has no one left to undo it).

Removing a member remains ``platform.onboarding.services.remove_member``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.enums import MembershipStatus
from workeasy360.contracts.errors import (
    MembershipConflict,
    MembershipValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.metrics import metrics


def directory(
    ctx: TenantContext,
    *,
    status: str | None = None,
    search: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> Any:
    """Members with their system roles. ``platform.member.view``."""
    from workeasy360.platform.organizations.selectors import member_directory

    authz.require(ctx, "platform.member.view")
    page = member_directory(ctx, status=status, search=search, limit=limit, cursor=cursor)
    roles = authz.system_role_keys([item["membership_id"] for item in page.items])
    for item in page.items:
        item["roles"] = roles.get(item["membership_id"], [])
        if item["is_owner"] and "owner" not in item["roles"]:
            item["roles"] = ["owner", *item["roles"]]
    return page


def change_role(
    ctx: TenantContext, membership_id: UUID, *, role_key: str
) -> dict[str, Any]:
    """Give a member exactly one system role. ``platform.role.assign``
    (dangerous: refused under impersonation)."""
    from workeasy360.platform.organizations.selectors import member
    from workeasy360.platform.organizations.services import lock_member

    authz.require(ctx, "platform.role.assign")
    if role_key not in authz.ASSIGNABLE_SYSTEM_ROLES:
        raise MembershipValidationError(
            "Choose one of: " + ", ".join(authz.ASSIGNABLE_SYSTEM_ROLES) + ".",
            field="role",
        )
    with transaction.atomic():
        # Locked, so two administrators changing one member's role serialise.
        target = lock_member(ctx.organization_id, membership_id)
        if target.is_owner:
            raise MembershipConflict("The owner's role cannot be changed.")
        if target.membership_id == ctx.membership_id:
            raise MembershipConflict("You cannot change your own role.")
        if target.status not in (MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED):
            raise MembershipConflict("Only an active or suspended member has a role.")
        previous = authz.replace_system_role(
            organization_id=ctx.organization_id,
            membership_id=target.membership_id,
            role_key=role_key,
        )
        audit.record(
            ctx,
            "membership.role_changed",
            resource_type="organizations.membership",
            resource_id=target.membership_id,
            changes={"from": previous, "to": [role_key]},
        )
    metrics.increment("organization_member_role_changed_total")
    row = member(ctx, membership_id) or {}
    row["roles"] = [role_key]
    return row


def suspend(ctx: TenantContext, membership_id: UUID) -> dict[str, Any]:
    """Suspend: the member keeps their account and history but cannot open the
    organization, and their seats are released for someone else.
    ``platform.member.update``."""
    from workeasy360.platform.entitlements.services import seats
    from workeasy360.platform.organizations.selectors import member
    from workeasy360.platform.organizations.services import set_member_status

    authz.require(ctx, "platform.member.update")
    with transaction.atomic():
        # The membership and its chooser row; the owner and the actor refused.
        suspended = set_member_status(
            ctx.organization_id,
            membership_id,
            target=MembershipStatus.SUSPENDED,
            actor_membership_id=ctx.membership_id,
        )
        released = seats.release_all_seats(
            ctx.organization_id, membership_id=suspended.membership_id
        )
        audit.record(
            ctx,
            "membership.suspended",
            resource_type="organizations.membership",
            resource_id=suspended.membership_id,
            changes={"seats_released": released},
        )
    metrics.increment("organization_member_suspended_total")
    return member(ctx, membership_id) or {}


def reactivate(ctx: TenantContext, membership_id: UUID) -> dict[str, Any]:
    """Undo a suspension. Seats are not restored automatically - they may
    since have been given to someone else. ``platform.member.update``."""
    from workeasy360.platform.organizations.selectors import member
    from workeasy360.platform.organizations.services import set_member_status

    authz.require(ctx, "platform.member.update")
    with transaction.atomic():
        reactivated = set_member_status(
            ctx.organization_id,
            membership_id,
            target=MembershipStatus.ACTIVE,
            actor_membership_id=ctx.membership_id,
        )
        audit.record(
            ctx,
            "membership.reactivated",
            resource_type="organizations.membership",
            resource_id=reactivated.membership_id,
        )
    return member(ctx, membership_id) or {}


def sign_out(ctx: TenantContext, membership_id: UUID) -> dict[str, Any]:
    """End every session the member has *for this organization*: any session
    signed in before now is refused here until they sign in again. Their
    sessions elsewhere are theirs and untouched. ``platform.member.sign_out``
    (dangerous: audited). Only the owner may sign the owner out."""
    from workeasy360.platform.organizations.selectors import member
    from workeasy360.platform.organizations.services import cut_off_member_sessions

    authz.require(ctx, "platform.member.sign_out")
    with transaction.atomic():
        now = timezone.now()
        target = cut_off_member_sessions(
            ctx.organization_id,
            membership_id,
            actor_membership_id=ctx.membership_id,
            at=now,
        )
        audit.record(
            ctx,
            "membership.signed_out",
            resource_type="organizations.membership",
            resource_id=target.membership_id,
            changes={"signed_out_at": now.isoformat()},
        )
    metrics.increment("organization_member_signed_out_total")
    return member(ctx, membership_id) or {}
