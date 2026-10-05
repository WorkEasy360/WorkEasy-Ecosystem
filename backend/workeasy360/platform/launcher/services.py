"""Write operations for the launcher: seat assignment and the launch
decision. Both are application services in the sense every other module in
this codebase uses the term - authorization and audit live here, never in
the view (F-7, C7).
"""

from __future__ import annotations

from typing import Any

from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.launcher.access import AppAccessDecision, can_launch_product


def launch(ctx: TenantContext, product_code: str) -> AppAccessDecision:
    """Re-run the authoritative access decision immediately before a browser
    is redirected at a product (Task 16).

    No caching, no trusting a decision the frontend rendered a moment ago:
    this is *why* Task 9 asked for one decision function rather than one per
    caller - the launcher's directory and this re-check must be structurally
    incapable of disagreeing, and the only way to guarantee that is for both
    to call the same function rather than two that are meant to agree.
    """
    return can_launch_product(ctx, product_code)


def assign_seat(
    ctx: TenantContext, *, product_code: str, membership_id: Any
) -> dict[str, Any]:
    """Give a member a seat, or refuse. Task 13.

    Authorization and audit both live here rather than in the view - the
    same shape ``platform.ecosystem.services`` already uses for every
    control-plane write, for the identical reason (F-7: a view-only check is
    bypassed by any other internal caller).
    """
    from workeasy360.contracts.enums import AuditOutcome
    from workeasy360.contracts.errors import MembershipRequired, SeatLimitReached
    from workeasy360.platform.audit import services as audit
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.entitlements.services import seats as seat_services
    from workeasy360.platform.organizations import selectors as org_selectors

    authz.require(ctx, "platform.seat.assign")

    if not org_selectors.active_membership_exists(ctx, membership_id, lock=True):
        # ``membership_id`` comes straight off the URL, so it can name any
        # UUID at all - another organization's member, or nothing. The
        # composite, deferred foreign key would refuse the row either way,
        # but only at COMMIT: the view would have already answered 201 and
        # the request would then die as a 500 on its way out (see
        # ``active_membership_exists`` for why no test would ever see that).
        # Refused here instead, uniformly - "not a member of this
        # organization" and "no such membership" are deliberately the same
        # answer, so this endpoint cannot be used to probe for membership
        # ids.
        raise MembershipRequired(
            "That member does not belong to this organization.",
            product_code=str(product_code),
        )

    try:
        assignment = seat_services.assign_seat(
            ctx.organization_id,
            pool_key=str(product_code),
            membership_id=membership_id,
            assigned_by_membership_id=ctx.membership_id,
        )
    except SeatLimitReached as exc:
        audit.record(
            ctx,
            "launcher.seat.assign_denied",
            resource_type="SeatAssignment",
            resource_id=str(membership_id),
            changes={"product_code": str(product_code), "reason": "seat_limit_reached"},
            outcome=AuditOutcome.DENIED,
        )
        raise exc

    audit.record(
        ctx,
        "launcher.seat.assigned",
        resource_type="SeatAssignment",
        resource_id=str(assignment.id),
        changes={"product_code": str(product_code), "membership_id": str(membership_id)},
    )
    return {"membership_id": str(membership_id), "product_code": str(product_code)}


def release_seat(
    ctx: TenantContext, *, product_code: str, membership_id: Any
) -> dict[str, Any]:
    """Release a member's seat. Idempotent - releasing an unheld seat is a
    no-op, not an error (the admin UI's unassign button should never fail
    because someone clicked it twice)."""
    from workeasy360.platform.audit import services as audit
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.entitlements.services import seats as seat_services

    authz.require(ctx, "platform.seat.assign")

    released = seat_services.release_seat(
        ctx.organization_id, pool_key=str(product_code), membership_id=membership_id
    )
    audit.record(
        ctx,
        "launcher.seat.released",
        resource_type="SeatAssignment",
        resource_id=str(membership_id),
        changes={"product_code": str(product_code), "released": bool(released)},
    )
    return {
        "membership_id": str(membership_id),
        "product_code": str(product_code),
        "released": bool(released),
    }
