"""What an organization holds, product by product, for the dashboard (Phase 11).

Read-only and computed from the same two facts provisioning uses - the
organization's subscriptions and its product trials - so the card a member sees
cannot disagree with the entitlement they were granted. A **paid** line wins
over a trial of the same product, exactly as it does in
``provisioning.compute_entitlements``.

Two reads for any number of products (subscriptions with their lines, trials),
plus one batched catalog read for plan names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.subscriptions import selectors
from workeasy360.contracts.enums import SubscriptionStatus


@dataclass(frozen=True, slots=True)
class HoldingDTO:
    """One product as this organization holds it."""

    product_code: str
    #: ``subscription`` (a paid line) or ``trial``.
    source: str
    plan_code: str
    plan_name: str
    subscription_status: str = ""
    #: A trial's end - a product trial's own, or a plan-level trial
    #: subscription's.
    trial_ends_at: datetime | None = None
    trial_id: str | None = None
    #: When the next period starts (and is billed), unless it is ending.
    renews_at: datetime | None = None
    #: When access ends, for a subscription cancelled to period end.
    cancel_at: datetime | None = None


#: ADR-073 (G13): how long a paid product that ended is still shown in My
#: Products, as Expired with a way to buy it again, before it is only Explore's.
LAPSED_WINDOW = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class LapsedDTO:
    """A product the organization **paid** for, whose access has ended."""

    product_code: str
    plan_code: str
    plan_name: str
    ended_at: datetime


def holdings(*, at: datetime | None = None) -> dict[str, HoldingDTO]:
    """``{product_code: HoldingDTO}`` for the current tenant."""
    return directory_state(at=at)[0]


def directory_state(
    *, at: datetime | None = None
) -> tuple[dict[str, HoldingDTO], dict[str, LapsedDTO]]:
    """What the organization holds, and (ADR-073) the paid products it held
    until recently and no longer does - from the same two reads."""
    moment = at or timezone.now()
    subscriptions = selectors.subscriptions_for_organization()
    trials = selectors.product_trials()
    ended = _ended_paid_lines(subscriptions, moment)

    wanted = (
        [
            item.plan_version_id
            for subscription in subscriptions
            if subscription.grants_access_at(moment)
            for item in subscription.effective_items(moment)
            if item.plan_version_id is not None
        ]
        + [trial.plan_version_id for trial in trials if trial.is_effective_at(moment)]
        + [item.plan_version_id for item, _end in ended.values()]
    )
    plans = catalog.plan_versions(wanted)

    found: dict[str, HoldingDTO] = {}
    for subscription in subscriptions:
        if not subscription.grants_access_at(moment):
            continue
        on_trial = subscription.status is SubscriptionStatus.TRIALING
        for item in subscription.effective_items(moment):
            if not item.product_code:
                continue
            # ADR-073 (G7): a product ended on its own shows when it ends.
            ends = _product_ends(subscription, item)
            plan = plans.get(item.plan_version_id)
            found[item.product_code] = HoldingDTO(
                product_code=item.product_code,
                source="trial" if on_trial else "subscription",
                plan_code=item.plan_code,
                plan_name=plan.plan_name if plan else item.plan_code,
                subscription_status=subscription.status.value,
                trial_ends_at=subscription.trial_ends_at if on_trial else None,
                renews_at=(
                    None
                    if subscription.cancel_at or on_trial or ends
                    else subscription.current_period_end
                ),
                cancel_at=_earliest(subscription.cancel_at, ends),
            )
    paid = selectors.paid_product_codes(subscriptions, moment)
    for trial in trials:
        if trial.product_code in found or trial.product_code in paid:
            continue
        if not trial.is_effective_at(moment):
            continue
        plan = plans.get(trial.plan_version_id)
        found[trial.product_code] = HoldingDTO(
            product_code=trial.product_code,
            source="trial",
            plan_code=trial.plan_code,
            plan_name=plan.plan_name if plan else trial.plan_code,
            trial_ends_at=trial.ends_at,
            trial_id=str(trial.id),
        )
    lapsed: dict[str, LapsedDTO] = {}
    for code, (item, ended_at) in ended.items():
        if code in found:
            continue
        plan = plans.get(item.plan_version_id)
        lapsed[code] = LapsedDTO(
            product_code=code,
            plan_code=item.plan_code,
            plan_name=plan.plan_name if plan else item.plan_code,
            ended_at=ended_at,
        )
    return found, lapsed


def _earliest(*moments: datetime | None) -> datetime | None:
    present = [m for m in moments if m is not None]
    return min(present) if present else None


def _product_ends(subscription: selectors.SubscriptionDTO, item) -> datetime | None:
    """When this line's product stops being held by the subscription on its own -
    ``None`` while a line for it runs on (a scheduled plan or quantity change
    opens the next line where this one closes)."""
    if item.removed_at is None:
        return None
    for other in subscription.items:
        if (
            other.product_code == item.product_code
            and other.id != item.id
            and other.added_at >= item.removed_at
            and (other.removed_at is None or other.removed_at > other.added_at)
        ):
            return _product_ends(subscription, other)
    return item.removed_at


def _ended_paid_lines(
    subscriptions: tuple[selectors.SubscriptionDTO, ...], moment: datetime
) -> dict[str, tuple[selectors.SubscriptionItemDTO, datetime]]:
    """``{product_code: (line, ended_at)}``: the last **paid** line of each product
    whose access ended within :data:`LAPSED_WINDOW` before ``moment`` - the line
    removed, or its subscription cancelled or expired.

    Paid means the line was in force after any trial its subscription had: a
    plan-level trial that simply ran out is an expired *trial*, which Explore
    already reports, not an expired purchase."""
    found: dict[str, tuple[selectors.SubscriptionItemDTO, datetime]] = {}
    since = moment - LAPSED_WINDOW
    for subscription in subscriptions:
        access_ends = subscription.access_ends_at()
        if subscription.status is SubscriptionStatus.EXPIRED and access_ends is None:
            access_ends = subscription.current_period_end
        for item in subscription.items:
            if not item.product_code or item.plan_version_id is None:
                continue
            ended_at = _earliest(item.removed_at, access_ends)
            if ended_at is None or ended_at > moment or ended_at <= since:
                continue
            if ended_at <= item.added_at:
                continue  # a zero-width (superseded) line held nothing
            trial_end = subscription.trial_ends_at
            if trial_end is not None and ended_at <= trial_end:
                continue
            known = found.get(item.product_code)
            if known is None or ended_at > known[1]:
                found[item.product_code] = (item, ended_at)
    return found
