"""A customer's own three balances, for the User Admin Panel (2026-11-11).

Reads only. Every function takes the organization from the verified tenant context
(``ctx.organization_id``), never from a body or a query parameter, and checks the
member's permission itself: ``platform.billing.view`` to read balances, movements and
usage. Spending (a top-up or a credit pack) is an order (``payments.wallet_orders``)
behind ``platform.billing.manage``, paid like any other invoice.

**The three balances are never added together.** Each is its own kind of value: the
Platform Wallet and the Consumption Wallet are money in one currency each, AI Credits
are whole credits. Nothing here sums across kinds or across currencies.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

from django.db.models import F, Sum
from django.utils import timezone

from workeasy360.commercial.wallets import usage
from workeasy360.commercial.wallets.models import (
    CreditPack,
    UsageEvent,
    UsageRate,
    Wallet,
    WalletEntry,
)
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

VIEW = "platform.billing.view"
SPEND = "platform.billing.manage"
MAX_PAGE = 100
RECENT = 5

ORGANIZATION = Wallet.OwnerType.ORGANIZATION
KINDS = tuple(kind.value for kind in Wallet.Kind)
CONSUMPTION_METERS = tuple(
    meter
    for meter, kind in usage.WALLET_FOR_METER.items()
    if kind == Wallet.Kind.CONSUMPTION
)
COUNTED = (UsageEvent.Outcome.CHARGED, UsageEvent.Outcome.INCLUDED)


# --- input ----------------------------------------------------------------------------


def _limit(raw: Any) -> int:
    try:
        return max(1, min(int(raw or 25), MAX_PAGE))
    except (TypeError, ValueError):
        return 25


def _offset(raw: Any) -> int:
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _day(raw: Any, field: str) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CatalogValidationError("Use a date like 2026-11-30.", field=field) from exc


def _range(raw_from: Any, raw_to: Any) -> tuple[datetime | None, datetime | None]:
    """``from`` and ``to`` as whole UTC days, both included."""
    first = _day(raw_from, "from")
    last = _day(raw_to, "to")
    if first and last and first > last:
        raise CatalogValidationError(
            "The start date is after the end date.", field="from"
        )
    start = (
        datetime(first.year, first.month, first.day, tzinfo=dt_timezone.utc)
        if first
        else None
    )
    end = (
        datetime(last.year, last.month, last.day, tzinfo=dt_timezone.utc)
        + timedelta(days=1)
        if last
        else None
    )
    return start, end


# --- shapes ---------------------------------------------------------------------------


def entry_json(row: WalletEntry) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.wallet.kind,
        "currency": row.wallet.currency,
        "entry_type": row.entry_type,
        "entry_type_label": row.get_entry_type_display(),
        "amount": row.amount,
        "balance_after": row.balance_after,
        "reason": row.reason,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "created_at": row.created_at.isoformat(),
    }


def _recent(wallet: Wallet | None) -> list[dict[str, Any]]:
    if wallet is None:
        return []
    rows = (
        WalletEntry.objects.select_related("wallet")
        .filter(wallet=wallet)
        .order_by("-created_at", "-id")[:RECENT]
    )
    return [entry_json(row) for row in rows]


def _month_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=dt_timezone.utc)


def _next_month(now: datetime) -> datetime:
    start = _month_start(now)
    return (start + timedelta(days=32)).replace(day=1)


def _wallets(ctx: TenantContext, kind: str) -> list[Wallet]:
    return list(
        Wallet.objects.filter(
            owner_type=ORGANIZATION, owner_id=ctx.organization_id, kind=kind
        ).order_by("currency")
    )


def _billing_currency(ctx: TenantContext) -> str | None:
    from workeasy360.commercial.billing import services as billing

    account = billing.billing_account_or_none(ctx.organization_id)
    return account.currency if account is not None else None


def _money_wallet(
    ctx: TenantContext, kind: str, currency: str | None
) -> tuple[dict[str, Any], Wallet | None]:
    """One money wallet in the billing currency, and the others it holds (never added in)."""
    rows = _wallets(ctx, kind)
    chosen = next((w for w in rows if w.currency == currency), None)
    if chosen is None and currency is None and rows:
        chosen = rows[0]
    shown_currency = chosen.currency if chosen else (currency or "")
    return (
        {
            "exists": chosen is not None,
            "currency": shown_currency,
            "balance_minor": chosen.balance if chosen else 0,
            "low_balance_threshold": chosen.low_balance_threshold if chosen else None,
            "is_low": chosen.is_low if chosen else False,
            "recent": _recent(chosen),
            "other_currencies": [
                {"currency": w.currency, "balance_minor": w.balance}
                for w in rows
                if w is not chosen
            ],
        },
        chosen,
    )


def _allowance(ctx: TenantContext, meter: str, currency: str | None) -> int | None:
    """What this customer may use free of ``meter`` each month: the live rate's
    allowance plus what its running plans include. ``None`` when no rate is set (the
    meter is then not charged at all) and no plan includes any."""
    rate = usage.live_rate(meter, currency or "")
    plan = usage._plan_allowance(ctx.organization_id, meter)
    if rate is None:
        return plan or None
    return rate.included_units + plan


def _ai_credits(ctx: TenantContext, now: datetime) -> dict[str, Any]:
    rows = _wallets(ctx, Wallet.Kind.AI_CREDITS)
    wallet = rows[0] if rows else None
    start = _month_start(now)
    meter = UsageRate.Meter.AI_TOKENS.value
    included = _allowance(ctx, meter, None)
    tokens = UsageEvent.objects.filter(
        organization_id=ctx.organization_id,
        meter=meter,
        occurred_at__gte=start,
        outcome__in=COUNTED,
    ).aggregate(total=Sum("quantity"))["total"]
    purchased = spent = 0
    if wallet is not None:
        purchased = (
            WalletEntry.objects.filter(
                wallet=wallet, entry_type=WalletEntry.Type.CREDIT_PURCHASE
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
        spent = -(
            WalletEntry.objects.filter(
                wallet=wallet, entry_type=WalletEntry.Type.USAGE, created_at__gte=start
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
    return {
        "exists": wallet is not None,
        "balance": wallet.balance if wallet else 0,
        "low_balance_threshold": wallet.low_balance_threshold if wallet else None,
        "is_low": wallet.is_low if wallet else False,
        # The free allowance is counted in AI tokens, the unit products report.
        "included_per_month": included,
        "used_this_month": int(tokens or 0),
        "credits_spent_this_month": int(spent),
        "purchased_total": int(purchased),
        # Purchased credits do not expire; the free monthly allowance starts again.
        "resets_at": _next_month(now).isoformat() if included else None,
        "recent": _recent(wallet),
    }


def _meter_month(
    ctx: TenantContext, meter: str, currency: str | None, start: datetime
) -> dict[str, Any]:
    events = UsageEvent.objects.filter(
        organization_id=ctx.organization_id, meter=meter, occurred_at__gte=start
    )
    counted = events.filter(outcome__in=COUNTED).aggregate(
        quantity=Sum("quantity"), charged=Sum("charged_units")
    )
    charged = events.filter(outcome=UsageEvent.Outcome.CHARGED)
    if currency:
        charged = charged.filter(rate__currency=currency)
    amount = charged.aggregate(total=Sum("amount"))["total"]
    return {
        "meter": meter,
        "label": str(UsageRate.Meter(meter).label),
        "quantity": int(counted["quantity"] or 0),
        "included_units": _allowance(ctx, meter, currency),
        "charged_units": int(counted["charged"] or 0),
        "amount_minor": int(amount or 0),
        "failed": events.filter(outcome=UsageEvent.Outcome.FAILED).count(),
    }


def _packs(currency: str | None) -> list[dict[str, Any]]:
    rows = CreditPack.objects.filter(status=CreditPack.Status.ACTIVE)
    if currency:
        rows = rows.filter(currency=currency)
    return [
        {
            "code": p.code,
            "name": p.name,
            "credits": p.credits,
            "price_minor": p.price_minor,
            "currency": p.currency,
        }
        for p in rows.order_by("price_minor", "code")
    ]


# --- reads ----------------------------------------------------------------------------


def overview(ctx: TenantContext) -> dict[str, Any]:
    """The three balances, each on its own, with what the month has used."""
    authz.require(ctx, VIEW)
    now = timezone.now()
    currency = _billing_currency(ctx)
    platform, _ = _money_wallet(ctx, Wallet.Kind.PLATFORM, currency)
    consumption, _ = _money_wallet(ctx, Wallet.Kind.CONSUMPTION, currency)
    start = _month_start(now)
    consumption["this_month"] = [
        _meter_month(ctx, meter, consumption["currency"] or currency, start)
        for meter in CONSUMPTION_METERS
    ]
    return {
        "currency": currency,
        "can_spend": bool(authz.check(ctx, SPEND)),
        "platform": platform,
        "ai_credits": _ai_credits(ctx, now),
        "consumption": consumption,
        "credit_packs": _packs(currency),
        "generated_at": now.isoformat(),
    }


def transactions(
    ctx: TenantContext,
    *,
    kind: Any = None,
    entry_type: Any = None,
    date_from: Any = None,
    date_to: Any = None,
    limit: Any = None,
    offset: Any = None,
) -> dict[str, Any]:
    """This organization's movements, newest first, a page at a time."""
    authz.require(ctx, VIEW)
    rows = WalletEntry.objects.select_related("wallet").filter(
        wallet__owner_type=ORGANIZATION, wallet__owner_id=ctx.organization_id
    )
    if kind:
        if kind not in KINDS:
            raise CatalogValidationError("Choose a wallet.", field="kind")
        rows = rows.filter(wallet__kind=kind)
    if entry_type:
        if entry_type not in WalletEntry.Type.values:
            raise CatalogValidationError("Choose a transaction type.", field="type")
        rows = rows.filter(entry_type=entry_type)
    start, end = _range(date_from, date_to)
    if start:
        rows = rows.filter(created_at__gte=start)
    if end:
        rows = rows.filter(created_at__lt=end)
    size, skip = _limit(limit), _offset(offset)
    total = rows.count()
    page = rows.order_by("-created_at", "-id")[skip : skip + size]
    return {
        "items": [entry_json(row) for row in page],
        "total": total,
        "limit": size,
        "offset": skip,
        "types": [
            {"value": v, "label": str(label)} for v, label in WalletEntry.Type.choices
        ],
    }


def usage_events(
    ctx: TenantContext,
    *,
    meter: Any = None,
    product: Any = None,
    date_from: Any = None,
    date_to: Any = None,
    limit: Any = None,
    offset: Any = None,
) -> dict[str, Any]:
    """What this organization's products reported, and what each event cost."""
    authz.require(ctx, VIEW)
    mine = UsageEvent.objects.filter(organization_id=ctx.organization_id)
    rows = mine
    if meter:
        if meter not in UsageRate.Meter.values:
            raise CatalogValidationError("Choose a meter.", field="meter")
        rows = rows.filter(meter=meter)
    if product:
        rows = rows.filter(product_code=str(product)[:64])
    start, end = _range(date_from, date_to)
    if start:
        rows = rows.filter(occurred_at__gte=start)
    if end:
        rows = rows.filter(occurred_at__lt=end)
    size, skip = _limit(limit), _offset(offset)
    total = rows.count()
    page = rows.select_related("rate").order_by("-occurred_at", "-id")[skip : skip + size]
    products = sorted(
        set(mine.exclude(product_code="").values_list("product_code", flat=True)[:500])
    )
    return {
        "items": [
            {
                "id": str(e.id),
                "product_code": e.product_code,
                "meter": e.meter,
                "meter_label": e.get_meter_display(),
                "quantity": e.quantity,
                "outcome": e.outcome,
                "outcome_label": e.get_outcome_display(),
                "charged_units": e.charged_units,
                "amount": e.amount,
                # Credits for AI tokens; the rate's currency for everything else.
                "currency": e.rate.currency if e.rate_id else "",
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in page
        ],
        "total": total,
        "limit": size,
        "offset": skip,
        "products": products,
        "meters": [
            {"value": v, "label": str(label)} for v, label in UsageRate.Meter.choices
        ],
    }


def low_balances(ctx: TenantContext) -> list[dict[str, Any]]:
    """The organization's wallets under the threshold an Admin set (none without one).
    For the bell; the caller has checked ``platform.billing.view``."""
    rows = Wallet.objects.filter(
        owner_type=ORGANIZATION,
        owner_id=ctx.organization_id,
        low_balance_threshold__isnull=False,
    ).filter(balance__lt=F("low_balance_threshold"))
    return [
        {
            "kind": w.kind,
            "label": w.get_kind_display(),
            "currency": w.currency,
            "balance": w.balance,
            "threshold": w.low_balance_threshold,
            "updated_at": w.updated_at.isoformat(),
        }
        for w in rows.order_by("kind", "currency")
    ]
