"""Time-driven subscription operations. The **only** system write pathway.

Some commercial state changes have no actor. A trial reaches its end, a
scheduled cancellation's date arrives, a billing period rolls. They happen
because a clock moved, and there is no request, no session and no membership
behind them.

That creates a genuine problem with R-10. A tenant context makes the
persistence chokepoint refuse writes for a read-only organization - and the
organizations most in need of these operations are precisely the suspended,
non-paying ones whose subscription must still be cancellable and expirable.
A suspended customer whose cancellation could never take effect would keep an
open commercial record forever.

So this module - and nothing else in ``commercial/`` - writes subscription
state without a tenant context, using the audited escape hatch:

    unscoped_access(reason=...)   relaxes application scoping (Layer 2)
    organization_db_scope(org)    satisfies the RLS policy (Layer 3)

Both are required and neither is sufficient. RLS is **not** relaxed: every
write still happens inside exactly one organization's database scope, so a
bug here can corrupt one tenant's own record and cannot reach another's.

**This is not a general bypass**, and three things keep it that way:

1. It is one file, named in the ``unscoped`` allowlist, so ``grep`` gives the
   complete list of system writers.
2. Every operation is audited with ``ActorKind.SYSTEM`` and a reason. An
   audit failure fails the operation.
3. Nothing here takes a caller-supplied status. Each function applies exactly
   one transition, decided by a durable timestamp that has passed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

# Phase 12 (G-32): SoftTimeLimitExceeded subclasses Exception. Swallowed
# here, a pass that outran its budget kept going until the hard kill, with
# no summary, and the organizations enumerated last were never reached.
from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.subscriptions.domain import advance_period, assert_transition
from workeasy360.commercial.subscriptions.hooks import notify_commercial_change
from workeasy360.contracts.enums import ActorKind, RenewalPolicy, SubscriptionStatus
from workeasy360.contracts.events import commercial_events as schemas
from workeasy360.contracts.events.envelope import Actor, DomainEvent
from workeasy360.platform.audit import services as audit
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

SYSTEM_TRANSITIONS = "commercial_system_transitions_total"


@contextmanager
def system_scope(organization_id: UUID, *, reason: str) -> Iterator[None]:
    """Enter both isolation layers for one organization, with no actor.

    The reason is mandatory and is recorded by ``unscoped_access``. A short
    or absent reason is refused rather than accepted with a shrug: this
    record is what an auditor reads.
    """
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason=reason, module="commercial.subscriptions.system"),
        organization_db_scope(organization_id),
    ):
        yield


def _system_event(
    *, organization_id: UUID, event_type: str, aggregate_id: Any, payload: dict[str, Any]
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        event_version=1,
        organization_id=organization_id,
        aggregate_type=schemas.SUBSCRIPTION_AGGREGATE,
        aggregate_id=str(aggregate_id),
        payload=payload,
        actor=Actor(kind=ActorKind.SYSTEM, service_name="commercial.subscriptions"),
    )


def process_due_subscriptions(*, at: datetime | None = None) -> dict[str, int]:
    """Apply every clock-driven transition that is now due.

    One pass over the subscriptions whose durable timestamps have passed. Each
    organization is handled in its own transaction and its own database
    scope, so one corrupt tenant cannot stop the sweep for everyone else -
    and cannot have its rows written while another tenant's scope is open.
    """
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_due_for_attention,
    )
    from workeasy360.platform.organizations.selectors import all_organization_ids

    moment = at or timezone.now()
    counts = {
        "trials_expired": 0,
        "cancellations_applied": 0,
        "periods_rolled": 0,
        "product_trials_expired": 0,
        "product_trials_converted": 0,
        "failed": 0,
    }

    # Organizations come from the control plane; due subscriptions come from
    # inside each one's scope. A single cross-tenant scan is not available -
    # a tenant table matches zero rows without a tenant variable - and would
    # be forbidden even if it were (03-multi-tenancy.md).
    #
    # The cost is one indexed query per organization per run. At the scale
    # this schedule is set for that is unremarkable; the trigger for a
    # cheaper work list is recorded in docs/phase-2/performance-review.md.
    for organization_id in all_organization_ids():
        try:
            due = subscriptions_due_for_attention(organization_id, at=moment)
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            counts["failed"] += 1
            logger.error(
                "commercial.system.scan_failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )
            continue

        changed = False
        for subscription_id in due:
            try:
                applied = _process_one(organization_id, subscription_id, moment)
                for key, value in applied.items():
                    counts[key] += value
                changed = changed or any(applied.values())
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                counts["failed"] += 1
                logger.error(
                    "commercial.system.sweep_failed",
                    organization_id=str(organization_id),
                    error_type=type(exc).__name__,
                )
        try:
            expired, converted = settle_product_trials(organization_id, at=moment)
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            counts["failed"] += 1
            logger.error(
                "commercial.system.product_trials_failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )
            expired = converted = 0
        counts["product_trials_expired"] += expired
        counts["product_trials_converted"] += converted
        if changed or expired or converted:
            notify_commercial_change(organization_id)

    if any(counts.values()):
        logger.info("commercial.system.sweep", **counts)
    return counts


def settle_product_trials(
    organization_id: UUID, *, at: datetime | None = None
) -> tuple[int, int]:
    """Close the product trials whose fate is now decided (Phase 11).

    Two facts close an ``active`` trial, both durable and neither taken from a
    caller:

    - a **paid** line now holds the product -> ``converted``. (Provisioning
      already stopped granting the trial the moment the paid line existed;
      this only makes the record say so.)
    - its ``ends_at`` has passed with no purchase -> ``expired``.

    Returns ``(expired, converted)``. Idempotent: a closed trial is never
    reopened, so a second run changes nothing. Runs for one organization, in
    its own scope and transaction, like every other system operation here.
    """
    from workeasy360.commercial.subscriptions.models import ProductTrial
    from workeasy360.commercial.subscriptions.selectors import (
        commercial_state,
        paid_product_codes,
    )
    from workeasy360.contracts.enums import ProductTrialStatus

    moment = at or timezone.now()
    expired = converted = 0
    with (
        system_scope(organization_id, reason="Clock-driven product trial settlement"),
        transaction.atomic(),
    ):
        active = list(
            ProductTrial.unscoped.select_for_update().filter(
                organization_id=organization_id, status=ProductTrialStatus.ACTIVE.value
            )
        )
        if not active:
            return 0, 0
        paid = paid_product_codes(commercial_state(organization_id), moment)
        for trial in active:
            if trial.product_code in paid:
                new_status, action = (
                    ProductTrialStatus.CONVERTED,
                    "commercial.product_trial.converted",
                )
                converted += 1
            elif trial.ends_at <= moment:
                new_status, action = (
                    ProductTrialStatus.EXPIRED,
                    "commercial.product_trial.expired",
                )
                expired += 1
            else:
                continue
            trial.status = new_status.value
            trial.ended_at = moment
            trial.save(update_fields=["status", "ended_at", "updated_at"])
            audit.record_system(
                organization_id,
                action,
                resource_type="subscriptions.product_trial",
                resource_id=str(trial.id),
                changes={"product_code": trial.product_code},
                reason="Clock-driven product trial settlement",
            )
    return expired, converted


def _process_one(
    organization_id: UUID, subscription_id: UUID, moment: datetime
) -> dict[str, int]:
    """Apply whichever transitions this subscription is due, in one transaction."""
    from workeasy360.commercial.subscriptions.models import Subscription

    applied = {"trials_expired": 0, "cancellations_applied": 0, "periods_rolled": 0}

    with (
        system_scope(
            organization_id,
            reason="Clock-driven commercial transition for one organization",
        ),
        transaction.atomic(),
    ):
        subscription = (
            Subscription.unscoped.select_for_update()
            .filter(id=subscription_id, organization_id=organization_id)
            .first()
        )
        if subscription is None:
            return applied

        status = subscription.status_enum

        if (
            status is SubscriptionStatus.TRIALING
            and subscription.trial_ends_at is not None
            and subscription.trial_ends_at <= moment
        ):
            _expire_trial(subscription, moment)
            applied["trials_expired"] = 1
            return applied

        if (
            status is SubscriptionStatus.CANCELLED
            and subscription.cancel_at is not None
            and subscription.cancel_at <= moment
        ):
            _expire_cancelled(subscription, moment)
            applied["cancellations_applied"] = 1
            return applied

        if (
            subscription.current_period_end <= moment
            and subscription.renewal_policy == RenewalPolicy.AUTO_RENEW.value
            and status.is_live
        ):
            _roll_period(subscription, moment)
            applied["periods_rolled"] = 1

    return applied


def _expire_trial(subscription: Any, moment: datetime) -> None:
    """A trial ended without converting. Access stops; data is retained.

    Expiry moves to ``expired`` (export-only), never deletion. Data is kept
    for the standard retention window so a late conversion restores
    everything instantly, and because deleting on expiry is legally risky and
    commercially fatal to win-backs.
    """
    assert_transition(subscription.status_enum, SubscriptionStatus.EXPIRED)
    subscription.status = SubscriptionStatus.EXPIRED.value
    subscription.ended_at = moment
    subscription.version += 1
    subscription.save(update_fields=["status", "ended_at", "version", "updated_at"])

    _publish(
        subscription,
        schemas.TRIAL_ENDED_V1.event_type,
        {
            "organization_id": str(subscription.organization_id),
            "subscription_id": str(subscription.id),
            "converted": False,
        },
    )
    audit.record_system(
        subscription.organization_id,
        "commercial.subscription.trial_expired",
        resource_type="subscriptions.subscription",
        resource_id=subscription.id,
        changes={"to": SubscriptionStatus.EXPIRED.value},
        reason="Trial period ended without conversion to a paid plan.",
    )
    metrics.increment(SYSTEM_TRANSITIONS, transition="trial_expired")


def _expire_cancelled(subscription: Any, moment: datetime) -> None:
    """A scheduled cancellation's effective date arrived."""
    assert_transition(subscription.status_enum, SubscriptionStatus.EXPIRED)
    subscription.status = SubscriptionStatus.EXPIRED.value
    subscription.ended_at = subscription.cancel_at or moment
    subscription.version += 1
    subscription.save(update_fields=["status", "ended_at", "version", "updated_at"])

    audit.record_system(
        subscription.organization_id,
        "commercial.subscription.expired",
        resource_type="subscriptions.subscription",
        resource_id=subscription.id,
        changes={"to": SubscriptionStatus.EXPIRED.value},
        reason="Scheduled cancellation reached its effective date.",
    )
    metrics.increment(SYSTEM_TRANSITIONS, transition="cancellation_applied")


