"""Seat assignment - who occupies a licensed seat.

**Deliberately separate from ``services/write.py``.** Two different questions
with two different writers:

- *How many seats does this organization have?* is a commercial fact derived
  from subscriptions. Capacity (``SeatGrant``) is written only by
  ``commercial/provisioning``, and the single-writer import contract names
  ``services.write`` for exactly that.
- *Which member holds one?* is a customer decision made by an administrator
  through ``platform.seat.assign``. It changes no commercial state and must
  not require a commercial code path.

Conflating them would mean either products could reach a capacity writer, or
a customer administrator assigning a colleague would have to call into
``commercial``. Products may import neither; an import-linter contract names
this module alongside ``services.write`` for the ``products`` source.

**Assignment is the contention point this module exists for.** Two admins
assigning the last seat simultaneously must not both succeed. Over-allocating
paid seats is a revenue leak that is embarrassing to explain, and a counter
cache with optimistic checking cannot prevent it.
"""

from __future__ import annotations

from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.errors import SeatLimitReached
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

SEAT_ASSIGNMENT_FAILURES = "seat_assignment_failures_total"


def capacity(organization_id: UUID, *, pool_key: str) -> int:
    """Total seats in a pool, summed across every granting source.

    Summed rather than maxed: a bundle granting 5 and a standalone plan
    granting 5 genuinely provide 10, and taking the max would quietly
    withhold half of what the customer bought.
    """
    from django.db.models import Sum

    from workeasy360.platform.entitlements.models import SeatGrant

    total = SeatGrant.objects.filter(
        organization_id=organization_id, pool_key=pool_key
    ).aggregate(total=Sum("seats_total"))["total"]
    return int(total or 0)


def assigned_count(organization_id: UUID, *, pool_key: str) -> int:
    """Members currently holding a seat in the pool."""
    from workeasy360.platform.entitlements.models import SeatAssignment

    return SeatAssignment.objects.filter(
        organization_id=organization_id,
        seat_grant__pool_key=pool_key,
        released_at__isnull=True,
    ).count()


def usage_by_pool(organization_id: UUID) -> dict[str, tuple[int, int]]:
    """``{pool_key: (seats_total, seats_assigned)}`` for every pool of the
    organization, in **two** queries however many products it holds - the
    dashboard draws a card per product and must not cost two queries each.

    Same arithmetic as ``capacity`` and ``assigned_count`` (capacity summed
    across sources, assignments not yet released); this is only the batch form.
    """
    from django.db.models import Count, Sum

    from workeasy360.platform.entitlements.models import SeatAssignment, SeatGrant

    totals = {
        row["pool_key"]: int(row["total"] or 0)
        for row in SeatGrant.objects.filter(organization_id=organization_id)
        .values("pool_key")
        .annotate(total=Sum("seats_total"))
    }
    used = {
        row["seat_grant__pool_key"]: int(row["n"])
        for row in SeatAssignment.objects.filter(
            organization_id=organization_id, released_at__isnull=True
        )
        .values("seat_grant__pool_key")
        .annotate(n=Count("id"))
    }
    return {pool: (total, used.get(pool, 0)) for pool, total in totals.items()}


def held_by(organization_id: UUID, *, pool_key: str) -> set[UUID]:
    """Every membership currently holding a seat in the pool.

    For the seat-assignment UI (Task 13): rendering "who has a seat" against
    a member list needs the *set*, not only the count ``assigned_count``
    already answers. One query, one purpose - callers building anything more
    elaborate belong to a selector of their own rather than growing this
    function's return type.
    """
    from workeasy360.platform.entitlements.models import SeatAssignment

    return set(
        SeatAssignment.objects.filter(
            organization_id=organization_id,
            seat_grant__pool_key=pool_key,
            released_at__isnull=True,
        ).values_list("membership_id", flat=True)
    )


