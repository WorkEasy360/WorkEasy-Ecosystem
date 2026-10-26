"""Orders: pay first, then grant (Phase 9).

    Explore -> quote -> place (order + its invoice, nothing granted)
            -> pay the invoice (hosted checkout, the existing path)
            -> signed webhook -> invoice PAID, order PAID       [settlement]
            -> fulfil: subscription line at the paid price       [here]
            -> provisioning -> entitlement -> product access

**What the browser sends**: identifiers only - a plan or bundle version, a
billing interval, a quantity, declined bundle components. Price, currency,
proration, trial eligibility and whether payment is needed are all decided
here, from the catalog, pricing, the billing account and subscription history.
A self-serve order accepts exactly what Explore lists: public, available plans
and bundles of listed products.

**What needs payment now**:

- **A new subscription** is charged the full first period.
- **A line added to a running subscription** is charged the prorated rest of
  the current period; it is invoiced in full from the next period.
- **Nothing is charged now** for:
  - a trial, or a free-trial offer;
  - a line joining a trial;
  - a zero price;
  - an account billed by invoice or by a partner (a sales-led account,
    which only staff or a partner can set).

  Those are fulfilled at once and recorded with nothing due, so the order
  history is complete.

**Durable, idempotent, concurrency-safe**:

- **Idempotency.** The ``Idempotency-Key`` is claimed with every argument
  fingerprinted.
- **At most one pending order per organization**, enforced by a partial
  unique index.
- **Fulfilment cannot be lost.** It runs after the payment commits, and the
  payments clock re-drives any order that is paid but not yet fulfilled.
- **Fulfilment runs once.** It takes the locks in the same order as
  settlement, cancellation and expiry (invoice, then order), so none of them
  can deadlock with another. The order's status is re-checked under the lock.
- **Paid but impossible** (the product became held some other way): the
  order fails and its payment is refunded automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.contracts.enums import (
    BillingInterval,
    CollectionMode,
    PlanBillingType,
    PlanKind,
    SubscriptionStatus,
)
from workeasy360.contracts.errors import (
    CatalogValidationError,
    CommercialError,
    OrderNotAllowed,
    OrderNotFound,
)
from workeasy360.contracts.values.cursor import decode_cursor, encode_cursor
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.idempotency import services as idempotency
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MANAGE = "commercial.subscription.manage"
#: Either lets a member read the organization's orders.
VIEW_ANY = ("platform.billing.view", "commercial.subscription.view")
STAFF_VIEW = "platform.staff_payment.view"

MAX_QUANTITY = 100_000
PAGE_SIZE = 25
MAX_PAGE = 50

_CLOSED_FOR_ORDERS = frozenset(
    {SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE, SubscriptionStatus.SUSPENDED}
)


# --- what is being bought ---------------------------------------------------------


@dataclass(frozen=True)
class _Target:
    kind: str
    plan: Any = None
    bundle: Any = None
    subscription: Any = None
    wanted: tuple[Any, ...] = ()
    declined: tuple[UUID, ...] = ()
    product_codes: tuple[str, ...] = ()
    name: str = ""
    free_trial: bool = False


@dataclass(frozen=True)
class Quote:
    """What placing this order would do. Informational until placed; placing
    re-derives every figure."""

    target: _Target
    currency: str
    billing_interval: BillingInterval
    quantity: int
    recurring_amount_minor: int
    amount_due_minor: int
    prorated: bool
    is_trial: bool
    trial_days: int
    collection_mode: str
    snapshot: dict[str, Any] = field(default_factory=dict)
    open_subscription: Any = None

    @property
    def requires_payment(self) -> bool:
        return (
            self.amount_due_minor > 0
            and not self.is_trial
            and self.collection_mode == CollectionMode.AUTOMATIC.value
            and not (
                self.open_subscription is not None
                and self.open_subscription.status is SubscriptionStatus.TRIALING
                and self.target.kind != "activation"
            )
        )

    @property
    def description(self) -> str:
        if self.target.kind == "activation":
            return f"Activate subscription ({self.billing_interval.value})"[:255]
        suffix = f" x{self.quantity}" if self.target.kind == "plan" else ""
        return f"{self.target.name}{suffix} ({self.billing_interval.value})"[:255]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.target.kind,
            "description": self.description,
            "product_codes": list(self.target.product_codes),
            "currency": self.currency,
            "billing_interval": self.billing_interval.value,
            "quantity": self.quantity,
            "recurring_amount_minor": self.recurring_amount_minor,
            "amount_due_minor": self.amount_due_minor if self.requires_payment else 0,
            "prorated": self.prorated and self.requires_payment,
            "is_trial": self.is_trial,
            "trial_days": self.trial_days,
            "requires_payment": self.requires_payment,
        }


def _open_subscription() -> Any:
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_for_organization,
    )

    return next(
        (
            s
            for s in subscriptions_for_organization()
            if s.status is not SubscriptionStatus.EXPIRED
        ),
        None,
    )


def _uuid(raw: Any, field_name: str) -> UUID | None:
    if raw in (None, ""):
        return None
    try:
        return UUID(str(raw))
    except ValueError as exc:
        raise CatalogValidationError(
            f"{field_name} is not a valid id.", field=field_name
        ) from exc


def _declined(raw: Any) -> tuple[UUID, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list) or len(raw) > 50:
        raise CatalogValidationError(
            "declined_plan_version_ids must be a list of ids.",
            field="declined_plan_version_ids",
        )
    return tuple(
        sorted({_uuid(value, "declined_plan_version_ids") for value in raw}, key=str)  # type: ignore[arg-type]
    )


def _target(
    *,
    plan_version_id: Any,
    bundle_version_id: Any,
    activate_subscription_id: Any,
    declined_plan_version_ids: Any,
    open_subscription: Any,
    at: datetime,
) -> _Target:
    plan_id = _uuid(plan_version_id, "plan_version_id")
    bundle_id = _uuid(bundle_version_id, "bundle_version_id")
    activate_id = _uuid(activate_subscription_id, "activate_subscription_id")
    if sum(value is not None for value in (plan_id, bundle_id, activate_id)) != 1:
        raise CatalogValidationError(
            "Name exactly one of plan_version_id, bundle_version_id or "
            "activate_subscription_id.",
            field="plan_version_id",
        )
    if activate_id is not None:
        if (
            open_subscription is None
            or open_subscription.id != activate_id
            or open_subscription.status is not SubscriptionStatus.TRIALING
        ):
            raise OrderNotAllowed(
                "Only this organization's trial can be activated.",
                reason="subscription_not_open",
            )
        return _Target(
            kind="activation",
            subscription=open_subscription,
            product_codes=tuple(
                sorted({i.product_code for i in open_subscription.effective_items(at)})
            ),
            name="Subscription",
        )

    # A self-serve order buys exactly what Explore lists - never a private,
    # draft or withdrawn offer named by a guessed id.
    offers = catalog.marketplace_offers(at=at)
    if plan_id is not None:
        if str(plan_id) not in {p["plan_version_id"] for p in offers["plans"]}:
            raise OrderNotAllowed("This plan is not for sale.", reason="not_for_sale")
        plan = catalog.require_plan_version(plan_id)
        return _Target(
            kind="plan",
            plan=plan,
            product_codes=(plan.product_code,),
            name=plan.plan_name,
            free_trial=plan.billing_type is PlanBillingType.FREE_TRIAL,
        )
    if str(bundle_id) not in {b["bundle_version_id"] for b in offers["bundles"]}:
        raise OrderNotAllowed("This bundle is not for sale.", reason="not_for_sale")
    bundle = catalog.require_bundle_version(bundle_id)  # type: ignore[arg-type]
    declined = _declined(declined_plan_version_ids)
    if any(i.plan_version_id in declined and not i.is_optional for i in bundle.items):
        raise CatalogValidationError(
            "A mandatory component of the bundle cannot be declined.",
            field="declined_plan_version_ids",
        )
    wanted = tuple(i for i in bundle.items if i.plan_version_id not in declined)
    if not wanted:
        raise CatalogValidationError(
            "Declining every component leaves nothing to buy.",
            field="declined_plan_version_ids",
        )
    return _Target(
        kind="bundle",
        bundle=bundle,
        wanted=wanted,
        declined=declined,
        product_codes=tuple(sorted({i.product_code for i in wanted})),
        name=bundle.bundle_name,
        free_trial=bundle.billing_type is PlanBillingType.FREE_TRIAL,
    )


def _trial_days(ctx: TenantContext, target: _Target) -> int:
    """How long a trial this organization may still have of ``target`` - one
    per product, from durable history (``has_used_a_trial``)."""
    from workeasy360.commercial.subscriptions.selectors import has_used_a_trial

    if target.kind == "plan":
        days = target.plan.trial_days
    else:
        plans = catalog.plan_versions([i.plan_version_id for i in target.wanted])
        days = (
            target.bundle.trial_days
            if target.free_trial
            else max(
                (plans[i.plan_version_id].trial_days for i in target.wanted), default=0
            )
        )
    if days <= 0:
        return 0
    if any(
        has_used_a_trial(ctx.organization_id, product_code=code)
        for code in target.product_codes
    ):
        return 0
    return days


def _price(
    ctx: TenantContext,
    target: _Target,
    *,
    currency: str,
    interval: BillingInterval,
    quantity: int,
    region: str,
    at: datetime,
) -> Any:
    from workeasy360.commercial.pricing.resolution import resolve_price
    from workeasy360.contracts.values.money import CurrencyCode

    return resolve_price(
        plan_version_id=target.plan.id if target.kind == "plan" else None,
        bundle_version_id=target.bundle.id if target.kind == "bundle" else None,
        organization_id=ctx.organization_id,
        currency=CurrencyCode(currency),
        interval=interval,
        quantity=quantity,
        region=region,
        at=at,
        partner_id=ctx.partner_id,
    )


def _quote(
    ctx: TenantContext,
    *,
    plan_version_id: Any = None,
    bundle_version_id: Any = None,
    activate_subscription_id: Any = None,
    declined_plan_version_ids: Any = None,
    billing_interval: Any = None,
    quantity: Any = 1,
    with_trial: Any = False,
    at: datetime,
) -> Quote:
    from workeasy360.commercial.billing.services import billing_account_or_none
    from workeasy360.commercial.subscriptions.domain import prorate
    from workeasy360.commercial.subscriptions.selectors import sale_facts

    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or not (1 <= quantity <= MAX_QUANTITY)
    ):
        raise CatalogValidationError(
            f"Quantity must be between 1 and {MAX_QUANTITY:,}.", field="quantity"
        )
    if not isinstance(with_trial, bool):
        raise CatalogValidationError(
            "with_trial must be true or false.", field="with_trial"
        )
    try:
        interval = BillingInterval(billing_interval or BillingInterval.MONTHLY.value)
    except ValueError as exc:
        raise CatalogValidationError(
            "billing_interval is not a supported interval.", field="billing_interval"
        ) from exc
    if interval is BillingInterval.ONE_TIME:
        raise CatalogValidationError(
            "A subscription renews; choose a recurring interval.",
            field="billing_interval",
        )

    account = billing_account_or_none(ctx.organization_id)
    if account is None:
        raise OrderNotAllowed(
            "Set up billing (currency and billing email) before buying.",
            reason="billing_account_required",
        )
    subscription = _open_subscription()
    target = _target(
        plan_version_id=plan_version_id,
        bundle_version_id=bundle_version_id,
        activate_subscription_id=activate_subscription_id,
        declined_plan_version_ids=declined_plan_version_ids,
        open_subscription=subscription,
        at=at,
    )

    if target.kind == "activation":
        facts = [
            f
            for f in sale_facts(ctx.organization_id)
            if f.subscription_id == subscription.id
            and f.added_at <= at
            and (f.removed_at is None or f.removed_at > at)
        ]
        recurring = sum(f.total_amount_minor for f in facts)
        return Quote(
            target=target,
            currency=subscription.currency,
            billing_interval=subscription.billing_interval,
            quantity=1,
            recurring_amount_minor=recurring,
            amount_due_minor=recurring,
            prorated=False,
            is_trial=False,
            trial_days=0,
            collection_mode=account.collection_mode,
            open_subscription=subscription,
        )

    if target.free_trial and subscription is not None:
        raise OrderNotAllowed(
            "A free trial starts a new subscription, and this organization "
            "already has one. Choose a paid plan instead.",
            reason="subscription_exists",
        )
    if target.kind == "plan" and target.plan.plan_kind is PlanKind.ADD_ON:
        held_now = (
            {i.product_code for i in subscription.effective_items(at)}
            if subscription is not None
            else set()
        )
        if target.plan.addon_for_code not in held_now:
            raise OrderNotAllowed(
                f"{target.plan.plan_name} is an add-on for "
                f"{target.plan.addon_for_code!r}, which this organization does "
                f"not have.",
                reason="add_on_requires_base",
                requires=target.plan.addon_for_code,
            )
    if subscription is not None:
        if subscription.status in _CLOSED_FOR_ORDERS:
            raise OrderNotAllowed(
                "Pay the open invoice before buying anything else.",
                reason="outstanding_balance",
            )
        if subscription.status not in (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIALING,
        ):
            raise OrderNotAllowed(
                "The subscription is ending; nothing can be added to it.",
                reason="subscription_not_open",
            )
        held = {i.product_code for i in subscription.effective_items(at)}
        if held & set(target.product_codes):
            raise OrderNotAllowed(
                "This organization already has that product.",
                reason="already_owned",
                product_codes=sorted(held & set(target.product_codes)),
            )

    trial_days = 0
    if subscription is None and (with_trial or target.free_trial):
        trial_days = _trial_days(ctx, target)
        if target.free_trial and trial_days == 0:
            raise OrderNotAllowed(
                "This organization has already had its free trial of this product.",
                reason="trial_used",
            )

    currency = subscription.currency if subscription is not None else account.currency
    chosen = subscription.billing_interval if subscription is not None else interval
    quote = _price(
        ctx,
        target,
        currency=currency,
        interval=chosen,
        quantity=quantity if target.kind == "plan" else 1,
        region=account.billing_region,
        at=at,
    )
    recurring = int(quote.total.minor_units)
    running = (
        subscription is not None and subscription.status is SubscriptionStatus.ACTIVE
    )
    due = (
        prorate(
            recurring,
            period_start=subscription.current_period_start,
            period_end=subscription.current_period_end,
            at=at,
        )
        if running
        else recurring
    )
    return Quote(
        target=target,
        currency=currency,
        billing_interval=chosen,
        quantity=quantity if target.kind == "plan" else 1,
        recurring_amount_minor=recurring,
        amount_due_minor=due,
        prorated=running,
        is_trial=trial_days > 0,
        trial_days=trial_days,
        collection_mode=account.collection_mode,
        snapshot=quote.as_snapshot(),
        open_subscription=subscription,
    )


def quote(ctx: TenantContext, **request: Any) -> dict[str, Any]:
    """What placing this order would cost and do - for the checkout page.
    Placing it re-derives every figure; nothing here is a commitment."""
    authz.require(ctx, MANAGE)
    return _quote(ctx, at=timezone.now(), **request).as_dict()


# --- placing ---------------------------------------------------------------------


def _next_number() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('payments_order_number_seq')")
        (value,) = cursor.fetchone()
    return f"ORD-{int(value):06d}"


def _pending_order_id() -> UUID | None:
    from workeasy360.commercial.payments.models import Order

    return (
        Order.objects.filter(status=Order.Status.PENDING_PAYMENT)
        .values_list("id", flat=True)
        .first()
    )


def _refuse_while_pending() -> None:
    pending = _pending_order_id()
    if pending is not None:
        raise OrderNotAllowed(
            "Another order is awaiting payment. Pay or cancel it first.",
            reason="order_pending",
            order_id=str(pending),
        )


def place(ctx: TenantContext, *, idempotency_key: str, **request: Any) -> dict[str, Any]:
    """Place an order. Returns the order; ``requires_payment`` says whether the
    browser goes on to pay its invoice. Replays with the same key return the
    same order."""
    from workeasy360.commercial.payments.models import Order

    authz.require(ctx, MANAGE)
    if not idempotency_key or len(idempotency_key) > 128:
        raise CatalogValidationError(
            "An Idempotency-Key of at most 128 characters is required.",
            field="idempotency_key",
        )
    replay = Order.objects.filter(idempotency_key=idempotency_key).first()
    if replay is not None:
        _assert_same_request(ctx, replay, idempotency_key, request)
        return order_view(replay)
    _refuse_while_pending()

    now = timezone.now()
    q = _quote(ctx, at=now, **request)
    if q.requires_payment:
        order = _place_payable(ctx, q, idempotency_key=idempotency_key, request=request)
    else:
        order = _place_fulfilled(ctx, q, idempotency_key=idempotency_key, request=request)
    metrics.increment(
        "commercial_orders_placed_total",
        kind=q.target.kind,
        requires_payment=str(q.requires_payment).lower(),
    )
    return order_view(order)


def _fingerprint(request: dict[str, Any]) -> dict[str, Any]:
    return {key: request.get(key) for key in sorted(request)}


def _assert_same_request(
    ctx: TenantContext, order: Any, key: str, request: dict[str, Any]
) -> None:
    """A replayed key must carry the same arguments, or it is a conflict - the
    integration idempotency ledger's own rule, checked through it."""
    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.order.place",
            key=key,
            arguments=_fingerprint(request),
        )
        if not claim.is_replay:  # pragma: no cover - the order exists, so the claim does
            idempotency.complete(claim, result_ref=f"payments.order:{order.id}")