def _roll_period(subscription: Any, moment: datetime) -> None:
    """Advance to the current billing period.

    **No payment is taken and none is recorded.** Phase 2 has no payment
    provider, and a renewal that quietly marked itself paid would be the most
    dangerous placeholder in the system. This advances the period so that the
    subscription's window reflects reality; collecting for it is a later
    phase's responsibility.
    """
    start, end = advance_period(
        start=subscription.current_period_start,
        end=subscription.current_period_end,
        interval=subscription.interval_enum,
        at=moment,
    )
    if (start, end) == (
        subscription.current_period_start,
        subscription.current_period_end,
    ):
        return

    subscription.current_period_start = start
    subscription.current_period_end = end
    subscription.version += 1
    subscription.save(
        update_fields=[
            "current_period_start",
            "current_period_end",
            "version",
            "updated_at",
        ]
    )
    audit.record_system(
        subscription.organization_id,
        "commercial.subscription.period_rolled",
        resource_type="subscriptions.subscription",
        resource_id=subscription.id,
        changes={
            "current_period_start": start.isoformat(),
            "current_period_end": end.isoformat(),
        },
        reason="Billing period reached its end under an auto-renew policy.",
    )
    metrics.increment(SYSTEM_TRANSITIONS, transition="period_rolled")


def suspend_for_non_payment(
    organization_id: UUID, *, subscription_id: UUID, reason_code: str
) -> Any:
    """Move a subscription to ``suspended``: read-only, not locked out.

    The customer's data stays visible and exportable, the "pay now" path
    stays open, and an entire category of angry support escalation
    disappears. Writes, background automation and API mutations stop.

    Exposed for the dunning process a payment integration will drive. It is
    not called by anything in Phase 2, and is covered by its own test rather
    than left unexercised.
    """
    from workeasy360.commercial.subscriptions.models import Subscription

    with (
        system_scope(
            organization_id,
            reason="Dunning suspension applied without a request tenant context",
        ),
        transaction.atomic(),
    ):
        subscription = (
            Subscription.unscoped.select_for_update()
            .filter(id=subscription_id, organization_id=organization_id)
            .first()
        )
        if subscription is None:
            return None

        previous = subscription.status_enum
        assert_transition(previous, SubscriptionStatus.SUSPENDED)
        subscription.status = SubscriptionStatus.SUSPENDED.value
        subscription.version += 1
        subscription.save(update_fields=["status", "version", "updated_at"])

        _publish(
            subscription,
            schemas.SUBSCRIPTION_SUSPENDED_V1.event_type,
            {
                "organization_id": str(organization_id),
                "subscription_id": str(subscription.id),
                "from_status": previous.value,
                "reason_code": reason_code,
            },
        )
        audit.record_system(
            organization_id,
            "commercial.subscription.suspended",
            resource_type="subscriptions.subscription",
            resource_id=subscription.id,
            changes={"from": previous.value, "reason_code": reason_code},
            reason=f"Subscription suspended: {reason_code}.",
        )
        metrics.increment(SYSTEM_TRANSITIONS, transition="suspended")

    notify_commercial_change(organization_id)
    return subscription