def assign_seat(
    organization_id: UUID,
    *,
    pool_key: str,
    membership_id: UUID,
    assigned_by_membership_id: UUID | None = None,
) -> object:
    """Give a member a seat in a pool, or refuse.

    The concurrency guarantee, and why each piece is needed:

    1. ``select_for_update`` on **every** grant in the pool, ordered by id.
       Locking the grants serialises concurrent assignments; ordering by id
       gives a consistent lock order, so two callers working on overlapping
       pools cannot deadlock.
    2. The occupancy count runs **inside the same transaction, after the
       lock**, so it cannot be stale by the time it is acted on.
    3. The partial unique constraint on ``(seat_grant, membership)`` where
       ``released_at IS NULL`` catches the remaining case - the same member
       assigned twice - at the database, because that race is between two
       different rows and no lock ordering prevents it.

    Returns the existing assignment unchanged if the member already holds a
    seat: assignment is idempotent, and a retried request must not consume a
    second seat.
    """
    from workeasy360.platform.entitlements.models import SeatAssignment, SeatGrant

    with transaction.atomic():
        grants = list(
            SeatGrant.objects.select_for_update()
            .filter(organization_id=organization_id, pool_key=pool_key)
            .order_by("id")
        )
        if not grants:
            metrics.increment(SEAT_ASSIGNMENT_FAILURES, reason="no_grant")
            raise SeatLimitReached(
                f"This organization holds no seats in {pool_key!r}.",
                pool=pool_key,
                total=0,
            )

        existing = SeatAssignment.objects.filter(
            organization_id=organization_id,
            membership_id=membership_id,
            seat_grant__pool_key=pool_key,
            released_at__isnull=True,
        ).first()
        if existing is not None:
            return existing

        total = sum(grant.seats_total for grant in grants)
        used = SeatAssignment.objects.filter(
            organization_id=organization_id,
            seat_grant__in=grants,
            released_at__isnull=True,
        ).count()
        if used >= total:
            metrics.increment(SEAT_ASSIGNMENT_FAILURES, reason="pool_full")
            raise SeatLimitReached(
                f"All {total} seat(s) in {pool_key!r} are assigned.",
                pool=pool_key,
                total=total,
            )

        # Fill grants in a stable order so seats are consumed predictably
        # rather than depending on which grant the query happened to return.
        target = _grant_with_room(grants, organization_id)
        try:
            # The savepoint keeps a constraint violation from poisoning the
            # caller's transaction: PostgreSQL aborts the whole transaction
            # on a violation, and without it a routine double-submit would
            # fail the enclosing request rather than returning a clean error.
            with transaction.atomic():
                assignment = SeatAssignment(
                    organization_id=organization_id,
                    seat_grant=target,
                    membership_id=membership_id,
                    assigned_by_id=assigned_by_membership_id,
                )
                assignment.save()
        except IntegrityError:
            already = SeatAssignment.objects.filter(
                organization_id=organization_id,
                membership_id=membership_id,
                seat_grant__pool_key=pool_key,
                released_at__isnull=True,
            ).first()
            if already is not None:
                return already
            raise

    logger.info("entitlement.seat.assigned", pool_key=pool_key)
    return assignment


def _grant_with_room(grants: list, organization_id: UUID) -> object:
    """The first grant in the pool that still has an unused seat."""
    from django.db.models import Count

    from workeasy360.platform.entitlements.models import SeatAssignment

    used_by_grant = dict(
        SeatAssignment.objects.filter(
            organization_id=organization_id,
            seat_grant__in=grants,
            released_at__isnull=True,
        )
        .values_list("seat_grant_id")
        .annotate(used=Count("id"))
    )
    for grant in grants:
        if used_by_grant.get(grant.id, 0) < grant.seats_total:
            return grant
    # Only reachable if capacity changed between the count above and here,
    # which the row locks prevent. Returning the last grant rather than
    # raising would over-allocate, so this is a hard failure.
    raise SeatLimitReached(
        f"No seat available in {grants[0].pool_key!r}.",
        pool=grants[0].pool_key,
        total=sum(g.seats_total for g in grants),
    )


def release_seat(organization_id: UUID, *, pool_key: str, membership_id: UUID) -> int:
    """Release a member's seat. Idempotent; returns how many were released.

    Soft: ``released_at`` is stamped rather than the row deleted, so "who had
    a seat in March?" stays answerable for a licence audit.
    """
    from workeasy360.platform.entitlements.models import SeatAssignment

    released = SeatAssignment.objects.filter(
        organization_id=organization_id,
        membership_id=membership_id,
        seat_grant__pool_key=pool_key,
        released_at__isnull=True,
    ).update(released_at=timezone.now(), over_limit_since=None)
    if released:
        logger.info("entitlement.seat.released", pool_key=pool_key)
    return released


def release_all_seats(organization_id: UUID, *, membership_id: UUID) -> int:
    """Release every seat a member holds, in every pool. Idempotent.

    For a member leaving the organization: a removed member can no longer
    use a seat, and the seat screens list active members only, so a seat
    left assigned to them could never be freed from the UI - the
    organization would keep paying for capacity nobody can use.
    """
    from workeasy360.platform.entitlements.models import SeatAssignment

    released = SeatAssignment.objects.filter(
        organization_id=organization_id,
        membership_id=membership_id,
        released_at__isnull=True,
    ).update(released_at=timezone.now(), over_limit_since=None)
    if released:
        logger.info("entitlement.seat.released_on_removal", released=released)
    return released


def over_limit_assignments(organization_id: UUID, *, pool_key: str = "") -> list:
    """Assignments awaiting a remediation decision.

    Flagged by ``commercial/provisioning`` when it reconciles capacity - the
    flag is a consequence of commercial state, so the entitlement writer owns
    setting it and this module only reads it and clears it on release.
    """
    from workeasy360.platform.entitlements.models import SeatAssignment

    queryset = SeatAssignment.objects.filter(
        organization_id=organization_id,
        released_at__isnull=True,
        over_limit_since__isnull=False,
    )
    if pool_key:
        queryset = queryset.filter(seat_grant__pool_key=pool_key)
    return list(queryset.select_related("seat_grant").order_by("over_limit_since", "id"))
