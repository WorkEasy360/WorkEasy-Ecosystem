"""Resolving a partner principal's permission set (registered with authz).

Computed from the database on **every** check, never cached. The set depends
on four things that can each change at any moment - the partner's status, the
user's membership, the partner's active capability grants and their expiry -
and the requirement is that a change applies to the very next action
(Task 61, 65). One indexed query per fact is a fair price on a low-volume
surface for being able to say that without qualification.

What the set contains, by partner status:

===========  ==========================================================
active       every permission the capabilities unlock (admin) or their
             read-only subset (viewer)
suspended    the read-only subset only: history stays visible, nothing new
             can be done (Task 62)
terminated   only commission and payout visibility, so money owed remains
             readable after the relationship ends
draft        nothing - a partner that has not been activated cannot act
===========  ==========================================================
"""

from __future__ import annotations

from workeasy360.commercial.partners import capabilities, selectors
from workeasy360.contracts.enums import PartnerMemberRole, PartnerStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz.registry import permission_registry


def partner_permissions(ctx: TenantContext) -> frozenset[str]:
    if ctx.partner_id is None:
        return frozenset()
    partner = selectors.partner_row(ctx.partner_id)
    if partner is None:
        return frozenset()
    member = selectors.member_of(ctx.partner_id, ctx.user_id)
    if member is None:
        return frozenset()

    status = partner.status_enum
    if status is PartnerStatus.DRAFT:
        return frozenset()

    held = selectors.active_capabilities(ctx.partner_id)
    is_admin = member.role == PartnerMemberRole.ADMIN.value

    if status is PartnerStatus.ACTIVE:
        keys = capabilities.permissions_for(held, admin=is_admin)
    elif status is PartnerStatus.SUSPENDED:
        keys = capabilities.permissions_for(held, admin=False)
    else:  # terminated
        keys = (
            capabilities.permissions_for(held, admin=False)
            & capabilities.HISTORY_ONLY_PERMISSIONS
        )
    return permission_registry.expand(keys)
