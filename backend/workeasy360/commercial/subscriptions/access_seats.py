"""One member's seat in one product's pool, as an outcome (ADR-078).

The trial's own starter seat (``trials``) reports *why* a seat was not given -
no pool yet, already seated, full - so the automatic activation can say "Retry
Setup" or "Ask your owner" instead of failing silently. A paid order's buyer is
seated by ``payments.orders.seat_the_buyer`` (ADR-073), which this mirrors.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.contracts.errors import SeatLimitReached
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

SEATED = "seated"
ALREADY_SEATED = "already_seated"
NO_SEAT_POOL = "no_seat_pool"
SEATS_FULL = "seats_full"


def seat_member(
    organization_id: UUID, pool_key: str, membership_id: UUID
) -> tuple[str, Any]:
    """One member, one pool: ``(outcome, assignment or None)``. The caller is
    inside the organization's scope and records the audit entry."""
    from workeasy360.platform.entitlements.services import seats

    if seats.capacity(organization_id, pool_key=pool_key) == 0:
        return NO_SEAT_POOL, None
    if membership_id in seats.held_by(organization_id, pool_key=pool_key):
        return ALREADY_SEATED, None
    try:
        assignment = seats.assign_seat(
            organization_id,
            pool_key=pool_key,
            membership_id=membership_id,
            assigned_by_membership_id=membership_id,
        )
    except SeatLimitReached:
        return SEATS_FULL, None
    return SEATED, assignment