def _publish(subscription: Any, event_type: str, payload: dict[str, Any]) -> None:
    """Publish inside the caller's transaction, as every publisher must."""
    from workeasy360.integration.events import services as events

    events.publish(
        _system_event(
            organization_id=subscription.organization_id,
            event_type=event_type,
            aggregate_id=subscription.id,
            payload=payload,
        )
    )


# --- Phase 8 extension: dunning and settlement, driven by payments -------------
#
# ``commercial/payments`` decides *when* (an invoice is overdue, a payment
# settled it); these apply the one transition that decision implies, under the
# same rules as every other system write here: one organization's scope, a
# row lock, the state machine, an event and an audit entry with a reason.


def _dunning_transition(
    organization_id: UUID,
    *,
    subscription_id: UUID,
    allowed_from: frozenset[SubscriptionStatus],
    target: SubscriptionStatus,
    action: str,
    reason: str,
) -> Any:
    from workeasy360.commercial.subscriptions.models import Subscription

    with system_scope(organization_id, reason=reason), transaction.atomic():
        subscription = (
            Subscription.unscoped.select_for_update()
            .filter(id=subscription_id, organization_id=organization_id)
            .first()
        )
        if subscription is None or subscription.status_enum not in allowed_from:
            # Already moved (a payment landed first, staff acted): nothing to do.
            return None
        previous = subscription.status_enum
        assert_transition(previous, target)
        subscription.status = target.value
        subscription.version += 1
        subscription.save(update_fields=["status", "version", "updated_at"])
        if target is SubscriptionStatus.ACTIVE:
            _publish(
                subscription,
                schemas.SUBSCRIPTION_ACTIVATED_V1.event_type,
                {
                    "organization_id": str(organization_id),
                    "subscription_id": str(subscription.id),
                    "from_status": previous.value,
                    "to_status": target.value,
                    "product_codes": [],
                },
            )
        audit.record_system(
            organization_id,
            action,
            resource_type="subscriptions.subscription",
            resource_id=subscription.id,
            changes={"from": previous.value, "to": target.value},
            reason=reason,
        )
        metrics.increment(SYSTEM_TRANSITIONS, transition=target.value)
    notify_commercial_change(organization_id)
    return subscription


def mark_past_due(organization_id: UUID, *, subscription_id: UUID) -> Any:
    """An invoice passed its due date unpaid. Access is unchanged (past due
    restricts nothing - 06-entitlements.md); dunning has started."""
    return _dunning_transition(
        organization_id,
        subscription_id=subscription_id,
        allowed_from=frozenset({SubscriptionStatus.ACTIVE}),
        target=SubscriptionStatus.PAST_DUE,
        action="commercial.subscription.past_due",
        reason="An invoice passed its due date without payment.",
    )


def enter_grace(organization_id: UUID, *, subscription_id: UUID) -> Any:
    """Still unpaid after the past-due period: the last step before suspension."""
    return _dunning_transition(
        organization_id,
        subscription_id=subscription_id,
        allowed_from=frozenset({SubscriptionStatus.PAST_DUE}),
        target=SubscriptionStatus.GRACE,
        action="commercial.subscription.grace",
        reason="An invoice stayed unpaid through the past-due period.",
    )


def settle_after_payment(
    organization_id: UUID, *, subscription_id: UUID, lift_suspension: bool = False
) -> Any:
    """Every overdue invoice is now paid: a past-due or grace subscription is
    active again. A *suspended* one only when ``lift_suspension`` - the caller
    knows dunning itself suspended it; a suspension staff applied for another
    reason is theirs to lift, and a payment must not quietly undo it."""
    allowed = {SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE}
    if lift_suspension:
        allowed.add(SubscriptionStatus.SUSPENDED)
    return _dunning_transition(
        organization_id,
        subscription_id=subscription_id,
        allowed_from=frozenset(allowed),
        target=SubscriptionStatus.ACTIVE,
        action="commercial.subscription.payment_settled",
        reason="Outstanding invoices were paid.",
    )


