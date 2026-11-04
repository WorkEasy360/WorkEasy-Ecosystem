"""The central commercial rule engine: one answer per channel (ADR-056).

    resolve_channel_price(plan, interval, currency, channel, partner, organization)

answers, for **every** Store level - direct customer, affiliate, reseller,
white-label - and for Super Admin's previews: the list price, the promotional
discount, what the customer pays in this channel, the set-up fee, the partner's
cost and margin *or* the affiliate's commission, the partner's and the
platform's share, the tax treatment and the amount payable now.

**It owns no rule of its own.** Every figure comes from the function that
already decides it for real money, so a screen and a ledger can never disagree:

- prices: ``pricing.resolve_price`` (contract, partner book, regional, default;
  promotional discount via ``pricing.domain.apply_discount``) - the function an
  order is placed with;
- reseller / white-label margin: ``domain.select_margin`` + ``domain.partner_cost``
  - the selection and arithmetic the accrual uses;
- commission: ``domain.select_rule`` + ``domain.rule_amount`` - likewise;
- the partner's allowed price range: ``partners.price_ranges``.

``compose`` is the pure step that turns those inputs into a :class:`ChannelQuote`.
``resolve_channel_price`` feeds it one exact ``resolve_price`` per call (a
preview, a quote); ``channel_prices`` feeds it the bulk list-price reads for a
screen of many plans. Both are informational: an order re-resolves every figure
when it is placed, and a commission is accrued only from a verified payment.

Tax: the platform records each price's tax treatment (exclusive / inclusive)
but computes no tax amount - there is no tax engine. ``tax_minor`` is therefore
always ``None`` and the payable amount is before any tax for exclusive prices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.utils import timezone

from workeasy360.commercial.commissions import domain
from workeasy360.contracts.values.money import CurrencyCode

DIRECT: Final = "direct"
AFFILIATE: Final = "affiliate"
RESELLER: Final = "reseller"
WHITE_LABEL: Final = "white_label"
CHANNELS: Final = (DIRECT, AFFILIATE, RESELLER, WHITE_LABEL)
#: Channels in which a partner sells to its customer (and so may earn a margin).
SELLING: Final = frozenset({RESELLER, WHITE_LABEL})
#: Stands in for "any partner" in a programme-level preview: matches only the
#: programme defaults, never one partner's override.
NO_PARTNER: Final = UUID(int=0)


@dataclass(frozen=True)
class ChannelQuote:
    channel: str
    currency: str
    interval: str
    #: Direct list price before any promotional discount.
    list_minor: int
    discount_bp: int
    #: What a direct customer pays: list less the discount.
    base_minor: int
    #: What the customer pays in this channel (a partner's book, else base).
    customer_minor: int
    price_source: str
    setup_fee_minor: int | None
    tax_behaviour: str
    partner_cost_minor: int | None = None
    margin_minor: int | None = None
    margin_bp: int | None = None
    commission_minor: int | None = None
    earning: str = "none"
    rule: dict[str, Any] | None = None
    price_range: dict[str, Any] | None = None
    tax_minor: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def partner_share_minor(self) -> int:
        return int(self.margin_minor or self.commission_minor or 0)

    @property
    def platform_share_minor(self) -> int:
        return self.customer_minor - self.partner_share_minor

    @property
    def payable_minor(self) -> int:
        """What a first purchase pays now: the price plus any set-up fee."""
        return self.customer_minor + int(self.setup_fee_minor or 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "currency": self.currency,
            "interval": self.interval,
            "list_minor": self.list_minor,
            "discount_bp": self.discount_bp,
            "base_minor": self.base_minor,
            "customer_minor": self.customer_minor,
            "price_source": self.price_source,
            "setup_fee_minor": self.setup_fee_minor,
            "tax_behaviour": self.tax_behaviour,
            "tax_minor": self.tax_minor,
            "partner_cost_minor": self.partner_cost_minor,
            "margin_minor": self.margin_minor,
            "margin_bp": self.margin_bp,
            "commission_minor": self.commission_minor,
            "earning": self.earning,
            "rule": self.rule,
            "partner_share_minor": self.partner_share_minor,
            "platform_share_minor": self.platform_share_minor,
            "payable_minor": self.payable_minor,
            "price_range": self.price_range,
            "notes": list(self.notes),
        }


def _rule_dict(rule: domain.RuleView) -> dict[str, Any]:
    return {
        "kind": "commission",
        "code": rule.code,
        "version": rule.version,
        "rate_bp": rule.rate_bp,
        "fixed_amount_minor": rule.fixed_amount_minor,
        "fixed_currency": rule.fixed_currency,
        "recurring": rule.recurring,
        "recurring_months": rule.recurring_months,
        "partner_specific": rule.partner_id is not None,
        "product_code": rule.product_code,
        "plan_code": rule.plan_code,
    }


def _margin_dict(margin: domain.MarginView) -> dict[str, Any]:
    return {
        "kind": "margin",
        "code": margin.code,
        "version": margin.version,
        "discount_bp": margin.discount_bp,
        "recurring": margin.recurring,
        "partner_specific": margin.partner_id is not None,
        "product_code": margin.product_code,
    }


def compose(
    *,
    channel: str,
    currency: str,
    interval: str,
    product_code: str,
    plan_code: str,
    list_minor: int,
    discount_bp: int,
    base_minor: int,
    customer_minor: int,
    price_source: str,
    setup_fee_minor: int | None,
    tax_behaviour: str,
    partner_id: UUID | None,
    rules: list[domain.RuleView],
    margins: list[domain.MarginView],
    at: datetime,
    price_range: dict[str, Any] | None = None,
) -> ChannelQuote:
    """The pure step: prices in, every share out. No I/O."""
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel {channel!r}.")
    money = CurrencyCode(currency)
    who = partner_id or NO_PARTNER
    common = {
        "channel": channel,
        "currency": currency,
        "interval": interval,
        "list_minor": list_minor,
        "discount_bp": discount_bp,
        "base_minor": base_minor,
        "customer_minor": customer_minor,
        "price_source": price_source,
        "setup_fee_minor": setup_fee_minor,
        "tax_behaviour": tax_behaviour,
        "price_range": price_range,
    }
    if channel == DIRECT:
        return ChannelQuote(**common)

    if channel in SELLING:
        # The accrual's own order: a margin rule first, else commission.
        margin = domain.select_margin(
            margins,
            partner_id=who,
            experience=channel,
            product_code=product_code,
            at=at,
        )
        if margin is not None:
            cost = domain.partner_cost(
                list_minor=base_minor, currency=money, discount_bp=margin.discount_bp
            )
            earned = max(customer_minor - cost, 0)
            return ChannelQuote(
                **common,
                partner_cost_minor=cost,
                margin_minor=earned,
                margin_bp=domain.margin_bp(
                    customer_minor=customer_minor, cost_minor=cost
                ),
                earning="margin",
                rule=_margin_dict(margin),
                notes=() if earned > 0 else ("no_margin_at_this_price",),
            )

    rule = domain.select_rule(
        rules, partner_id=who, product_code=product_code, plan_code=plan_code, at=at
    )
    if rule is None:
        return ChannelQuote(**common, notes=("no_rule",))
    amount = domain.rule_amount(
        rule, basis_minor=customer_minor, currency=money, whole=True
    )
    return ChannelQuote(
        **common,
        commission_minor=amount,
        earning="commission",
        rule=_rule_dict(rule),
        notes=() if channel == AFFILIATE else ("earns_commission_without_margin_rule",),
    )


def _views() -> tuple[list[domain.RuleView], list[domain.MarginView]]:
    from workeasy360.commercial.commissions import selectors

    return selectors.rule_views(), selectors.margin_views()


def resolve_channel_price(
    *,
    plan_version_id: UUID,
    interval: Any,
    currency: str,
    channel: str,
    partner_id: UUID | None = None,
    organization_id: UUID | None = None,
    quantity: int = 1,
    at: datetime | None = None,
) -> ChannelQuote | None:
    """The exact quote for one plan, cycle and channel - ``None`` when the plan
    has no price in ``currency`` (a catalog gap, never a guessed number)."""
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.partners import price_ranges
    from workeasy360.commercial.pricing.resolution import resolve_price
    from workeasy360.contracts.enums import BillingInterval
    from workeasy360.contracts.errors import PriceNotAvailable

    moment = at or timezone.now()
    plan = catalog.plan_version(plan_version_id)
    if plan is None:
        return None
    cycle = BillingInterval(str(interval))
    money = CurrencyCode(currency)
    try:
        listed = resolve_price(
            plan_version_id=plan_version_id,
            organization_id=None,
            currency=money,
            interval=cycle,
            quantity=quantity,
            at=moment,
        )
        sold = resolve_price(
            plan_version_id=plan_version_id,
            organization_id=organization_id,
            currency=money,
            interval=cycle,
            quantity=quantity,
            at=moment,
            partner_id=partner_id if channel in SELLING else None,
        )
    except PriceNotAvailable:
        return None
    rules, margins = _views()
    bounds = (
        price_ranges.range_for(
            experience=channel,
            plan_id=plan.plan_id,
            interval=cycle.value,
            currency=money.value,
        )
        if channel in SELLING
        else None
    )
    return compose(
        channel=channel,
        currency=money.value,
        interval=cycle.value,
        product_code=plan.product_code,
        plan_code=plan.plan_code,
        list_minor=listed.list_minor,
        discount_bp=listed.discount_bp,
        base_minor=listed.total.minor_units,
        customer_minor=sold.total.minor_units,
        price_source=sold.price_source.value,
        setup_fee_minor=sold.setup_fee_minor,
        tax_behaviour=sold.tax_behaviour.value,
        partner_id=partner_id,
        rules=rules,
        margins=margins,
        at=moment,
        price_range=bounds,
    )


def channel_prices(
    plans: list[dict[str, Any]],
    *,
    currency: str,
    channel: str,
    partner_id: UUID | None = None,
    at: datetime | None = None,
) -> dict[tuple[str, str], ChannelQuote]:
    """``{(plan_version_id, interval): quote}`` for a screen of many plans, in a
    fixed number of queries. ``plans`` rows carry ``plan_version_id``,
    ``plan_id`` (optional), ``product_code`` and ``plan_code``.

    Prices come from the bulk list-price reads, which follow ``resolve_price``'s
    book order; a contract price (one organization's) is not considered - this
    is a catalog view. Display only."""
    from workeasy360.commercial.partners import price_ranges
    from workeasy360.commercial.pricing import selectors as price_selectors

    moment = at or timezone.now()
    ids = [UUID(str(p["plan_version_id"])) for p in plans]
    if not ids:
        return {}
    listed = price_selectors.direct_list_prices(
        currency=currency, plan_version_ids=ids, at=moment
    )
    own: dict[tuple[str, str], dict[str, Any]] = {}
    if channel in SELLING and partner_id is not None:
        for row in price_selectors.current_prices(plan_version_ids=ids, at=moment):
            if (
                row["channel"] == "partner"
                and row["partner_id"] == str(partner_id)
                and row["currency"] == currency
                and row["net_amount_minor"] is not None
            ):
                own.setdefault((str(row["plan_version_id"]), row["interval"]), row)
    ranges: dict[tuple[str, str, str], dict[str, Any]] = {}
    if channel in SELLING:
        plan_ids = [UUID(str(p["plan_id"])) for p in plans if p.get("plan_id")]
        for r in price_ranges.ranges(plan_ids=plan_ids):
            if r["experience"] == channel and r["currency"] == currency:
                ranges[(r["plan_id"], r["interval"], r["currency"])] = r
    rules, margins = _views()

    out: dict[tuple[str, str], ChannelQuote] = {}
    for plan in plans:
        pid = str(plan["plan_version_id"])
        for (target, interval), price in listed.items():
            if target != pid or price.get("amount_minor") is None:
                continue
            partner_row = own.get((pid, interval))
            out[(pid, interval)] = compose(
                channel=channel,
                currency=currency,
                interval=interval,
                product_code=plan["product_code"],
                plan_code=plan["plan_code"],
                # The direct list read: ``amount_minor`` after the discount.
                list_minor=int(price["list_amount_minor"]),
                discount_bp=int(price["discount_bp"]),
                base_minor=int(price["amount_minor"]),
                customer_minor=int(
                    partner_row["net_amount_minor"]
                    if partner_row
                    else price["amount_minor"]
                ),
                price_source="partner_book" if partner_row else "default_book",
                setup_fee_minor=(partner_row or price).get("setup_fee_minor"),
                tax_behaviour=str(
                    (partner_row or price).get("tax_behaviour") or "exclusive"
                ),
                partner_id=partner_id,
                rules=rules,
                margins=margins,
                at=moment,
                price_range=ranges.get(
                    (str(plan.get("plan_id") or ""), interval, currency)
                ),
            )
    return out