def _new_order(ctx: TenantContext, q: Quote, *, key: str, status: str) -> Any:
    from workeasy360.commercial.payments.models import Order

    return Order(
        organization_id=ctx.organization_id,
        number=_next_number(),
        kind=q.target.kind,
        status=status,
        plan_version_id=q.target.plan.id if q.target.kind == "plan" else None,
        bundle_version_id=q.target.bundle.id if q.target.kind == "bundle" else None,
        subscription_id=q.target.subscription.id
        if q.target.kind == "activation"
        else None,
        declined_plan_version_ids=[str(d) for d in q.target.declined],
        quantity=q.quantity,
        billing_interval=q.billing_interval.value,
        currency=q.currency,
        description=q.description,
        product_codes=list(q.target.product_codes),
        recurring_amount_minor=q.recurring_amount_minor,
        amount_due_minor=q.amount_due_minor if q.requires_payment else 0,
        price_snapshot=q.snapshot,
        is_trial=q.is_trial,
        placed_by_user_id=ctx.user_id,
        idempotency_key=key,
    )


def _place_payable(
    ctx: TenantContext, q: Quote, *, idempotency_key: str, request: dict[str, Any]
) -> Any:
    from workeasy360.commercial.payments.models import Invoice, InvoiceLine, Order

    now = timezone.now()
    expires = now + timedelta(hours=max(1, int(settings.ORDER_TTL_HOURS)))
    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.order.place",
            key=idempotency_key,
            arguments=_fingerprint(request),
        )
        if claim.is_replay:
            return Order.objects.get(idempotency_key=idempotency_key)
        order = _new_order(
            ctx, q, key=idempotency_key, status=Order.Status.PENDING_PAYMENT
        )
        order.expires_at = expires
        try:
            with transaction.atomic():
                order.save()
        except IntegrityError as exc:
            pending = _pending_order_id()
            raise OrderNotAllowed(
                "Another order is awaiting payment. Pay or cancel it first.",
                reason="order_pending",
                order_id=str(pending) if pending else None,
            ) from exc
        running = q.open_subscription
        period_end = (
            running.current_period_end
            if running is not None and q.target.kind != "activation"
            else _period_end(now, q.billing_interval)
        )
        invoice = Invoice.objects.create(
            order=order,
            subscription_id=(
                running.id
                if running is not None and running.status is SubscriptionStatus.ACTIVE
                else (q.target.subscription.id if q.target.kind == "activation" else None)
            ),
            number=_invoice_number(),
            currency=q.currency,
            total_minor=q.amount_due_minor,
            billing_interval=q.billing_interval.value,
            collection_mode=CollectionMode.AUTOMATIC.value,
            period_start=now,
            period_end=period_end,
            issued_at=now,
            due_at=expires,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            subscription_item_id=None,
            description=q.description,
            product_code=q.target.product_codes[0] if q.target.kind == "plan" else "",
            plan_code=q.target.plan.plan_code if q.target.kind == "plan" else "",
            bundle_code=q.target.bundle.bundle_code if q.target.kind == "bundle" else "",
            quantity=q.quantity,
            amount_minor=q.amount_due_minor,
        )
        _sync_summary(order)
        audit.record(
            ctx,
            "commercial.order.placed",
            resource_type="payments.order",
            resource_id=order.id,
            changes={
                "number": order.number,
                "kind": order.kind,
                "products": order.product_codes,
                "amount_due_minor": order.amount_due_minor,
                "currency": order.currency,
                "prorated": q.prorated,
            },
        )
        idempotency.complete(claim, result_ref=f"payments.order:{order.id}")
    return order


