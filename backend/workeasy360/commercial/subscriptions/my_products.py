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
from datetime import datetime

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


def holdings(*, at: datetime | None = None) -> dict[str, HoldingDTO]:
    """``{product_code: HoldingDTO}`` for the current tenant."""
    moment = at or timezone.now()
    subscriptions = selectors.subscriptions_for_organization()
    trials = selectors.product_trials()

    wanted = [
        item.plan_version_id
        for subscription in subscriptions
        if subscription.grants_access_at(moment)
        for item in subscription.effective_items(moment)
        if item.plan_version_id is not None
    ] + [trial.plan_version_id for trial in trials if trial.is_effective_at(moment)]
    plans = catalog.plan_versions(wanted)

    found: dict[str, HoldingDTO] = {}
    for subscription in subscriptions:
        if not subscription.grants_access_at(moment):
            continue
        on_trial = subscription.status is SubscriptionStatus.TRIALING
        for item in subscription.effective_items(moment):
            if not item.product_code:
                continue
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
                    if subscription.cancel_at or on_trial
                    else subscription.current_period_end
                ),
                cancel_at=subscription.cancel_at,
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
    return found
