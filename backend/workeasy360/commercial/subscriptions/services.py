"""Subscription application services - the commercial command surface.

Every command here follows the same five-step shape, and the order is
load-bearing:

1. **Authorise.** In the service, not the view: the task path and every
   internal caller would otherwise bypass it (C7).
2. **Claim an idempotency key**, inside the transaction, so a duplicate
   delivery returns the original result instead of buying twice.
3. **Write durable state**, taking a row lock on the subscription so two
   concurrent commands serialise.
4. **Publish to the outbox and audit**, in the *same* transaction. A message
   published before commit describes work that may never happen; one
   published after may be lost entirely (ADR-008).
5. **Notify provisioning**, after the transaction, never inside it.

No external service is called anywhere in that sequence, so no transaction is
ever held across a network round trip (E2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.catalog.domain import compare_plan_versions
from workeasy360.commercial.pricing.resolution import PriceQuote, resolve_price
from workeasy360.commercial.subscriptions.domain import (
    assert_mutable,
    assert_transition,
    default_timing,
    effective_moment,
    trial_end,
)
from workeasy360.commercial.subscriptions.hooks import notify_commercial_change
from workeasy360.contracts.enums import (
    BillingInterval,
    ChangeTiming,
    CollectionMode,
    RenewalPolicy,
    SubscriptionStatus,
    TrialSource,
)
from workeasy360.contracts.errors import (
    CatalogValidationError,
    CommercialError,
    CommercialRecordNotFound,
    InvalidStateTransition,
    UnsupportedCurrencyOperation,
)
from workeasy360.contracts.events import commercial_events as schemas
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events import services as events
from workeasy360.integration.idempotency import services as idempotency
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MANAGE = "commercial.subscription.manage"

SUBSCRIPTION_ACTIVATIONS = "commercial_subscription_activations_total"


class ConcurrentModification(CommercialError):
    """The subscription changed between the caller reading it and writing.

    Raised rather than overwriting: the caller's decision was made against a
    state that no longer exists, and applying it anyway is how two admins
    clicking "upgrade" produce two upgrades.
    """

    code = "concurrent_modification"


# --- shared helpers -------------------------------------------------------


def _lock(subscription_id: UUID, *, expected_version: int | None) -> Any:
    """Row-lock the subscription and check the optimistic token.

    Both, not either. The lock serialises concurrent commands; the version
    check catches a caller acting on a stale read even when the two commands
    never overlap in time.
    """
    from workeasy360.commercial.subscriptions.models import Subscription

    subscription = (
        Subscription.objects.select_for_update().filter(id=subscription_id).first()
    )
    if subscription is None:
        # The scoped manager has already excluded other tenants' rows, so a
        # miss here is either a bad id or an IDOR attempt. Both are 404.
        raise CommercialRecordNotFound(
            f"No subscription {subscription_id}.", subscription_id=str(subscription_id)
        )
    if expected_version is not None and subscription.version != expected_version:
        raise ConcurrentModification(
            f"Subscription was at version {subscription.version}, not "
            f"{expected_version}. Re-read it and retry.",
            expected_version=expected_version,
            actual_version=subscription.version,
        )
    return subscription


def _bump(subscription: Any, fields: list[str]) -> None:
    subscription.version += 1
    subscription.save(update_fields=[*fields, "version", "updated_at"])


def _open_subscription(
    ctx: TenantContext,
    *,
    billing_interval: BillingInterval,
    collection_mode: CollectionMode,
    renewal_policy: RenewalPolicy,
    now: datetime,
    trial_days: int,
    trial_source: TrialSource | None,
) -> Any:
    """The organization's open subscription, created on first purchase.

    One subscription per organization by construction: a suite purchase
    becomes many items on one renewal cycle rather than four subscriptions
    with four dunning cycles.
    """
    from workeasy360.commercial.billing.services import billing_account_for
    from workeasy360.commercial.subscriptions.domain import add_months
    from workeasy360.commercial.subscriptions.models import Subscription

    existing = (
        Subscription.objects.select_for_update()
        .exclude(status=SubscriptionStatus.EXPIRED.value)
        .order_by("created_at")
        .first()
    )
    if existing is not None:
        return existing

    account = billing_account_for(ctx.organization_id)
    # A row lock cannot protect a row that does not exist yet, so the
    # uniqueness constraint resolves the first-purchase race and this handles
    # losing it. Re-reading rather than raising, because both callers wanted
    # the same thing - an open subscription - and one of them got it.
    months = billing_interval.months
    period_end = add_months(now, months) if months else now + timezone.timedelta(days=365)

    ends = trial_end(start=now, trial_days=trial_days)
    subscription = Subscription(
        organization_id=ctx.organization_id,
        status=(
            SubscriptionStatus.TRIALING.value if ends else SubscriptionStatus.ACTIVE.value
        ),
        currency=account.currency,
        billing_interval=billing_interval.value,
        collection_mode=collection_mode.value,
        renewal_policy=renewal_policy.value,
        current_period_start=now,
        current_period_end=period_end,
        trial_ends_at=ends,
        trial_source=(trial_source or TrialSource.SELF_SERVE).value if ends else "",
        activated_at=None if ends else now,
    )
    try:
        # The savepoint keeps a constraint violation from poisoning the
        # caller's transaction: PostgreSQL aborts the whole transaction on a
        # violation, and without it losing this race would fail the request
        # rather than resolving it.
        with transaction.atomic():
            subscription.save()
    except IntegrityError:
        won_by_another = (
            Subscription.objects.select_for_update()
            .exclude(status=SubscriptionStatus.EXPIRED.value)
            .order_by("created_at")
            .first()
        )
        if won_by_another is None:  # pragma: no cover - violation was not the race
            raise
        return won_by_another
    return subscription


def _quote_for(
    ctx: TenantContext,
    *,
    subscription: Any,
    plan_version_id: UUID | None = None,
    bundle_version_id: UUID | None = None,
    quantity: int,
    at: datetime,
) -> PriceQuote:
    """Resolve a price and refuse any currency other than the subscription's.

    The check is here, at the one point where a price becomes a commitment,
    rather than in the pricing module: pricing answers "what does this cost
    in X?", and whether X is acceptable is a subscription decision.
    """
    from workeasy360.commercial.billing.services import billing_account_for

    account = billing_account_for(ctx.organization_id)
    currency = subscription.currency_enum

    quote = resolve_price(
        plan_version_id=plan_version_id,
        bundle_version_id=bundle_version_id,
        organization_id=ctx.organization_id,
        currency=currency,
        interval=subscription.interval_enum,
        quantity=quantity,
        region=account.billing_region,
        at=at,
    )
    if quote.currency is not currency:
        # Unreachable through resolve_price, which only ever returns the
        # currency it was asked for. Kept because it is the assertion that
        # makes the guarantee structural rather than a property of one
        # function's current implementation.
        raise UnsupportedCurrencyOperation(
            f"Resolved a {quote.currency.value} price for a "
            f"{currency.value} subscription. Cross-currency settlement is not "
            f"supported.",
            subscription_currency=currency.value,
            quote_currency=quote.currency.value,
        )
    return quote


def _assert_sellable(plan: Any) -> None:
    """A new commitment may only be made against a live plan version."""
    if not plan.is_published:
        raise CatalogValidationError(
            f"Plan version {plan.plan_code} v{plan.version} is not published.",
            field="plan_version_id",
        )
    if not plan.plan_status.accepts_new_subscriptions:
        raise CatalogValidationError(
            f"Plan {plan.plan_code!r} is {plan.plan_status.value} and does not "
            f"accept new subscriptions.",
            field="plan_version_id",
        )


def _item_payload(subscription: Any, item: Any) -> dict[str, Any]:
    payload = {
        "organization_id": str(subscription.organization_id),
        "subscription_id": str(subscription.id),
        "subscription_item_id": str(item.id),
        "product_code": item.product_code,
        "plan_code": item.plan_code,
        "plan_version": item.plan_version_number or 0,
        "quantity": item.quantity,
    }
    if item.bundle_code:
        payload["bundle_code"] = item.bundle_code
    return payload


def _publish_item_added(ctx: TenantContext, subscription: Any, item: Any) -> None:
    events.publish(
        events.build_event(
            ctx,
            schemas.SUBSCRIPTION_ITEM_ADDED_V1.event_type,
            1,
            aggregate_type=schemas.SUBSCRIPTION_AGGREGATE,
            aggregate_id=subscription.id,
            payload=_item_payload(subscription, item),
        )
    )


def _create_plan_item(
    *,
    subscription: Any,
    plan: Any,
    quantity: int,
    added_at: datetime,
    quote: PriceQuote | None,
    parent_item: Any | None = None,
    seat_pool: str = "",
    bundle_code: str = "",
) -> Any:
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    snapshot = quote.as_snapshot() if quote is not None else {}
    item = SubscriptionItem(
        organization_id=subscription.organization_id,
        subscription=subscription,
        parent_item=parent_item,
        plan_version_id=plan.id,
        product_code=plan.product_code,
        plan_code=plan.plan_code,
        plan_version_number=plan.version,
        bundle_code=bundle_code,
        seat_pool=seat_pool,
        quantity=quantity,
        added_at=added_at,
        price_id=UUID(snapshot["price_id"]) if snapshot else None,
        price_source=snapshot.get("price_source", ""),
        price_reason=snapshot.get("price_reason", "")[:255],
        unit_amount_minor=snapshot.get("unit_amount_minor"),
        total_amount_minor=snapshot.get("total_amount_minor"),
        currency=snapshot.get("currency", ""),
        interval=snapshot.get("interval", ""),
        price_model=snapshot.get("price_model", ""),
        tax_behaviour=snapshot.get("tax_behaviour", ""),
    )
    item.save()
    return item


# --- commands -------------------------------------------------------------


def purchase_plan(
    ctx: TenantContext,
    *,
    plan_version_id: UUID,
    idempotency_key: str,
    quantity: int = 1,
    with_trial: bool = False,
    trial_source: TrialSource = TrialSource.SELF_SERVE,
    billing_interval: BillingInterval = BillingInterval.MONTHLY,
    collection_mode: CollectionMode = CollectionMode.AUTOMATIC,
    renewal_policy: RenewalPolicy = RenewalPolicy.AUTO_RENEW,
) -> Any:
    """Buy one plan, creating the subscription if this is the first purchase.

    Idempotent on ``idempotency_key``: a retried checkout returns the item it
    created the first time rather than adding a second line.
    """
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    authz.require(ctx, MANAGE)
    now = timezone.now()
    plan = catalog.require_plan_version(plan_version_id)
    _assert_sellable(plan)

    if quantity < 1:
        raise CatalogValidationError("Quantity must be at least 1.", field="quantity")

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.subscription.purchase_plan",
            key=idempotency_key,
            arguments={"plan_version_id": plan_version_id, "quantity": quantity},
        )
        if claim.is_replay:
            return _resolve_item_ref(claim.result_ref)

        trial_days = _trial_days_for(ctx, plan) if with_trial else 0
        subscription = _open_subscription(
            ctx,
            billing_interval=billing_interval,
            collection_mode=collection_mode,
            renewal_policy=renewal_policy,
            now=now,
            trial_days=trial_days,
            trial_source=trial_source,
        )
        assert_mutable(subscription.status_enum, operation="purchase_plan")

        if SubscriptionItem.objects.filter(
            subscription=subscription,
            product_code=plan.product_code,
            removed_at__isnull=True,
        ).exists():
            raise CommercialError(
                f"This subscription already includes {plan.product_code!r}. "
                f"Change the existing line's plan instead of adding a second.",
                product_code=plan.product_code,
            )

        quote = _quote_for(
            ctx,
            subscription=subscription,
            plan_version_id=plan.id,
            quantity=quantity,
            at=now,
        )
        item = _create_plan_item(
            subscription=subscription,
            plan=plan,
            quantity=quantity,
            added_at=now,
            quote=quote,
        )

        _publish_item_added(ctx, subscription, item)
        audit.record(
            ctx,
            "commercial.subscription.plan_purchased",
            resource_type="subscriptions.item",
            resource_id=item.id,
            changes={
                "plan": plan.plan_code,
                "plan_version": plan.version,
                "quantity": quantity,
                "trial_days": trial_days,
            },
        )
        idempotency.complete(claim, result_ref=f"subscriptions.item:{item.id}")
        _bump(subscription, [])

    notify_commercial_change(ctx.organization_id)
    return item


def purchase_bundle(
    ctx: TenantContext,
    *,
    bundle_version_id: UUID,
    idempotency_key: str,
    declined_plan_version_ids: list[UUID] | None = None,
    with_trial: bool = False,
    trial_source: TrialSource = TrialSource.SELF_SERVE,
    billing_interval: BillingInterval = BillingInterval.MONTHLY,
    collection_mode: CollectionMode = CollectionMode.AUTOMATIC,
    renewal_policy: RenewalPolicy = RenewalPolicy.AUTO_RENEW,
) -> Any:
    """Buy a bundle, expanding it into per-plan lines.

    The expansion is **deterministic** (the bundle version's items, in a
    stable order), **atomic** (one transaction - a bundle is never half
    provisioned), **idempotent** (the key, plus a database constraint that
    makes a second open line for the same product impossible even if the key
    were bypassed) and **auditable** (one audit entry and one event per line).

    The bundle line carries the price; the derived lines carry the access.
    Pricing the components would invent revenue, and granting from the bundle
    line directly would be the entitlement shortcut the architecture forbids.
    """
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    authz.require(ctx, MANAGE)
    now = timezone.now()
    declined = set(declined_plan_version_ids or [])

    bundle = catalog.require_bundle_version(bundle_version_id)
    if bundle.published_at is None:
        raise CatalogValidationError(
            f"Bundle version {bundle.bundle_code} v{bundle.version} is not published.",
            field="bundle_version_id",
        )

    wanted = [item for item in bundle.items if item.plan_version_id not in declined]
    mandatory_declined = [
        item.plan_code
        for item in bundle.items
        if item.plan_version_id in declined and not item.is_optional
    ]
    if mandatory_declined:
        raise CatalogValidationError(
            f"Cannot decline mandatory bundle component(s) {sorted(mandatory_declined)}.",
            field="declined_plan_version_ids",
        )
    if not wanted:
        raise CatalogValidationError(
            "Declining every component leaves nothing to purchase.",
            field="declined_plan_version_ids",
        )

    plans = catalog.plan_versions([item.plan_version_id for item in wanted])

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.subscription.purchase_bundle",
            key=idempotency_key,
            arguments={
                "bundle_version_id": bundle_version_id,
                "declined": sorted(str(d) for d in declined),
            },
        )
        if claim.is_replay:
            return _resolve_item_ref(claim.result_ref)

        trial_days = max(
            (plans[item.plan_version_id].trial_days for item in wanted), default=0
        )
        subscription = _open_subscription(
            ctx,
            billing_interval=billing_interval,
            collection_mode=collection_mode,
            renewal_policy=renewal_policy,
            now=now,
            trial_days=trial_days if with_trial else 0,
            trial_source=trial_source,
        )
        assert_mutable(subscription.status_enum, operation="purchase_bundle")

        clashing = list(
            SubscriptionItem.objects.filter(
                subscription=subscription,
                product_code__in=[item.product_code for item in wanted],
                removed_at__isnull=True,
            ).values_list("product_code", flat=True)
        )
        if clashing:
            raise CommercialError(
                f"This subscription already includes {sorted(clashing)}, which "
                f"the bundle also grants. Remove or change those lines first.",
                product_codes=sorted(clashing),
            )

        quote = _quote_for(
            ctx,
            subscription=subscription,
            bundle_version_id=bundle.id,
            quantity=1,
            at=now,
        )
        snapshot = quote.as_snapshot()
        bundle_line = SubscriptionItem(
            organization_id=subscription.organization_id,
            subscription=subscription,
            bundle_version_id=bundle.id,
            bundle_code=bundle.bundle_code,
            # Empty product_code: a bundle line spans products, and leaving it
            # empty keeps it out of the one-open-line-per-product constraint,
            # which is about access rather than billing.
            product_code="",
            quantity=1,
            added_at=now,
            price_id=UUID(snapshot["price_id"]),
            price_source=snapshot["price_source"],
            price_reason=snapshot["price_reason"][:255],
            unit_amount_minor=snapshot["unit_amount_minor"],
            total_amount_minor=snapshot["total_amount_minor"],
            currency=snapshot["currency"],
            interval=snapshot["interval"],
            price_model=snapshot["price_model"],
            tax_behaviour=snapshot["tax_behaviour"],
        )
        bundle_line.save()

        for bundle_item in wanted:
            plan = plans[bundle_item.plan_version_id]
            _assert_sellable(plan)
            derived = _create_plan_item(
                subscription=subscription,
                plan=plan,
                quantity=bundle_item.quantity,
                added_at=now,
                quote=None,
                parent_item=bundle_line,
                seat_pool=bundle_item.seat_pool,
                bundle_code=bundle.bundle_code,
            )
            _publish_item_added(ctx, subscription, derived)

        audit.record(
            ctx,
            "commercial.subscription.bundle_purchased",
            resource_type="subscriptions.item",
            resource_id=bundle_line.id,
            changes={
                "bundle": bundle.bundle_code,
                "bundle_version": bundle.version,
                "expanded_to": sorted(
                    plans[item.plan_version_id].plan_code for item in wanted
                ),
                "declined": sorted(str(d) for d in declined),
            },
        )
        idempotency.complete(claim, result_ref=f"subscriptions.item:{bundle_line.id}")
        _bump(subscription, [])

    notify_commercial_change(ctx.organization_id)
    return bundle_line


def activate_subscription(
    ctx: TenantContext,
    *,
    subscription_id: UUID,
    idempotency_key: str,
    expected_version: int | None = None,
) -> Any:
    """Convert a trial to paid, or confirm a paid signup.

    Deliberately does **not** take or verify a payment: Phase 2 has no
    payment provider, and a method here that pretended to collect money would
    be the most dangerous kind of placeholder. Activation records that the
    commercial relationship is live; what makes it live is out of scope.
    """
    authz.require(ctx, MANAGE)
    now = timezone.now()

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.subscription.activate",
            key=idempotency_key,
            arguments={"subscription_id": subscription_id},
        )
        subscription = _lock(subscription_id, expected_version=expected_version)
        if claim.is_replay:
            return subscription

        previous = subscription.status_enum
        assert_transition(previous, SubscriptionStatus.ACTIVE)

        if previous is SubscriptionStatus.ACTIVE:
            # Already live. Returning early rather than re-applying, because
            # re-applying would publish an ``activated`` event for a state
            # change that did not happen and bump the optimistic token,
            # invalidating every concurrent caller's read for no reason.
            # The self-transition is legal; performing it is not useful.
            idempotency.complete(
                claim, result_ref=f"subscriptions.subscription:{subscription.id}"
            )
            return subscription

        was_trial = previous is SubscriptionStatus.TRIALING
        subscription.status = SubscriptionStatus.ACTIVE.value
        fields = ["status"]
        if subscription.activated_at is None:
            subscription.activated_at = now
            fields.append("activated_at")
        if was_trial:
            # The trial is over the moment it converts. Leaving the date in
            # the future would keep access_ends_at() pinned to it and expire
            # a paying customer.
            subscription.trial_ends_at = now
            fields.append("trial_ends_at")
        _bump(subscription, fields)

        events.publish(
            events.build_event(
                ctx,
                schemas.SUBSCRIPTION_ACTIVATED_V1.event_type,
                1,
                aggregate_type=schemas.SUBSCRIPTION_AGGREGATE,
                aggregate_id=subscription.id,
                payload={
                    "organization_id": str(ctx.organization_id),
                    "subscription_id": str(subscription.id),
                    "from_status": previous.value,
                    "to_status": SubscriptionStatus.ACTIVE.value,
                    "product_codes": _open_product_codes(subscription),
                },
            )
        )
        if was_trial:
            events.publish(
                events.build_event(
                    ctx,
                    schemas.TRIAL_ENDED_V1.event_type,
                    1,
                    aggregate_type=schemas.SUBSCRIPTION_AGGREGATE,
                    aggregate_id=subscription.id,
                    payload={
                        "organization_id": str(ctx.organization_id),
                        "subscription_id": str(subscription.id),
                        "converted": True,
                    },
                )
            )
        audit.record(
            ctx,
            "commercial.subscription.activated",
            resource_type="subscriptions.subscription",
            resource_id=subscription.id,
            changes={"from": previous.value, "to": SubscriptionStatus.ACTIVE.value},
        )
        idempotency.complete(
            claim, result_ref=f"subscriptions.subscription:{subscription.id}"
        )

    metrics.increment(SUBSCRIPTION_ACTIVATIONS, from_status=previous.value)
    notify_commercial_change(ctx.organization_id)
    return subscription


def change_plan(
    ctx: TenantContext,
    *,
    subscription_item_id: UUID,
    to_plan_version_id: UUID,
    idempotency_key: str,
    timing: ChangeTiming | None = None,
    expected_version: int | None = None,
) -> Any:
    """Upgrade or downgrade one line.

    A change is **not** an edit. The outgoing line is closed at the effective
    moment and a new line opens there, so the subscription's history contains
    both what the customer had and what they moved to, with the exact instant
    the change took effect. An edit would leave only the destination and make
    a mid-period invoice impossible to explain.

    Timing defaults by direction: upgrades immediately, downgrades and
    lateral moves at the period end. The caller may override, which is what
    "downgrade me now" in a support tool needs.
    """
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    authz.require(ctx, MANAGE)
    now = timezone.now()
    target = catalog.require_plan_version(to_plan_version_id)
    _assert_sellable(target)

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.subscription.change_plan",
            key=idempotency_key,
            arguments={
                "subscription_item_id": subscription_item_id,
                "to_plan_version_id": to_plan_version_id,
            },
        )
        if claim.is_replay:
            return _resolve_item_ref(claim.result_ref)

        current_item = (
            SubscriptionItem.objects.select_related("subscription")
            .filter(id=subscription_item_id, removed_at__isnull=True)
            .first()
        )
        if current_item is None:
            raise CommercialRecordNotFound(
                f"No open subscription item {subscription_item_id}.",
                subscription_item_id=str(subscription_item_id),
            )
        if current_item.is_derived:
            raise CommercialError(
                "A bundle component cannot be changed on its own. Change the "
                "bundle, or unbundle first.",
                subscription_item_id=str(subscription_item_id),
            )
        if current_item.plan_version_id is None:
            raise CommercialError(
                "A bundle line has no plan to change.",
                subscription_item_id=str(subscription_item_id),
            )

        subscription = _lock(
            current_item.subscription_id, expected_version=expected_version
        )
        assert_mutable(subscription.status_enum, operation="change_plan")

        source = catalog.require_plan_version(current_item.plan_version_id)
        if source.product_code != target.product_code:
            raise CatalogValidationError(
                f"Cannot change a {source.product_code!r} line to a "
                f"{target.product_code!r} plan. That is a purchase and a "
                f"removal, not a plan change.",
                field="to_plan_version_id",
            )
        if source.id == target.id:
            raise InvalidStateTransition(
                "The line is already on this plan version.",
                entity="subscriptions.SubscriptionItem",
                from_status=str(source.id),
                to_status=str(target.id),
            )

        direction = compare_plan_versions(
            from_seats=source.included_seats * current_item.quantity,
            to_seats=target.included_seats * current_item.quantity,
            from_features=source.feature_map(),
            to_features=target.feature_map(),
        )
        chosen = timing or default_timing(direction)
        effective_at = effective_moment(
            timing=chosen, now=now, period_end=subscription.current_period_end
        )
        # A change cannot take effect before the line it changes does.
        #
        # Reached by rescheduling a pending change: the outgoing line starts
        # at the period end, and an upgrade would otherwise default to
        # "immediate" and close it before it opened. The database check
        # constraint refuses that window, so without this the caller gets an
        # IntegrityError instead of the thing they asked for - which is to
        # replace what is queued.
        #
        # Clamping leaves the superseded line with a zero-width window, which
        # ``is_effective_at`` never matches, so it grants nothing.
        effective_at = max(effective_at, current_item.added_at)

        if direction == "downgrade":
            _check_seat_headroom(
                ctx,
                subscription=subscription,
                item=current_item,
                target=target,
                effective_at=effective_at,
            )

        quote = _quote_for(
            ctx,
            subscription=subscription,
            plan_version_id=target.id,
            quantity=current_item.quantity,
            at=effective_at,
        )

        current_item.removed_at = effective_at
        current_item.save(update_fields=["removed_at", "updated_at"])

        new_item = _create_plan_item(
            subscription=subscription,
            plan=target,
            quantity=current_item.quantity,
            added_at=effective_at,
            quote=quote,
        )

        events.publish(
            events.build_event(
                ctx,
                schemas.PLAN_CHANGED_V1.event_type,
                1,
                aggregate_type=schemas.SUBSCRIPTION_AGGREGATE,
                aggregate_id=subscription.id,
                payload={
                    "organization_id": str(ctx.organization_id),
                    "subscription_id": str(subscription.id),
                    "product_code": target.product_code,
                    "from_plan_code": source.plan_code,
                    "from_plan_version": source.version,
                    "to_plan_code": target.plan_code,
                    "to_plan_version": target.version,
                    "direction": direction,
                    "effective_at": effective_at.isoformat(),
                },
            )
        )
        audit.record(
            ctx,
            "commercial.subscription.plan_changed",
            resource_type="subscriptions.item",
            resource_id=new_item.id,
            changes={
                "from": f"{source.plan_code} v{source.version}",
                "to": f"{target.plan_code} v{target.version}",
                "direction": direction,
                "timing": chosen.value,
                "effective_at": effective_at.isoformat(),
            },
        )
        idempotency.complete(claim, result_ref=f"subscriptions.item:{new_item.id}")
        _bump(subscription, [])

    notify_commercial_change(ctx.organization_id)
    return new_item


def cancel_subscription(
    ctx: TenantContext,
    *,
    subscription_id: UUID,
    idempotency_key: str,
    immediate: bool = False,
    reason_code: str = "customer_request",
    expected_version: int | None = None,
) -> Any:
    """Schedule the end of the commercial relationship.

    ``immediate=False`` is the default and the normal case: the customer keeps
    access until ``current_period_end``, because they paid for it. Revoking on
    request is theft and generates chargebacks.

    ``immediate=True`` exists for the cases where it is correct - fraud,
    a mistaken signup inside a refund window - and is audited with a reason.
    """
    authz.require(ctx, MANAGE)
    now = timezone.now()

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.subscription.cancel",
            key=idempotency_key,
            arguments={"subscription_id": subscription_id, "immediate": immediate},
        )
        subscription = _lock(subscription_id, expected_version=expected_version)
        if claim.is_replay:
            return subscription

        previous = subscription.status_enum
        assert_transition(previous, SubscriptionStatus.CANCELLED)

        effective_at = now if immediate else max(subscription.current_period_end, now)
        subscription.status = SubscriptionStatus.CANCELLED.value
        subscription.cancelled_at = now
        subscription.cancel_at = effective_at
        # Auto-renewal must stop, or the renewal sweep would roll the period
        # past the cancellation date and quietly restore access.
        subscription.renewal_policy = RenewalPolicy.NONE.value
        _bump(subscription, ["status", "cancelled_at", "cancel_at", "renewal_policy"])

        events.publish(
            events.build_event(
                ctx,
                schemas.SUBSCRIPTION_CANCELLED_V1.event_type,
                1,
                aggregate_type=schemas.SUBSCRIPTION_AGGREGATE,
                aggregate_id=subscription.id,
                payload={
                    "organization_id": str(ctx.organization_id),
                    "subscription_id": str(subscription.id),
                    "effective_at": effective_at.isoformat(),
                    "immediate": immediate,
                    "reason_code": reason_code,
                },
            )
        )
        audit.record(
            ctx,
            "commercial.subscription.cancelled",
            resource_type="subscriptions.subscription",
            resource_id=subscription.id,
            changes={
                "from": previous.value,
                "immediate": immediate,
                "effective_at": effective_at.isoformat(),
                "reason_code": reason_code,
            },
        )
        idempotency.complete(
            claim, result_ref=f"subscriptions.subscription:{subscription.id}"
        )

    notify_commercial_change(ctx.organization_id)
    return subscription


# --- helpers used by the commands ----------------------------------------


def _trial_days_for(ctx: TenantContext, plan: Any) -> int:
    """Trial length for this plan, or zero if this organization has had one.

    One trial per product per organization. Enforced from durable subscription
    history rather than a flag, because items are never deleted and a flag is
    one data-fix away from being cleared.
    """
    from workeasy360.commercial.subscriptions.selectors import has_used_a_trial

    if plan.trial_days <= 0:
        return 0
    if has_used_a_trial(ctx.organization_id, product_code=plan.product_code):
        logger.info(
            "commercial.trial.refused_repeat",
            product_code=plan.product_code,
        )
        return 0
    return plan.trial_days


def _check_seat_headroom(
    ctx: TenantContext,
    *,
    subscription: Any,
    item: Any,
    target: Any,
    effective_at: datetime,
) -> None:
    """Refuse a downgrade that would strand assigned seats, with a deadline.

    The architecture's rule is that a seat reduction must not auto-revoke
    anybody: assignments beyond the new limit are marked over-limit and the
    organization is given a deadline to choose. That choice cannot be made
    retroactively for an *immediate* downgrade, so an immediate downgrade
    with too many seats assigned is refused and the caller is told how many
    to release. A scheduled (period-end) downgrade is permitted, because
    there is time to remediate before it takes effect.
    """
    from workeasy360.contracts.errors import SeatRemediationRequired
    from workeasy360.platform.entitlements.services import seats

    if effective_at > timezone.now():
        return

    pool_key = item.seat_pool or item.product_code
    assigned = seats.assigned_count(ctx.organization_id, pool_key=pool_key)
    capacity = target.included_seats * item.quantity
    if assigned > capacity:
        raise SeatRemediationRequired(
            f"{assigned} seats are assigned in {pool_key!r} but the target "
            f"plan provides {capacity}. Release {assigned - capacity} seat(s), "
            f"or schedule the downgrade for the period end "
            f"({subscription.current_period_end.isoformat()}) so the "
            f"organization has time to choose.",
            pool_key=pool_key,
            assigned=assigned,
            capacity=capacity,
            period_end=subscription.current_period_end.isoformat(),
        )


def _open_product_codes(subscription: Any) -> list[str]:
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    return sorted(
        SubscriptionItem.objects.filter(
            subscription=subscription, removed_at__isnull=True
        )
        .exclude(product_code="")
        .values_list("product_code", flat=True)
    )


def _resolve_item_ref(result_ref: str) -> Any:
    """Return the object a replayed command originally produced.

    Raises rather than returning ``None``. A replay that cannot find what it
    originally produced has not succeeded - it has lost the answer - and
    handing back ``None`` makes the caller's next attribute access the place
    the failure surfaces, several frames from the cause. The reachable case is
    an operator having removed the row; the unreachable one is a claim that
    committed without its result, which the shared transaction prevents.
    """
    from workeasy360.commercial.subscriptions.models import (
        Subscription,
        SubscriptionItem,
    )

    kind, _, raw = result_ref.partition(":")
    model = {
        "subscriptions.item": SubscriptionItem,
        "subscriptions.subscription": Subscription,
    }.get(kind)

    resolved = model.objects.filter(id=raw).first() if model is not None else None
    if resolved is None:
        raise CommercialRecordNotFound(
            f"This command was already performed, but the record it produced "
            f"({result_ref!r}) can no longer be read. Issue a new idempotency "
            f"key only after establishing what the original command did.",
            result_ref=result_ref,
        )
    return resolved