def _place_fulfilled(
    ctx: TenantContext, q: Quote, *, idempotency_key: str, request: dict[str, Any]
) -> Any:
    """Nothing is owed now: buy through the subscription services (which
    authorize, claim their own idempotency and notify provisioning), then
    record the order. A retry replays both steps."""
    from workeasy360.commercial.payments.models import Order
    from workeasy360.commercial.subscriptions import services as subscriptions

    inner_key = f"order:{idempotency_key}"[:128]
    line_id: UUID | None = None
    if q.target.kind == "activation":
        subscription = subscriptions.activate_subscription(
            ctx, subscription_id=q.target.subscription.id, idempotency_key=inner_key
        )
        subscription_id = subscription.id
    elif q.target.kind == "plan":
        line = subscriptions.purchase_plan(
            ctx,
            plan_version_id=q.target.plan.id,
            idempotency_key=inner_key,
            quantity=q.quantity,
            with_trial=q.is_trial,
            billing_interval=q.billing_interval,
        )
        line_id, subscription_id = line.id, line.subscription_id
    else:
        line = subscriptions.purchase_bundle(
            ctx,
            bundle_version_id=q.target.bundle.id,
            idempotency_key=inner_key,
            declined_plan_version_ids=list(q.target.declined),
            with_trial=q.is_trial,
            billing_interval=q.billing_interval,
        )
        line_id, subscription_id = line.id, line.subscription_id

    now = timezone.now()
    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.order.place",
            key=idempotency_key,
            arguments=_fingerprint(request),
        )
        if claim.is_replay:
            return Order.objects.get(idempotency_key=idempotency_key)
        order = _new_order(ctx, q, key=idempotency_key, status=Order.Status.FULFILLED)
        order.subscription_id = subscription_id
        order.subscription_item_id = line_id
        order.fulfilled_at = now
        order.save()
        _sync_summary(order)
        audit.record(
            ctx,
            "commercial.order.fulfilled",
            resource_type="payments.order",
            resource_id=order.id,
            changes={
                "number": order.number,
                "kind": order.kind,
                "products": order.product_codes,
                "trial": order.is_trial,
                "paid": False,
            },
        )
        idempotency.complete(claim, result_ref=f"payments.order:{order.id}")
    return order


def _period_end(start: datetime, interval: BillingInterval) -> datetime:
    from workeasy360.commercial.subscriptions.domain import add_months

    return (
        add_months(start, interval.months)
        if interval.months
        else start + timedelta(days=365)
    )


def _invoice_number() -> str:
    from workeasy360.commercial.payments.invoicing import next_invoice_number

    return next_invoice_number()


# --- cancelling, reading ----------------------------------------------------------


def _locked(order_id: UUID) -> tuple[Any, Any]:
    """``(invoice, order)``, locked in that order - the order every writer uses."""
    from workeasy360.commercial.payments.models import Invoice, Order

    invoice = Invoice.objects.select_for_update().filter(order_id=order_id).first()
    order = Order.objects.select_for_update().filter(id=order_id).first()
    return invoice, order


def cancel(ctx: TenantContext, order_id: UUID) -> dict[str, Any]:
    """Withdraw an order still awaiting payment; its invoice is voided. A
    payment that lands afterwards is refunded automatically."""
    from workeasy360.commercial.payments.models import Invoice, Order

    authz.require(ctx, MANAGE)
    with transaction.atomic():
        invoice, order = _locked(order_id)
        if order is None:
            raise OrderNotFound("No such order.", order_id=str(order_id))
        if order.status != Order.Status.PENDING_PAYMENT:
            raise OrderNotAllowed(
                "Only an order awaiting payment can be cancelled.", reason="not_pending"
            )
        now = timezone.now()
        order.status = Order.Status.CANCELLED
        order.cancelled_at = now
        order.version += 1
        order.save(update_fields=["status", "cancelled_at", "version", "updated_at"])
        if invoice is not None and invoice.status == Invoice.Status.OPEN:
            _void(invoice, reason="The order was cancelled before payment.", now=now)
        _sync_summary(order)
        audit.record(
            ctx,
            "commercial.order.cancelled",
            resource_type="payments.order",
            resource_id=order.id,
            changes={"number": order.number},
        )
    return order_view(order)


