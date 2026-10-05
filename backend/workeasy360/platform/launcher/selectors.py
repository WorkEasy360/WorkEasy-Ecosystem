"""Read models for the launcher: the app directory and the seat-assignment
page. Both return DTOs, never model instances (F-6) - the same rule every
other selector in this codebase follows.

**Caching, deliberately absent here.** Task 30 draws the line this module
sits exactly on: catalog metadata (name, description, licensing mode) is
slow-changing and could be cached, but the *state* every function here
returns - entitlement, seat, integration health - is the security-sensitive
current answer, and "never serve stale security decisions merely for speed"
(Task 30) means this module computes it live, every call, through
``platform.launcher.access.can_launch_product``. If a future profiling pass
finds this too slow, the fix is caching the *catalog half* inside
``commercial.catalog`` behind its own port - never memoising the decision
this module exists to keep honest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.launcher.access import AppAccessDecision, can_launch_product


def app_directory(ctx: TenantContext) -> list[AppAccessDecision]:
    """Every marketable product, with this member's access state against
    each one. Task 6/7/21: data-driven, no hardcoded product list - the set
    of products is exactly what ``ProductDirectory.list_marketable()``
    returns, and nothing here branches on which product code it is looking
    at (Task 22).
    """
    from workeasy360.contracts.ports.catalog import get_product_directory

    return [
        # The descriptor is handed straight back rather than re-read per
        # product: ``list_marketable()`` already fetched every one of them in
        # a single query, and letting the decision re-fetch each was half the
        # directory's entire query cost. See ``can_launch_product`` for why
        # this changes no answer.
        can_launch_product(ctx, str(descriptor.code), descriptor=descriptor)
        for descriptor in get_product_directory().list_marketable()
    ]


@dataclass(frozen=True, slots=True)
class SeatMemberRow:
    """One member's seat state, for the assignment UI (Task 13)."""

    membership_id: str
    email: str
    held: bool


@dataclass(frozen=True, slots=True)
class SeatOverviewDTO:
    """Everything Task 13's "CRM — Seats: 8 / 10, Aman ✓, Rahul ✓, Priya —"
    view needs, for one product."""

    product_code: str
    display_name: str
    licensing_mode: str
    seats_total: int
    seats_assigned: int
    members: list[SeatMemberRow]
    next_cursor: str | None
    has_more: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_code": self.product_code,
            "display_name": self.display_name,
            "licensing_mode": self.licensing_mode,
            "seats_total": self.seats_total,
            "seats_assigned": self.seats_assigned,
            "members": [
                {"membership_id": m.membership_id, "email": m.email, "held": m.held}
                for m in self.members
            ],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
        }


def seat_overview(
    ctx: TenantContext,
    product_code: str,
    *,
    limit: int = 100,
    cursor: str | None = None,
) -> SeatOverviewDTO:
    """Seat capacity and per-member assignment for one product.

    Authorized here, not in the view (F-7, C7): the same rule
    ``ecosystem.selectors.integrations`` already follows, for the identical
    reason - a view-only check is bypassed by any other internal caller.
    """
    from workeasy360.contracts.ports.catalog import get_product_directory
    from workeasy360.contracts.values.identifiers import ProductCode
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.entitlements.services import seats as seat_services
    from workeasy360.platform.organizations import selectors as org_selectors

    authz.require(ctx, "platform.seat.assign")

    descriptor = get_product_directory().get(ProductCode(product_code))
    display_name = descriptor.display_name if descriptor else str(product_code)
    licensing_mode = descriptor.licensing_mode.value if descriptor else ""

    total = seat_services.capacity(ctx.organization_id, pool_key=str(product_code))
    held_ids = {
        str(mid)
        for mid in seat_services.held_by(ctx.organization_id, pool_key=str(product_code))
    }

    page = org_selectors.membership_summaries(ctx, limit=limit, cursor=cursor)
    members = [
        SeatMemberRow(
            membership_id=row["membership_id"],
            email=row["email"],
            held=row["membership_id"] in held_ids,
        )
        for row in page.items
    ]

    return SeatOverviewDTO(
        product_code=str(product_code),
        display_name=display_name,
        licensing_mode=licensing_mode,
        seats_total=total,
        seats_assigned=len(held_ids),
        members=members,
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )
