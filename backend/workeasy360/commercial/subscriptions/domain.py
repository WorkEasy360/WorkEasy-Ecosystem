"""Subscription lifecycle rules as pure Python. No Django, no I/O (G3).

The state machine is a table, not a chain of conditionals. A reviewer can
read the whole legal surface in one glance, an illegal move is impossible to
express rather than merely discouraged, and an absent edge is visible.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from workeasy360.contracts.enums import (
    BillingInterval,
    ChangeTiming,
    SubscriptionStatus,
)
from workeasy360.contracts.errors import InvalidStateTransition

S = SubscriptionStatus

#: Legal subscription moves.
#:
#: Note what is **absent**, because each absence is a decision:
#:
#: ``cancelled -> active`` - un-cancelling is not supported. The customer has
#: been told the relationship ends on a date, billing has been told, and any
#: partner commission has been told. Resuming is a new subscription, which
#: leaves both facts in the record.
#:
#: ``expired -> anything`` - expiry is terminal. A returning customer gets a
#: new subscription, so win-back reporting is not a guess.
#:
#: ``trialing -> past_due`` - a trial has nothing to fail to pay.
TRANSITIONS: Final[dict[SubscriptionStatus, frozenset[SubscriptionStatus]]] = {
    S.TRIALING: frozenset({S.ACTIVE, S.CANCELLED, S.EXPIRED}),
    S.ACTIVE: frozenset({S.PAST_DUE, S.SUSPENDED, S.CANCELLED}),
    S.PAST_DUE: frozenset({S.ACTIVE, S.GRACE, S.CANCELLED}),
    S.GRACE: frozenset({S.ACTIVE, S.SUSPENDED, S.CANCELLED}),
    S.SUSPENDED: frozenset({S.ACTIVE, S.CANCELLED}),
    S.CANCELLED: frozenset({S.EXPIRED}),
    S.EXPIRED: frozenset(),
}

#: Statuses in which the commercial shape may still be changed - items added,
#: plans upgraded, seats varied. A cancelled subscription keeps its access
#: until the period end but does not accept new commitments, because the
#: customer has said they are leaving and billing for more would be a dispute.
MUTABLE_STATUSES: Final[frozenset[SubscriptionStatus]] = frozenset(
    {S.TRIALING, S.ACTIVE, S.PAST_DUE, S.GRACE}
)


def assert_transition(current: SubscriptionStatus, target: SubscriptionStatus) -> None:
    """Raise unless the move is on the table. Self-transitions are no-ops."""
    if current is target:
        return
    if target not in TRANSITIONS[current]:
        raise InvalidStateTransition(
            entity="subscriptions.Subscription",
            from_status=current.value,
            to_status=target.value,
        )


def assert_mutable(current: SubscriptionStatus, *, operation: str) -> None:
    if current not in MUTABLE_STATUSES:
        raise InvalidStateTransition(
            f"{operation} is not permitted on a {current.value} subscription.",
            entity="subscriptions.Subscription",
            from_status=current.value,
            to_status=current.value,
        )


def advance_period(
    *, start: datetime, end: datetime, interval: BillingInterval, at: datetime
) -> tuple[datetime, datetime]:
    """The next billing period after ``end``.

    Rolls forward repeatedly until the period contains ``at``, so a renewal
    job that has not run for three months produces the correct current period
    rather than one three months stale. Idempotent: calling it for a period
    that already contains ``at`` returns that period unchanged.

    ``one_time`` never rolls - there is no next period - and returns the
    original window.
    """
    months = interval.months
    if months == 0:
        return start, end

    period_start, period_end = start, end
    steps = 1
    # Bounded so a corrupt row cannot spin: 400 monthly rolls is over thirty
    # years, far beyond any legitimate gap, and reaching it is a data defect
    # rather than a long outage.
    #
    # Each step is measured from ``start``, not chained from the previous
    # ``period_end``: chaining re-clamps an already-clamped date (a period
    # anchored on the 29th/30th/31st that has passed through a shorter
    # February comes out on the 28th), so a multi-month catch-up would drift
    # to an earlier day-of-month than a renewal that ran on time ever would -
    # exactly the anniversary drift ``add_months`` exists to prevent.
    for _ in range(400):
        if period_end > at:
            break
        period_start = period_end
        steps += 1
        period_end = add_months(start, months * steps)
    return period_start, period_end


def add_months(moment: datetime, months: int) -> datetime:
    """Add whole months, clamping the day to the target month's length.

    31 January plus one month is 28 (or 29) February, not 3 March. Rolling
    over into the next month shifts every subsequent anniversary and makes a
    customer's billing date drift, which they notice.
    """
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, _days_in_month(year, month))
    return moment.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    first_of_next = datetime(year + month // 12, month % 12 + 1, 1)
    return (first_of_next - timedelta(days=1)).day


def trial_end(*, start: datetime, trial_days: int) -> datetime | None:
    """When a trial of ``trial_days`` beginning at ``start`` ends."""
    if trial_days <= 0:
        return None
    return start + timedelta(days=trial_days)


def default_timing(direction: str) -> ChangeTiming:
    """When a plan change takes effect, by default.

    Upgrades are immediate: the customer asked for more capability and
    expects it now, and withholding it until the period end is the kind of
    thing that gets described as "we charged them and nothing happened".

    Downgrades and lateral moves wait for the period end: the current period
    is already paid for at the higher tier, so removing capability early
    takes away something the customer has bought.
    """
    return ChangeTiming.IMMEDIATE if direction == "upgrade" else ChangeTiming.PERIOD_END


def effective_moment(
    *, timing: ChangeTiming, now: datetime, period_end: datetime
) -> datetime:
    """Resolve a timing choice to an instant.

    A period-end change whose period has already elapsed takes effect now
    rather than in the past: scheduling a change into the past would make it
    invisible to the effective-dating filter and the change would never
    happen.
    """
    if timing is ChangeTiming.IMMEDIATE:
        return now
    return max(period_end, now)


def prorate(
    amount_minor: int, *, period_start: datetime, period_end: datetime, at: datetime
) -> int:
    """The share of one period's price for the rest of that period (Phase 9).

    A line added to a running subscription is paid for from ``at`` to the
    period's end, then invoiced in full with the subscription from the next
    period. Rounded half up to the minor unit, in integer arithmetic - never a
    float (F1). Zero once the period has ended; the whole amount if ``at`` is
    at or before its start.
    """
    total = int((period_end - period_start).total_seconds())
    remaining = int((period_end - max(at, period_start)).total_seconds())
    if amount_minor <= 0 or total <= 0 or remaining <= 0:
        return 0
    remaining = min(remaining, total)
    # Half up: (2 * a * r + t) // (2 * t).
    return (2 * amount_minor * remaining + total) // (2 * total)