def _void(invoice: Any, *, reason: str, now: datetime) -> None:
    invoice.status = invoice.Status.VOID
    invoice.voided_at = now
    invoice.void_reason = reason[:255]
    invoice.version += 1
    invoice.save(
        update_fields=["status", "voided_at", "void_reason", "version", "updated_at"]
    )


def _can_view(ctx: TenantContext) -> None:
    for key in VIEW_ANY:
        if authz.check(ctx, key).allowed:
            return
    authz.require(ctx, VIEW_ANY[0])


def order_view(order: Any) -> dict[str, Any]:
    invoice = getattr(order, "invoice", None) if order.pk else None
    return {
        "order_id": str(order.id),
        "number": order.number,
        "kind": order.kind,
        "status": order.status,
        "description": order.description,
        "product_codes": list(order.product_codes or []),
        "currency": order.currency,
        "billing_interval": order.billing_interval,
        "quantity": order.quantity,
        "recurring_amount_minor": order.recurring_amount_minor,
        "amount_due_minor": order.amount_due_minor,
        "is_trial": order.is_trial,
        "requires_payment": order.status == order.Status.PENDING_PAYMENT,
        "invoice_id": str(invoice.id) if invoice is not None else None,
        "invoice_number": invoice.number if invoice is not None else None,
        "created_at": order.created_at.isoformat(),
        "expires_at": order.expires_at.isoformat() if order.expires_at else None,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "fulfilled_at": order.fulfilled_at.isoformat() if order.fulfilled_at else None,
        "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "failure_reason": order.failure_reason,
    }


def orders(
    ctx: TenantContext, *, limit: int = PAGE_SIZE, cursor: str | None = None
) -> dict[str, Any]:
    """The organization's orders, newest first, keyset-paginated. Two queries
    (orders with their invoices joined) whatever the page size."""
    from django.db.models import Q

    from workeasy360.commercial.payments.models import Order

    _can_view(ctx)
    limit = max(1, min(int(limit or PAGE_SIZE), MAX_PAGE))
    rows = Order.objects.select_related("invoice").order_by("-created_at", "-id")
    after = decode_cursor(cursor)
    if after is not None:
        rows = rows.filter(
            Q(created_at__lt=after[0]) | Q(created_at=after[0], id__lt=after[1])
        )
    page = list(rows[: limit + 1])
    more = len(page) > limit
    page = page[:limit]
    return {
        "results": [order_view(o) for o in page],
        "next_cursor": encode_cursor(page[-1].created_at, page[-1].id)
        if more and page
        else None,
        "has_more": more,
    }


def order_detail(ctx: TenantContext, order_id: UUID) -> dict[str, Any]:
    from workeasy360.commercial.payments.models import Order

    _can_view(ctx)
    order = Order.objects.select_related("invoice").filter(id=order_id).first()
    if order is None:
        raise OrderNotFound("No such order.", order_id=str(order_id))
    return order_view(order)


# --- settlement and fulfilment (no actor) -----------------------------------------


def mark_paid(invoice: Any, *, at: datetime) -> UUID | None:
    """Called by settlement, inside its transaction, when an order's invoice
    has just become paid (the invoice is already locked). Returns the order to
    fulfil after commit, or ``None`` if it was not awaiting payment."""
    from workeasy360.commercial.payments.models import Order

    order = (
        Order.unscoped.select_for_update()
        .filter(id=invoice.order_id, organization_id=invoice.organization_id)
        .first()
    )
    if order is None or order.status != Order.Status.PENDING_PAYMENT:
        return None
    order.status = Order.Status.PAID
    order.paid_at = at
    order.version += 1
    order.save(update_fields=["status", "paid_at", "version", "updated_at"])
    _sync_summary(order)
    return order.id


def fulfil(organization_id: UUID, order_id: UUID) -> str:
    """Add what a paid order bought. Idempotent: anything but a ``paid``
    order is left alone. ``fulfilled`` or ``failed`` (then refunded)."""
    from workeasy360.commercial.payments.models import Order
    from workeasy360.commercial.subscriptions import system
    from workeasy360.commercial.subscriptions.hooks import notify_commercial_change
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    ctx = routing_context(organization_id)
    if ctx is None:
        return "gone"
    refund_attempt: UUID | None = None
    with (
        tenant_context(ctx),
        organization_db_scope(organization_id),
        transaction.atomic(),
    ):
        invoice, order = _locked(order_id)
        if order is None or order.status != Order.Status.PAID:
            return "noop"
        now = timezone.now()
        try:
            with transaction.atomic():
                if order.kind == Order.Kind.ACTIVATION:
                    subscription = system.activate_paid(
                        ctx,
                        subscription_id=order.subscription_id,
                        order_number=order.number,
                    )
                    line = None
                else:
                    subscription, line = system.add_paid_line(
                        ctx,
                        plan_version_id=order.plan_version_id,
                        bundle_version_id=order.bundle_version_id,
                        declined_plan_version_ids=[
                            UUID(d) for d in order.declined_plan_version_ids or []
                        ],
                        quantity=order.quantity,
                        billing_interval=BillingInterval(order.billing_interval),
                        snapshot=order.price_snapshot,
                        order_number=order.number,
                    )
        except CommercialError as exc:
            order.status = Order.Status.FAILED
            order.failure_reason = str(exc.context.get("reason") or "unfulfillable")[:64]
            order.version += 1
            order.save(
                update_fields=["status", "failure_reason", "version", "updated_at"]
            )
            _sync_summary(order)
            audit.record_system(
                organization_id,
                "commercial.order.failed",
                resource_type="payments.order",
                resource_id=order.id,
                changes={"number": order.number, "reason": order.failure_reason},
                reason="A paid order could no longer be fulfilled; its payment is refunded.",
            )
            refund_attempt = _succeeded_attempt(invoice)
            outcome = "failed"
        else:
            _link_invoice(invoice, order, subscription, line)
            order.status = Order.Status.FULFILLED
            order.fulfilled_at = now
            order.subscription_id = subscription.id
            order.subscription_item_id = line.id if line is not None else None
            order.version += 1
            order.save(
                update_fields=[
                    "status",
                    "fulfilled_at",
                    "subscription",
                    "subscription_item_id",
                    "version",
                    "updated_at",
                ]
            )
            _sync_summary(order)
            audit.record_system(
                organization_id,
                "commercial.order.fulfilled",
                resource_type="payments.order",
                resource_id=order.id,
                changes={
                    "number": order.number,
                    "products": order.product_codes,
                    "paid": True,
                },
                reason="The order's payment was confirmed.",
            )
            outcome = "fulfilled"
    if outcome == "fulfilled":
        notify_commercial_change(organization_id)
    elif refund_attempt is not None:
        from workeasy360.commercial.payments.refunds import refund_automatically

        refund_automatically(
            organization_id,
            refund_attempt,
            reason="The order could not be fulfilled after payment.",
        )
    metrics.increment("commercial_orders_fulfilment_total", outcome=outcome)
    logger.info("payments.order.fulfilment", order_id=str(order_id), outcome=outcome)
    return outcome


def _succeeded_attempt(invoice: Any) -> UUID | None:
    from workeasy360.commercial.payments.models import PaymentAttempt

    if invoice is None:
        return None
    return (
        PaymentAttempt.objects.filter(invoice=invoice, status="succeeded")
        .exclude(provider_key="manual")
        .order_by("-completed_at")
        .values_list("id", flat=True)
        .first()
    )


def _link_invoice(invoice: Any, order: Any, subscription: Any, line: Any) -> None:
    """Name the subscription the order's invoice paid for. When the order
    opened the subscription (or converted a trial), its invoice *is* the first
    period's: it takes that period, so the period invoicing finds it invoiced
    and paid and never bills it again."""
    from workeasy360.commercial.payments.models import InvoiceLine

    if invoice is None:
        return
    fields = []
    if invoice.subscription_id is None or order.kind == order.Kind.ACTIVATION:
        invoice.subscription_id = subscription.id
        invoice.period_start = subscription.current_period_start
        invoice.period_end = subscription.current_period_end
        fields += ["subscription", "period_start", "period_end"]
    if fields:
        invoice.version += 1
        invoice.save(update_fields=[*fields, "version", "updated_at"])
    if line is not None:
        InvoiceLine.objects.filter(
            invoice=invoice, subscription_item_id__isnull=True
        ).update(subscription_item_id=line.id)


def process_due(organization_id: UUID, moment: datetime) -> dict[str, int]:
    """The clock's part: expire orders unpaid past their time (voiding their
    invoices) and re-drive any paid order whose fulfilment was lost."""
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import Invoice, Order

    expired = 0
    with organization_scope(
        organization_id, reason="Expire orders unpaid past their time"
    ):
        stale = list(
            Order.unscoped.filter(
                organization_id=organization_id,
                status=Order.Status.PENDING_PAYMENT,
                expires_at__lte=moment,
            ).values_list("id", flat=True)[:200]
        )
        for order_id in stale:
            with transaction.atomic():
                invoice = (
                    Invoice.unscoped.select_for_update()
                    .filter(organization_id=organization_id, order_id=order_id)
                    .first()
                )
                order = (
                    Order.unscoped.select_for_update()
                    .filter(organization_id=organization_id, id=order_id)
                    .first()
                )
                if order is None or order.status != Order.Status.PENDING_PAYMENT:
                    continue
                order.status = Order.Status.EXPIRED
                order.version += 1
                order.save(update_fields=["status", "version", "updated_at"])
                if invoice is not None and invoice.status == Invoice.Status.OPEN:
                    _void(invoice, reason="The order expired before payment.", now=moment)
                _sync_summary(order)
                audit.record_system(
                    organization_id,
                    "commercial.order.expired",
                    resource_type="payments.order",
                    resource_id=order.id,
                    changes={"number": order.number},
                    reason="The order was not paid in time.",
                )
                expired += 1
        # Paid a while ago and still not fulfilled: the after-commit call was
        # lost (a crash, a deploy). A minute's slack keeps the clock from
        # racing the webhook's own fulfilment.
        lost = list(
            Order.unscoped.filter(
                organization_id=organization_id,
                status=Order.Status.PAID,
                paid_at__lte=moment - timedelta(minutes=1),
            ).values_list("id", flat=True)[:50]
        )
    redriven = sum(fulfil(organization_id, order_id) == "fulfilled" for order_id in lost)
    return {"expired_orders": expired, "fulfilled_orders": redriven}