# --- Phase 9: fulfilling a paid order ---------------------------------------------
#
# ``commercial/payments.orders`` decides *that* an order is paid (a signed
# webhook or staff settled its invoice); these add what it bought, at the price
# it paid. Both run inside the caller's transaction, with the order row locked
# and a routing context (no actor) bound for the organization - the payment is
# the authority, so there is no permission check here, which is why they are
# in this module and never reachable from a request.


def add_paid_line(
    ctx: Any,
    *,
    plan_version_id: UUID | None,
    bundle_version_id: UUID | None,
    declined_plan_version_ids: list[UUID],
    quantity: int,
    billing_interval: Any,
    snapshot: dict[str, Any],
    order_number: str,
) -> tuple[Any, Any]:
    """Open the subscription (active, its first period starting now) or join
    the running one, and add the plan or bundle the order bought. Returns
    ``(subscription, line)``. Raises ``CommercialError`` with a ``reason``
    when that is no longer possible - the caller refunds."""
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.subscriptions import services
    from workeasy360.contracts.enums import CollectionMode, PlanKind
    from workeasy360.contracts.errors import CommercialError

    now = timezone.now()
    subscription = services._open_subscription(
        ctx,
        billing_interval=billing_interval,
        collection_mode=CollectionMode.AUTOMATIC,
        renewal_policy=RenewalPolicy.AUTO_RENEW,
        now=now,
        trial_days=0,
        trial_source=None,
    )
    if subscription.status_enum is not SubscriptionStatus.ACTIVE:
        raise CommercialError(
            "The subscription is no longer open for new lines.",
            reason="subscription_not_open",
        )
    if subscription.billing_interval != billing_interval.value or (
        snapshot.get("currency") and snapshot["currency"] != subscription.currency
    ):
        raise CommercialError(
            "The subscription's billing terms changed after the order was placed.",
            reason="interval_mismatch",
        )

    if plan_version_id is not None:
        plan = catalog.require_plan_version(plan_version_id)
        if plan.plan_kind is PlanKind.ADD_ON:
            services._assert_add_on_base(subscription, plan)
        if services._open_line_for(subscription, [plan.product_code]):
            raise CommercialError("The product is already held.", reason="already_owned")
        line = services._create_plan_item(
            subscription=subscription,
            plan=plan,
            quantity=quantity,
            added_at=now,
            quote=None,
            snapshot=snapshot,
        )
        _publish(
            subscription,
            schemas.SUBSCRIPTION_ITEM_ADDED_V1.event_type,
            services._item_payload(subscription, line),
        )
        action, changes = (
            "commercial.subscription.plan_purchased",
            {"plan": plan.plan_code, "plan_version": plan.version, "quantity": quantity},
        )
    else:
        bundle = catalog.require_bundle_version(bundle_version_id)
        declined = set(declined_plan_version_ids)
        wanted = [i for i in bundle.items if i.plan_version_id not in declined]
        plans = catalog.plan_versions([i.plan_version_id for i in wanted])
        if services._open_line_for(subscription, [i.product_code for i in wanted]):
            raise CommercialError(
                "A product in the bundle is already held.", reason="already_owned"
            )
        line = services._create_bundle_lines(
            subscription=subscription,
            bundle=bundle,
            wanted=wanted,
            plans=plans,
            snapshot=snapshot,
            added_at=now,
            publish=lambda derived: _publish(
                subscription,
                schemas.SUBSCRIPTION_ITEM_ADDED_V1.event_type,
                services._item_payload(subscription, derived),
            ),
        )
        action, changes = (
            "commercial.subscription.bundle_purchased",
            {
                "bundle": bundle.bundle_code,
                "bundle_version": bundle.version,
                "expanded_to": sorted(plans[i.plan_version_id].plan_code for i in wanted),
            },
        )
    audit.record_system(
        subscription.organization_id,
        action,
        resource_type="subscriptions.item",
        resource_id=line.id,
        changes={**changes, "order": order_number},
        reason="A paid order was fulfilled.",
    )
    services._bump(subscription, [])
    metrics.increment(SYSTEM_TRANSITIONS, transition="order_fulfilled")
    return subscription, line


