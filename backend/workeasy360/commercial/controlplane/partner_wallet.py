"""A reseller's Platform Wallet, and the plans it pays for (ADR-063).

The specification of 08 November 2026 (sections 6.3, 7 and 8): a reseller adds
money to its Platform Wallet, then activates, renews and changes its customers'
plans from it at its **approved partner purchase price**. Before this, a partner
wallet could only be topped up offline by staff and nothing ever debited it.

**One ledger.** The wallet is the shared ``wallets.Wallet`` (owner = the partner,
kind = platform, one per currency) and every movement is ``wallets.ledger.post``:
locked, keyed, never overdrawn. There is no second partner balance, and earnings
(commissions) never come through here.

**What it pays.** The partner purchase price is the commissions rule a margin is
accrued under (``channel_pricing.resolve_channel_price`` -> ``partner_cost_minor``),
for the customer's currency, plan, cycle and seats. With no approved margin rule
there is no purchase price, and the wallet is not offered: a price is never
guessed. A line added to a running subscription pays the rest of the period
(``subscriptions.domain.prorate``); a plan's one-time set-up fee is added in full.

**Who is invoiced.** A customer paid for this way has ``partner_wallet``
collection: it is never invoiced, its own members cannot add to or change it
(``subscriptions.services``), and the ordinary order and sell commands refuse it.
Its sales earn the reseller no margin or commission - it bought at cost.

**Renewal.** Each period is paid once, under the key
``partner-renewal:<subscription>:<period end>``: by "Renew now" ahead of time, or
by the subscription clock at the period's end (``collect_renewal``). Whichever
runs second finds the key and charges nothing. If the wallet cannot pay at the
end, the lines end with the paid period.

The browser sends identifiers and the amount it was shown. The server re-derives
the amount and refuses a different one (``price_changed``), so a partner is never
charged more than it confirmed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.billing import services as billing
from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.catalog.domain import compare_plan_versions
from workeasy360.commercial.commissions import channel_pricing
from workeasy360.commercial.partners import access, customers
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.payments import invoicing, orders, partner_fees
from workeasy360.commercial.payments import selectors as payment_selectors
from workeasy360.commercial.subscriptions import selectors as subscription_selectors
from workeasy360.commercial.subscriptions import services as subscription_services
from workeasy360.commercial.subscriptions.domain import default_timing, prorate
from workeasy360.commercial.wallets import api as wallets
from workeasy360.commercial.wallets import ledger
from workeasy360.contracts.enums import (
    BillingInterval,
    ChangeTiming,
    CollectionMode,
    RenewalPolicy,
    SubscriptionStatus,
)
from workeasy360.contracts.errors import (
    CommercialError,
    PartnerRecordNotFound,
    PartnerValidationError,
)
from workeasy360.contracts.values import portals
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import acting_scope, in_scope
from workeasy360.platform.identity import portals as portal_links
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

VIEW: Final = "platform.partner_wallet.view"
MANAGE: Final = "platform.partner_wallet.manage"
SELL: Final = "platform.partner_subscription.sell"
SUBSCRIPTION_MANAGE: Final = "platform.partner_subscription.manage"

#: One online top-up: at least 1.00 and at most 10,00,000.00 of the currency's
#: major unit (minor units here). Larger amounts are an offline transfer staff record.
MIN_TOP_UP_MINOR: Final = 100
MAX_TOP_UP_MINOR: Final = 100_000_000
#: "Renew now" opens this long before the period ends.
RENEW_WINDOW: Final = timedelta(days=30)
WALLET_EVENTS: Final = "partner_wallet_operations_total"


class NoPartnerPrice(CommercialError):
    """No approved partner purchase price exists for what was asked."""

    code = "no_partner_price"
    http_status = 409


class WalletNotUsable(CommercialError):
    """This customer cannot be paid for from the wallet (it pays directly)."""

    code = "wallet_not_usable"
    http_status = 409


# --- reading the wallet ------------------------------------------------------------


def _partner(ctx: TenantContext) -> Any:
    partner = partner_selectors.partner_row(ctx.partner_id) if ctx.partner_id else None
    if partner is None:
        raise PartnerRecordNotFound("No such partner.")
    return partner


def _currency(raw: Any, default: str) -> str:
    value = str(raw or default or "").strip().upper()
    try:
        return CurrencyCode(value).value
    except ValueError:
        raise PartnerValidationError("Unsupported currency.", field="currency") from None


def _may_trade(ctx: TenantContext) -> bool:
    try:
        access.require_transacting(ctx)
    except CommercialError:
        return False
    return True


@in_scope
def wallet(ctx: TenantContext) -> dict[str, Any]:
    """The partner's Platform Wallet: a balance per currency (the payout
    currency is always listed, even before its first top-up), whether Add Money
    is open to this person, the top-ups in flight and the latest movements."""
    authz.require(ctx, VIEW)
    partner = _partner(ctx)
    held = wallets.partner_wallets(partner.id)
    currencies = {row["currency"] for row in held}
    default = partner.payout_currency or ""
    if default and default not in currencies:
        held.append(
            {
                "wallet_id": None,
                "currency": default,
                "balance_minor": 0,
                "low_balance_threshold": None,
                "is_low": False,
            }
        )
    held.sort(key=lambda row: (row["currency"] != default, row["currency"]))
    permissions = authz.effective_permissions(ctx)
    trading = _may_trade(ctx)
    top_ups = partner_fees.for_partner(
        partner.id, purpose=partner_fees.PARTNER_WALLET_TOP_UP, limit=10
    )
    return {
        "currency": default,
        "wallets": held,
        "top_up": {
            "allowed": MANAGE in permissions and trading,
            "online": {
                row["currency"]: partner_fees.online_available(row["currency"])
                for row in held
            },
            "min_minor": MIN_TOP_UP_MINOR,
            "max_minor": MAX_TOP_UP_MINOR,
            "offline_note": (
                "For a bank transfer, raise a support ticket with the transfer's "
                "reference: Workeasy360 records it against your wallet."
            ),
        },
        "pays_for_customers": SELL in permissions and trading,
        "top_ups": top_ups,
        "recent": wallets.partner_entries(partner.id, limit=10)["items"],
    }


def _clamp(raw: Any, default: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, min(value, high))


@in_scope
def transactions(
    ctx: TenantContext,
    *,
    currency: Any = None,
    entry_type: Any = None,
    limit: Any = 25,
    offset: Any = 0,
) -> dict[str, Any]:
    """Every movement of this partner's wallet, newest first, each with what
    caused it: the order and customer for an activation, the subscription for a
    renewal, the top-up payment, the entry a reversal answers."""
    authz.require(ctx, VIEW)
    partner = _partner(ctx)
    page = wallets.partner_entries(
        partner.id,
        currency=str(currency).upper() if currency else None,
        entry_type=str(entry_type) if entry_type else None,
        limit=max(1, _clamp(limit, 25, 100)),
        offset=_clamp(offset, 0, 1_000_000),
    )
    describe_sources(partner.id, page["items"])
    return page


def describe_sources(partner_id: UUID, items: list[dict[str, Any]]) -> None:
    """Name each movement's order and customer, from this partner's own orders
    and customers only."""
    order_ids = {i["source_id"] for i in items if i["source_type"] == "order"}
    names = {
        str(row["organization_id"]): row["name"]
        for row in partner_selectors.customer_names_for_partner(partner_id)
    }
    summaries = (
        payment_selectors.order_summaries_by_id(list(order_ids)) if order_ids else {}
    )
    for item in items:
        item["order"] = None
        item["customer"] = None
        if item["source_type"] == "order":
            summary = summaries.get(item["source_id"])
            if summary is not None:
                item["order"] = {"id": item["source_id"], "number": summary["number"]}
                org = str(summary["organization_id"])
                item["customer"] = {"organization_id": org, "name": names.get(org)}
        elif item["source_type"] == "subscription":
            org = subscription_selectors.organization_of_subscription(item["source_id"])
            if org is not None:
                item["customer"] = {
                    "organization_id": str(org),
                    "name": names.get(str(org)),
                }


# --- adding money ------------------------------------------------------------------


def _minor_units(text: Any, currency: str) -> int:
    """``"5000.00"`` in INR -> 500000: Decimal, never float, at most the
    currency's own decimals. The browser sends what the person typed."""
    from decimal import Decimal, InvalidOperation

    try:
        value = Decimal(str(text or "").strip())
    except InvalidOperation:
        raise PartnerValidationError("Enter the amount to add.", field="amount") from None
    exponent = CurrencyCode(currency).exponent
    if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -exponent:  # type: ignore[operator]
        raise PartnerValidationError(
            f"Enter an amount with at most {exponent} decimals.", field="amount"
        )
    return int(value.scaleb(exponent))


@in_scope
def start_top_up(
    ctx: TenantContext,
    *,
    amount_minor: Any = None,
    currency: Any = None,
    email: str = "",
    amount: Any = None,
) -> dict[str, Any]:
    """Open (or return the one already open) hosted checkout for a top-up. The
    wallet is credited only by the provider's verified callback
    (``top_up_paid``), never by the browser's return. ``amount`` is what the
    person typed ("5000.00"); ``amount_minor`` is for API callers."""
    authz.require(ctx, MANAGE)
    access.require_transacting(ctx)
    partner = _partner(ctx)
    code = _currency(currency, partner.payout_currency)
    if amount is not None and amount_minor is None:
        amount_minor = _minor_units(amount, code)
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise PartnerValidationError("Enter the amount to add.", field="amount_minor")
    if not MIN_TOP_UP_MINOR <= amount_minor <= MAX_TOP_UP_MINOR:
        raise PartnerValidationError(
            "Enter an amount within the limits shown.", field="amount_minor"
        )
    row = ledger.ensure(
        owner_type=wallets.PARTNER,
        owner_id=partner.id,
        kind=wallets.PLATFORM,
        currency=code,
    )
    payment = partner_fees.start(
        purpose=partner_fees.PARTNER_WALLET_TOP_UP,
        subject_id=row.id,
        partner_id=partner.id,
        amount_minor=amount_minor,
        currency=code,
        email=email,
        user_id=ctx.user_id,
        description="Workeasy360 Platform Wallet top-up",
        # Back to the wallet page of the workspace the partner works in.
        return_url=f"{portal_links.origin(portals.PARTNER)}{_wallet_page(partner.id)}",
    )
    audit.record(
        ctx,
        "partner.wallet_top_up_started",
        resource_type="partners.partner",
        resource_id=partner.id,
        changes={
            "payment_id": payment["id"],
            "amount_minor": amount_minor,
            "currency": code,
        },
    )
    metrics.increment(WALLET_EVENTS, operation="top_up_started")
    return payment


def _wallet_page(partner_id: UUID) -> str:
    """The Platform Wallet page of the partner's own workspace."""
    landing = partner_selectors.landing_workspace(partner_id)
    slug = {
        "reseller": "reseller",
        "white_label": "white-label",
        "affiliate": "affiliate",
    }.get(landing or "", "reseller")
    return f"/partner/{slug}/platform-wallet"


def top_up_paid(wallet_id: UUID, payment_id: UUID) -> None:
    """Credit the wallet for a verified top-up payment, once (the key is the
    payment). Registered with ``payments.partner_fees`` at app start."""
    paid = partner_fees.paid_amount(payment_id)
    if paid is None:  # pragma: no cover - only a succeeded payment calls this
        return
    amount, _currency_code = paid
    ledger.post(
        wallet_id,
        amount=amount,
        entry_type=wallets.TOP_UP,
        reason="Online top-up",
        idempotency_key=f"partner-fee:{payment_id}",
        source_type="partner_fee_payment",
        source_id=payment_id,
    )
    metrics.increment(WALLET_EVENTS, operation="top_up_credited")


# --- what the partner pays -----------------------------------------------------------


def _purchase_price(
    partner_id: UUID,
    organization_id: UUID,
    *,
    plan_version_id: UUID,
    interval: str,
    quantity: int,
    currency: str,
    at: datetime,
) -> Any:
    channel = partner_selectors.selling_experience_at(partner_id, at)
    quote = channel_pricing.resolve_channel_price(
        plan_version_id=plan_version_id,
        interval=interval,
        currency=currency,
        channel=channel,
        partner_id=partner_id,
        organization_id=organization_id,
        quantity=quantity,
        at=at,
    )
    if quote is None or quote.partner_cost_minor is None:
        raise NoPartnerPrice(
            "No partner purchase price is approved for this plan in this currency. "
            "Ask Workeasy360 to set one.",
            plan_version_id=str(plan_version_id),
        )
    return quote


def _open_subscription(organization_id: UUID) -> Any | None:
    for sub in subscription_selectors.commercial_state(organization_id):
        if sub.status is not SubscriptionStatus.EXPIRED:
            return sub
    return None


def _payer(organization_id: UUID) -> dict[str, Any]:
    """Whether this customer can be paid for from the wallet, and why not."""
    account = billing.billing_account_or_none(organization_id)
    sub = _open_subscription(organization_id)
    if account is None:
        return {"usable": False, "reason": "no_billing_account"}
    mode = account.collection_mode
    if mode == CollectionMode.PARTNER_WALLET.value:
        return {"usable": True, "reason": None, "adopted": True}
    if mode != CollectionMode.AUTOMATIC.value:
        return {"usable": False, "reason": "agreed_terms"}
    if sub is not None:
        return {"usable": False, "reason": "pays_directly"}
    return {"usable": True, "reason": None, "adopted": False}


_PAYER_WORDS: Final = {
    "no_billing_account": "This customer has no billing account yet.",
    "agreed_terms": "Workeasy360 bills this customer on agreed terms.",
    "pays_directly": (
        "This customer already holds a subscription it pays for itself. Sell it a "
        "plan it pays for (Add Order), or ask support to move it to your wallet."
    ),
}


def _charge_for_quote(
    partner_id: UUID, organization_id: UUID, q: Any, at: datetime
) -> orders.WalletCharge:
    """What the wallet pays for an order's quote: the purchase price for the
    plan, cycle and seats - the rest of the period on a running subscription -
    plus the plan's one-time set-up fee."""
    price = _purchase_price(
        partner_id,
        organization_id,
        plan_version_id=q.target.plan.id,
        interval=q.billing_interval.value,
        quantity=q.quantity,
        currency=q.currency,
        at=at,
    )
    full = int(price.partner_cost_minor)
    running = q.open_subscription
    if running is not None and running.status in (
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.PAST_DUE.value,
        SubscriptionStatus.GRACE.value,
    ):
        start, end = running.current_period_start, running.current_period_end
        due = prorate(full, period_start=start, period_end=end, at=at)
        period = {"start": start.isoformat(), "end": end.isoformat(), "prorated": True}
    else:
        due = full
        period = {"prorated": False}
    setup = int(price.setup_fee_minor or 0)
    return orders.WalletCharge(
        amount_minor=due + setup,
        detail={
            "partner_cost_minor": full,
            "due_now_minor": due,
            "setup_fee_minor": setup,
            "currency": q.currency,
            "billing_interval": q.billing_interval.value,
            "quantity": q.quantity,
            "channel": price.channel,
            "period": period,
        },
    )


def _wallet_line(partner_id: UUID, currency: str, charge: int) -> dict[str, Any]:
    wallet_id = wallets.partner_wallet_id(partner_id, currency)
    balance = 0
    if wallet_id is not None:
        balance = next(
            (
                w["balance_minor"]
                for w in wallets.partner_wallets(partner_id)
                if w["currency"] == currency
            ),
            0,
        )
    return {
        "currency": currency,
        "balance_minor": balance,
        "charge_minor": charge,
        "balance_after_minor": balance - charge,
        "sufficient": balance >= charge,
    }


def _order_request(body: dict[str, Any]) -> dict[str, Any]:
    interval = str(body.get("billing_interval") or BillingInterval.MONTHLY.value)
    try:
        BillingInterval(interval)
    except ValueError:
        raise PartnerValidationError(
            "Choose a billing cycle.", field="billing_interval"
        ) from None
    quantity = body.get("quantity", 1)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise PartnerValidationError("Choose how many seats.", field="quantity")
    try:
        plan = UUID(str(body.get("plan_version_id")))
    except ValueError:
        raise PartnerValidationError("Choose a plan.", field="plan_version_id") from None
    return {
        "plan_version_id": plan,
        "billing_interval": interval,
        "quantity": quantity,
        "with_trial": False,
    }


@in_scope
def activation_quote(
    ctx: TenantContext, organization_id: UUID, body: dict[str, Any]
) -> dict[str, Any]:
    """Steps 1-5 of Plan Activation: the customer, the plan, the approved
    partner purchase price and the wallet deduction it would make - exactly the
    amount ``activate`` then charges. Nothing is written."""
    authz.require(ctx, SELL)
    customer = access.customer_context(ctx, organization_id, write=False)
    request = _order_request(body)
    plan = customers.assert_sellable(ctx, request["plan_version_id"])
    now = timezone.now()
    plan_view = {
        "plan_version_id": str(plan.id),
        "product_code": plan.product_code,
        "plan_code": plan.plan_code,
        "name": plan.plan_name,
    }
    with acting_scope(customer):
        payer = _payer(organization_id)
        payer_view = {**payer, "message": _PAYER_WORDS.get(payer["reason"] or "")}
        if not payer["usable"]:
            # Nothing to price: this customer is not paid for from the wallet.
            return {
                "customer": {"organization_id": str(organization_id)},
                "plan": plan_view,
                "payer": payer_view,
                "charge": None,
                "charge_minor": None,
                "wallet": None,
                "renewal": "auto_renew",
            }
        q = orders.build_quote(
            customer, at=now, seller=customers.seller_for(ctx), **request
        )
        charge = _charge_for_quote(ctx.partner_id, organization_id, q, now)
    return {
        "customer": {"organization_id": str(organization_id)},
        "plan": plan_view,
        "payer": payer_view,
        "charge": charge.detail,
        "charge_minor": charge.amount_minor,
        "wallet": _wallet_line(ctx.partner_id, q.currency, charge.amount_minor),
        "renewal": "auto_renew",
    }


@in_scope
def activate(
    ctx: TenantContext,
    organization_id: UUID,
    body: dict[str, Any],
    *,
    idempotency_key: Any,
) -> dict[str, Any]:
    """Steps 6-8: confirm, record the order and its wallet transaction, start
    the subscription. Every gate again on the server: the permission, KYC and
    active status, this customer's live assignment, the product grant, the
    purchase price, the exact amount, the balance. One transaction."""
    authz.require(ctx, SELL)
    customer = access.customer_context(ctx, organization_id, write=True)
    request = _order_request(body)
    customers.assert_sellable(ctx, request["plan_version_id"])
    expected = body.get("expected_charge_minor")
    key = f"wallet:{customers.scoped_key(ctx, idempotency_key)}"[:128]
    partner_id = ctx.partner_id
    assert partner_id is not None
    with acting_scope(customer), transaction.atomic():
        access.lock_active_link(ctx, organization_id)
        payer = _payer(organization_id)
        if not payer["usable"]:
            raise WalletNotUsable(_PAYER_WORDS[payer["reason"]], reason=payer["reason"])
        account = billing.adopt_partner_wallet(customer)
        row = ledger.ensure(
            owner_type=wallets.PARTNER,
            owner_id=partner_id,
            kind=wallets.PLATFORM,
            currency=account.currency,
        )
        now = timezone.now()
        order = orders.place_paid_by_partner_wallet(
            customer,
            seller=customers.seller_for(ctx),
            idempotency_key=key,
            wallet_id=row.id,
            price=lambda q: _charge_for_quote(partner_id, organization_id, q, now),
            expected_charge_minor=expected,
            **request,
        )
    metrics.increment(WALLET_EVENTS, operation="activation")
    return {"order": order, "wallet": wallet_summary(partner_id, account.currency)}


def wallet_summary(partner_id: UUID, currency: str) -> dict[str, Any]:
    found = next(
        (w for w in wallets.partner_wallets(partner_id) if w["currency"] == currency),
        None,
    )
    return {"currency": currency, "balance_minor": found["balance_minor"] if found else 0}


# --- renewal ---------------------------------------------------------------------------


def renewal_key(subscription_id: UUID, period_end: datetime) -> str:
    return f"partner-renewal:{subscription_id}:{period_end.strftime('%Y%m%dT%H%M%S')}"


def _renewal_charge(
    partner_id: UUID, sub: Any, at: datetime
) -> tuple[int, list[dict[str, Any]]]:
    """What renewing ``sub`` for one period costs the partner: each line that
    renews (its plan's renewal rule is automatic) at the purchase price now."""
    lines = [
        item
        for item in sub.items
        if item.removed_at is None and not item.is_derived and item.plan_version_id
    ]
    rules = catalog.renewal_rules_of(item.plan_code for item in lines)
    total = 0
    detail: list[dict[str, Any]] = []
    for item in lines:
        if (
            rules.get(item.plan_code, RenewalPolicy.AUTO_RENEW.value)
            != RenewalPolicy.AUTO_RENEW.value
        ):
            continue
        price = _purchase_price(
            partner_id,
            sub.organization_id,
            plan_version_id=item.plan_version_id,
            interval=sub.billing_interval.value,
            quantity=item.quantity,
            currency=sub.currency,
            at=at,
        )
        total += int(price.partner_cost_minor)
        detail.append(
            {
                "product_code": item.product_code,
                "plan_code": item.plan_code,
                "quantity": item.quantity,
                "partner_cost_minor": int(price.partner_cost_minor),
            }
        )
    return total, detail


def _wallet_subscription(organization_id: UUID, subscription_id: UUID) -> Any:
    sub = next(
        (
            s
            for s in subscription_selectors.commercial_state(organization_id)
            if s.id == subscription_id
        ),
        None,
    )
    if sub is None:
        raise PartnerRecordNotFound("No such subscription.")
    if sub.collection_mode != CollectionMode.PARTNER_WALLET.value:
        raise WalletNotUsable(
            "This subscription is paid by the customer, not from your wallet.",
            reason="pays_directly",
        )
    return sub


def _renewable(sub: Any, now: datetime) -> str | None:
    """Why ``sub`` cannot be renewed now, or ``None``."""
    if not sub.status.is_live:
        return "not_live"
    if sub.renewal_policy is not RenewalPolicy.AUTO_RENEW or sub.cancel_at is not None:
        return "not_renewing"
    if sub.current_period_end - now > RENEW_WINDOW:
        return "too_early"
    return None


_RENEW_WORDS: Final = {
    "not_live": "This subscription has ended. Activate the plan again instead.",
    "not_renewing": "This subscription is set to end; it does not renew.",
    "too_early": "Renewal opens 30 days before the period ends.",
}


@in_scope
def renewal_quote(
    ctx: TenantContext, organization_id: UUID, subscription_id: UUID
) -> dict[str, Any]:
    authz.require(ctx, SELL)
    customer = access.customer_context(ctx, organization_id, write=False)
    with acting_scope(customer):
        sub = _wallet_subscription(organization_id, subscription_id)
        now = timezone.now()
        paid = wallets.entry_id_for_key(renewal_key(sub.id, sub.current_period_end))
        blocked = None if paid else _renewable(sub, now)
        total, lines = _renewal_charge(ctx.partner_id, sub, now)
    return {
        "subscription_id": str(sub.id),
        "period_end": sub.current_period_end.isoformat(),
        "already_paid": paid is not None,
        "blocked": blocked,
        "message": _RENEW_WORDS.get(blocked or ""),
        "charge_minor": 0 if paid else total,
        "lines": lines,
        "wallet": _wallet_line(ctx.partner_id, sub.currency, 0 if paid else total),
    }


@in_scope
def renew(
    ctx: TenantContext,
    organization_id: UUID,
    subscription_id: UUID,
    *,
    expected_charge_minor: Any,
) -> dict[str, Any]:
    """ "Renew now": pay the next period from the wallet ahead of its start.
    Keyed by the period, so a second click, a retry or the clock at the period's
    end never charges again."""
    authz.require(ctx, SELL)
    customer = access.customer_context(ctx, organization_id, write=True)
    partner_id = ctx.partner_id
    assert partner_id is not None
    with acting_scope(customer), transaction.atomic():
        access.lock_active_link(ctx, organization_id)
        sub = _wallet_subscription(organization_id, subscription_id)
        now = timezone.now()
        key = renewal_key(sub.id, sub.current_period_end)
        existing = wallets.entry_id_for_key(key)
        if existing is None:
            blocked = _renewable(sub, now)
            if blocked is not None:
                raise WalletNotUsable(_RENEW_WORDS[blocked], reason=blocked)
            total, lines = _renewal_charge(partner_id, sub, now)
            if total != expected_charge_minor:
                raise orders.WalletChargeChanged(
                    "The amount has changed since it was shown. Review it and confirm again.",
                    charge_minor=total,
                )
            if total > 0:
                row = ledger.ensure(
                    owner_type=wallets.PARTNER,
                    owner_id=partner_id,
                    kind=wallets.PLATFORM,
                    currency=sub.currency,
                )
                ledger.post(
                    row.id,
                    amount=-total,
                    entry_type=wallets.SUBSCRIPTION,
                    reason=f"Renewal from {sub.current_period_end.date().isoformat()}",
                    idempotency_key=key,
                    actor_user_id=ctx.user_id,
                    source_type="subscription",
                    source_id=sub.id,
                )
            audit.record(
                customer,
                "commercial.subscription.renewal_prepaid",
                resource_type="subscriptions.subscription",
                resource_id=sub.id,
                changes={
                    "period_end": sub.current_period_end.isoformat(),
                    "lines": lines,
                },
            )
    metrics.increment(WALLET_EVENTS, operation="renewal")
    return renewal_quote(ctx, organization_id, subscription_id)


def collect_renewal(subscription: Any, moment: datetime) -> bool:
    """The subscription clock's question at a partner-paid period's end: is the
    next period paid? Already (by "Renew now"), or now from the managing
    partner's wallet. ``False`` - the lines end - when no partner manages the
    customer any more, it has no purchase price, or the wallet is short.
    Registered with ``subscriptions.system`` at app start; runs inside the
    clock's transaction, and a refused debit rolls back only its own savepoint."""
    key = renewal_key(subscription.id, subscription.current_period_end)
    if wallets.entry_id_for_key(key) is not None:
        return True
    link = partner_selectors.live_link_for_organization(subscription.organization_id)
    if link is None:
        return False
    sub = next(
        (
            s
            for s in subscription_selectors.commercial_state(subscription.organization_id)
            if s.id == subscription.id
        ),
        None,
    )
    if sub is None:  # pragma: no cover - the clock holds the row
        return False
    try:
        # The clock has no actor: prices are read through a routing context,
        # as the payments sweep reads them (``invoicing.read_scope``).
        with invoicing.read_scope(subscription.organization_id):
            total, _lines = _renewal_charge(link.partner_id, sub, moment)
        if total <= 0:
            return True
        row = ledger.ensure(
            owner_type=wallets.PARTNER,
            owner_id=link.partner_id,
            kind=wallets.PLATFORM,
            currency=subscription.currency,
        )
        ledger.post(
            row.id,
            amount=-total,
            entry_type=wallets.SUBSCRIPTION,
            reason=f"Renewal from {subscription.current_period_end.date().isoformat()}",
            idempotency_key=key,
            source_type="subscription",
            source_id=subscription.id,
        )
    except (ledger.InsufficientBalance, NoPartnerPrice) as exc:
        logger.info(
            "partner_wallet.renewal_unpaid",
            subscription_id=str(subscription.id),
            reason=type(exc).__name__,
        )
        metrics.increment(WALLET_EVENTS, operation="renewal_unpaid")
        return False
    metrics.increment(WALLET_EVENTS, operation="renewal_collected")
    return True


# --- plan change ---------------------------------------------------------------------


def _line(sub: Any, item_id: UUID) -> Any:
    item = next(
        (
            i
            for i in sub.items
            if i.id == item_id and i.removed_at is None and not i.is_derived
        ),
        None,
    )
    if item is None or item.plan_version_id is None:
        raise PartnerRecordNotFound("No such plan line.")
    return item


def _change_terms(
    partner_id: UUID, sub: Any, item: Any, target: Any, now: datetime
) -> dict[str, Any]:
    source = catalog.require_plan_version(item.plan_version_id)
    direction = compare_plan_versions(
        from_seats=source.included_seats * item.quantity,
        to_seats=target.included_seats * item.quantity,
        from_features=source.feature_map(),
        to_features=target.feature_map(),
    )
    timing = default_timing(direction)
    args = {
        "interval": sub.billing_interval.value,
        "quantity": item.quantity,
        "currency": sub.currency,
        "at": now,
    }
    old = int(
        _purchase_price(
            partner_id, sub.organization_id, plan_version_id=source.id, **args
        ).partner_cost_minor
    )
    new = int(
        _purchase_price(
            partner_id, sub.organization_id, plan_version_id=target.id, **args
        ).partner_cost_minor
    )
    due = 0
    if timing is ChangeTiming.IMMEDIATE and new > old:
        due = prorate(
            new - old,
            period_start=sub.current_period_start,
            period_end=sub.current_period_end,
            at=now,
        )
    return {
        "direction": direction,
        "timing": timing.value,
        "from_cost_minor": old,
        "to_cost_minor": new,
        "charge_minor": due,
    }


@in_scope
def change_quote(
    ctx: TenantContext, organization_id: UUID, body: dict[str, Any]
) -> dict[str, Any]:
    authz.require(ctx, SUBSCRIPTION_MANAGE)
    customer = access.customer_context(ctx, organization_id, write=False)
    item_id, target_id = _change_ids(body)
    target = customers.assert_sellable(ctx, target_id)
    with acting_scope(customer):
        sub = _wallet_subscription(
            organization_id, _subscription_of(organization_id, item_id)
        )
        item = _line(sub, item_id)
        terms = _change_terms(ctx.partner_id, sub, item, target, timezone.now())
    return {
        **terms,
        "wallet": _wallet_line(ctx.partner_id, sub.currency, terms["charge_minor"]),
    }


def _change_ids(body: dict[str, Any]) -> tuple[UUID, UUID]:
    try:
        return UUID(str(body.get("subscription_item_id"))), UUID(
            str(body.get("to_plan_version_id"))
        )
    except ValueError:
        raise PartnerValidationError(
            "Choose the plan to move to.", field="to_plan_version_id"
        ) from None


def _subscription_of(organization_id: UUID, item_id: UUID) -> UUID:
    for sub in subscription_selectors.commercial_state(organization_id):
        if any(i.id == item_id for i in sub.items):
            return sub.id
    raise PartnerRecordNotFound("No such plan line.")


@in_scope
def change_plan(
    ctx: TenantContext,
    organization_id: UUID,
    body: dict[str, Any],
    *,
    idempotency_key: Any,
) -> dict[str, Any]:
    """Move a wallet-paid line to another plan, under the Admin's plan-change
    rule (``subscriptions.change_plan`` applies it): an upgrade takes effect now
    and the wallet pays the difference in purchase price for the rest of the
    period; a downgrade waits for the period end and costs nothing now (the next
    renewal is charged at the lower price). One transaction with the debit."""
    authz.require(ctx, SUBSCRIPTION_MANAGE)
    customer = access.customer_context(ctx, organization_id, write=True)
    item_id, target_id = _change_ids(body)
    target = customers.assert_sellable(ctx, target_id)
    partner_id = ctx.partner_id
    assert partner_id is not None
    key = customers.scoped_key(ctx, idempotency_key)
    with acting_scope(customer), transaction.atomic():
        access.lock_active_link(ctx, organization_id)
        sub = _wallet_subscription(
            organization_id, _subscription_of(organization_id, item_id)
        )
        item = _line(sub, item_id)
        now = timezone.now()
        terms = _change_terms(partner_id, sub, item, target, now)
        if terms["charge_minor"] != body.get("expected_charge_minor"):
            raise orders.WalletChargeChanged(
                "The amount has changed since it was shown. Review it and confirm again.",
                charge_minor=terms["charge_minor"],
            )
        new_item = subscription_services.change_plan(
            customer,
            subscription_item_id=item_id,
            to_plan_version_id=target_id,
            idempotency_key=f"wallet:{key}"[:128],
            timing=ChangeTiming(terms["timing"]),
            expected_version=body.get("expected_version"),
        )
        if terms["charge_minor"] > 0:
            row = ledger.ensure(
                owner_type=wallets.PARTNER,
                owner_id=partner_id,
                kind=wallets.PLATFORM,
                currency=sub.currency,
            )
            ledger.post(
                row.id,
                amount=-terms["charge_minor"],
                entry_type=wallets.SUBSCRIPTION,
                reason=f"Upgrade to {target.plan_code}",
                idempotency_key=f"plan-change:{new_item.id}",
                actor_user_id=ctx.user_id,
                source_type="subscription",
                source_id=sub.id,
            )
    metrics.increment(WALLET_EVENTS, operation="plan_change")
    return {**terms, "subscription_item_id": str(new_item.id)}