# --- projections -------------------------------------------------------------------


def _sync_summary(order: Any) -> None:
    """Keep the staff projection in step, in the order's own transaction."""
    from workeasy360.commercial.payments.models import OrderSummary

    OrderSummary.objects.update_or_create(
        order_id=order.id,
        defaults={
            "organization_id": order.organization_id,
            "number": order.number,
            "kind": order.kind,
            "status": order.status,
            "description": order.description,
            "product_codes": list(order.product_codes or []),
            "currency": order.currency,
            "amount_due_minor": order.amount_due_minor,
            "created_at": order.created_at or timezone.now(),
            "paid_at": order.paid_at,
        },
    )


def on_invoice_voided(invoice: Any, *, at: datetime) -> None:
    """Staff voided an order's invoice: the order is withdrawn with it.
    Inside the caller's transaction, with the invoice already locked."""
    from workeasy360.commercial.payments.models import Order

    if invoice.order_id is None:
        return
    order = (
        Order.unscoped.select_for_update()
        .filter(id=invoice.order_id, organization_id=invoice.organization_id)
        .first()
    )
    if order is None or order.status != Order.Status.PENDING_PAYMENT:
        return
    order.status = Order.Status.CANCELLED
    order.cancelled_at = at
    order.version += 1
    order.save(update_fields=["status", "cancelled_at", "version", "updated_at"])
    _sync_summary(order)


def staff_orders(
    ctx: TenantContext,
    *,
    status: str | None = None,
    limit: int = PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Every order across customers, newest first (``platform.staff_payment.view``).
    One query for the page, one for the customers' names."""
    from django.db.models import Q

    from workeasy360.commercial.payments.models import Order, OrderSummary
    from workeasy360.platform.organizations.selectors import organization_names

    authz.require(ctx, STAFF_VIEW)
    limit = max(1, min(int(limit or PAGE_SIZE), MAX_PAGE))
    rows = OrderSummary.objects.order_by("-created_at", "-order_id")
    if status:
        if status not in Order.Status.values:
            raise CatalogValidationError("Unknown order status.", field="status")
        rows = rows.filter(status=status)
    after = decode_cursor(cursor)
    if after is not None:
        rows = rows.filter(
            Q(created_at__lt=after[0]) | Q(created_at=after[0], order_id__lt=after[1])
        )
    page = list(rows[: limit + 1])
    more = len(page) > limit
    page = page[:limit]
    names = organization_names({row.organization_id for row in page})
    return {
        "results": [
            {
                "order_id": str(row.order_id),
                "organization_id": str(row.organization_id),
                "organization_name": names.get(row.organization_id, ""),
                "number": row.number,
                "kind": row.kind,
                "status": row.status,
                "description": row.description,
                "product_codes": list(row.product_codes or []),
                "currency": row.currency,
                "amount_due_minor": row.amount_due_minor,
                "created_at": row.created_at.isoformat(),
                "paid_at": row.paid_at.isoformat() if row.paid_at else None,
            }
            for row in page
        ],
        "next_cursor": encode_cursor(page[-1].created_at, page[-1].order_id)
        if more and page
        else None,
        "has_more": more,
    }


def paid_orders_by_product(*, start: Any, end: Any) -> dict[str, Any]:
    """Orders paid in ``[start, end)``: how many, and per product code (a bundle
    order counts once for each of its products, so the per-product figures can
    add up to more than ``orders``). For the Super Admin dashboard, whose
    caller has already authorized. Codes and counts only."""
    from collections import Counter

    from workeasy360.commercial.payments.models import OrderSummary

    per_product: Counter[str] = Counter()
    orders = 0
    rows = OrderSummary.objects.filter(paid_at__gte=start, paid_at__lt=end).values_list(
        "product_codes", flat=True
    )
    for codes in rows.iterator():
        orders += 1
        per_product.update(set(codes or []))
    return {"orders": orders, "by_product": dict(per_product)}


def paid_order_count(*, start: Any, end: Any) -> int:
    """How many orders were paid in ``[start, end)``."""
    from workeasy360.commercial.payments.models import OrderSummary

    return OrderSummary.objects.filter(paid_at__gte=start, paid_at__lt=end).count()


def recent_orders_for_organizations(
    organization_ids: list[UUID], *, limit: int = 20
) -> list[dict[str, Any]]:
    """The latest orders of these organizations, from the staff projection -
    for a staff view that has already authorised the organizations (the user
    directory's detail page). One query."""
    from workeasy360.commercial.payments.models import OrderSummary

    if not organization_ids:
        return []
    return [
        {
            "order_id": str(row.order_id),
            "organization_id": str(row.organization_id),
            "number": row.number,
            "status": row.status,
            "description": row.description,
            "currency": row.currency,
            "amount_due_minor": row.amount_due_minor,
            "created_at": row.created_at.isoformat(),
        }
        for row in OrderSummary.objects.filter(
            organization_id__in=organization_ids
        ).order_by("-created_at")[: max(1, min(limit, 50))]
    ]