def activate_paid(ctx: Any, *, subscription_id: UUID, order_number: str) -> Any:
    """Convert a trial whose activation order was paid: active now, with a
    fresh first paid period starting now (the period the order's invoice
    covers). An already active subscription is left as it is."""
    from workeasy360.commercial.subscriptions import services
    from workeasy360.commercial.subscriptions.domain import add_months
    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.contracts.errors import CommercialError

    subscription = (
        Subscription.objects.select_for_update().filter(id=subscription_id).first()
    )
    if subscription is None:
        raise CommercialError(
            "The subscription no longer exists.", reason="subscription_not_open"
        )
    previous = subscription.status_enum
    if previous is SubscriptionStatus.ACTIVE:
        return subscription
    if previous is not SubscriptionStatus.TRIALING:
        raise CommercialError(
            "Only a trial can be activated by an order.", reason="subscription_not_open"
        )
    now = timezone.now()
    months = subscription.interval_enum.months
    subscription.status = SubscriptionStatus.ACTIVE.value
    subscription.activated_at = subscription.activated_at or now
    subscription.trial_ends_at = now
    subscription.current_period_start = now
    subscription.current_period_end = (
        add_months(now, months) if months else now + timezone.timedelta(days=365)
    )
    services._bump(
        subscription,
        [
            "status",
            "activated_at",
            "trial_ends_at",
            "current_period_start",
            "current_period_end",
        ],
    )
    payload = {
        "organization_id": str(subscription.organization_id),
        "subscription_id": str(subscription.id),
    }
    _publish(
        subscription,
        schemas.SUBSCRIPTION_ACTIVATED_V1.event_type,
        {
            **payload,
            "from_status": previous.value,
            "to_status": SubscriptionStatus.ACTIVE.value,
            "product_codes": services._open_product_codes(subscription),
        },
    )
    _publish(
        subscription, schemas.TRIAL_ENDED_V1.event_type, {**payload, "converted": True}
    )
    audit.record_system(
        subscription.organization_id,
        "commercial.subscription.activated",
        resource_type="subscriptions.subscription",
        resource_id=subscription.id,
        changes={"from": previous.value, "to": "active", "order": order_number},
        reason="A paid activation order converted the trial.",
    )
    metrics.increment(SYSTEM_TRANSITIONS, transition="activated_by_order")
    return subscription
