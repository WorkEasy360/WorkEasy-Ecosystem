"""The Admin dashboard (2026-11-09): what the reference design shows, from real rows.

**Definitions, stated once.** Money stays per currency, never converted or added
across currencies; the page asks for one currency at a time.

- **Sales**: the value of invoices issued in the period, tax included, except void
  invoices and a checkout's invoice nobody has paid yet (an abandoned checkout
  sold nothing). What was billed, whether or not it has been collected.
- **Collections**: money received in the period, less refunds confirmed in the
  period. What actually arrived, whenever it was billed.
- **Customers**: live organizations, the platform's own excluded.
- **Active subscriptions**: running and billable: active, past due or in grace.

A figure that is a running total (customers, partners, subscriptions) is compared
with its value when the period began. A figure earned over the period (sales,
collections) is compared with the equally long period before it. Change is ``None``
when there was nothing to compare with, never an invented percentage.

Every section is present only for a caller holding the grant of the screen it
summarises; the page draws what it receives.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.payments import admin_reads as payment_reads
from workeasy360.commercial.subscriptions import admin_reads as subscription_reads
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

CUSTOMER_VIEW = "platform.staff_customer.view"
PARTNER_VIEW = "platform.staff_partner.view"
PAYMENT_VIEW = "platform.staff_payment.view"
KYC_VIEW = "platform.staff_kyc.view"
WALLET_VIEW = "platform.staff_wallet.view"
AUDIT_VIEW = "platform.staff_audit.view"

#: The longest period the date filter accepts, and the most months a chart draws.
MAX_PERIOD_DAYS = 366
MAX_TREND_MONTHS = 24


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=dt_timezone.utc)


def _parse_day(raw: str | None, field: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise CatalogValidationError("Use a date like 2026-11-09.", field=field) from exc


def period(
    raw_from: str | None, raw_to: str | None, *, days: Any = None
) -> dict[str, Any]:
    """The period asked for and the equally long one before it. Whole UTC days;
    ``to`` is included. Defaults to the last 30 days, ending today."""
    today = timezone.now().date()
    last = _parse_day(raw_to, "to") or today
    first = _parse_day(raw_from, "from")
    if first is None:
        try:
            count = int(days) if days else 30
        except (TypeError, ValueError) as exc:
            raise CatalogValidationError("Unknown period.", field="days") from exc
        if not 1 <= count <= MAX_PERIOD_DAYS:
            raise CatalogValidationError("Unknown period.", field="days")
        first = last - timedelta(days=count - 1)
    if first > last:
        raise CatalogValidationError("The period ends before it starts.", field="from")
    length = (last - first).days + 1
    if length > MAX_PERIOD_DAYS:
        raise CatalogValidationError(
            f"Choose a period of at most {MAX_PERIOD_DAYS} days.", field="from"
        )
    if last > today:
        raise CatalogValidationError("The period ends in the future.", field="to")
    return {
        "from": first,
        "to": last,
        "start": _at(first),
        "end": _at(last + timedelta(days=1)),
        "compared_start": _at(first - timedelta(days=length)),
        "compared_end": _at(first),
        "days": length,
    }


def optional_window(
    raw_from: str | None, raw_to: str | None
) -> tuple[datetime | None, datetime | None]:
    """Whole UTC days for a list's date filter; either end may be open."""
    first = _parse_day(raw_from, "from")
    last = _parse_day(raw_to, "to")
    return (
        _at(first) if first else None,
        _at(last + timedelta(days=1)) if last else None,
    )


def change_percent(now: int, before: int) -> float | None:
    """Change against ``before``, one decimal; ``None`` when there is no base."""
    if before <= 0:
        return None
    return round((now - before) * 100 / before, 1)


# --- the figures, each from one source --------------------------------------------------


def _customers_before(end: datetime) -> int:
    from workeasy360.platform.organizations.selectors import customers_created_before

    return customers_created_before(end)


def _default_currency(seen: Iterable[str]) -> str:
    from workeasy360.platform.administration.site import public_site

    preferred = str(public_site()["catalog_currency"])
    known = sorted(seen)
    if preferred in known or not known:
        return preferred
    return known[0]


def _running_total(now: int, at_start: int) -> dict[str, Any]:
    return {
        "total": now,
        "at_start": at_start,
        "added": now - at_start,
        "change_percent": change_percent(now, at_start),
    }


def _amount(now: int, before: int) -> dict[str, Any]:
    return {
        "amount_minor": now,
        "previous_minor": before,
        "change_percent": change_percent(now, before),
    }


def overview(
    ctx: TenantContext,
    *,
    raw_from: str | None = None,
    raw_to: str | None = None,
    days: Any = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Cards, wallets, pending actions, recent activity and top products."""
    authz.require(ctx, CUSTOMER_VIEW)
    window = period(raw_from, raw_to, days=days)
    result: dict[str, Any] = {
        "period": {
            "from": window["from"].isoformat(),
            "to": window["to"].isoformat(),
            "days": window["days"],
            "compared_from": window["compared_start"].date().isoformat(),
            "compared_to": (window["compared_end"] - timedelta(days=1))
            .date()
            .isoformat(),
        },
        "definitions": {
            "sales": "Invoices issued in the period, tax included, except void "
            "invoices and unpaid checkouts.",
            "collections": "Money received in the period, less refunds confirmed "
            "in the period.",
        },
    }
    cards: dict[str, Any] = {
        "customers": _running_total(
            _customers_before(window["end"]), _customers_before(window["start"])
        ),
        "active_subscriptions": _running_total(
            subscription_reads.running_at(window["end"]),
            subscription_reads.running_at(window["start"]),
        ),
    }
    if authz.check(ctx, PARTNER_VIEW).allowed:
        cards["partners"] = _running_total(
            partner_selectors.active_partner_count(window["end"]),
            partner_selectors.active_partner_count(window["start"]),
        )
    if authz.check(ctx, PAYMENT_VIEW).allowed:
        sales = payment_reads.sales_by_currency(window["start"], window["end"])
        sales_before = payment_reads.sales_by_currency(
            window["compared_start"], window["compared_end"]
        )
        money = payment_reads.collections_by_currency(window["start"], window["end"])
        money_before = payment_reads.collections_by_currency(
            window["compared_start"], window["compared_end"]
        )
        seen = {*sales, *sales_before, *money, *money_before}
        chosen = _chosen_currency(currency, seen)
        result["currency"] = chosen
        result["currencies"] = sorted({*seen, chosen})
        cards["sales"] = _amount(sales.get(chosen, 0), sales_before.get(chosen, 0))
        cards["collections"] = _amount(money.get(chosen, 0), money_before.get(chosen, 0))
        result["top_products"] = top_products(window, chosen)
    result["cards"] = cards
    if authz.check(ctx, WALLET_VIEW).allowed:
        result["wallets"] = wallet_summary(result.get("currency") or currency)
    result["pending"] = pending_actions(ctx)
    result["activity"] = recent_activity(ctx)
    return result


def _chosen_currency(raw: str | None, seen: Iterable[str]) -> str:
    if raw:
        cleaned = raw.strip().upper()
        if len(cleaned) != 3 or not cleaned.isalpha():
            raise CatalogValidationError("Unknown currency.", field="currency")
        return cleaned
    return _default_currency(seen)


def wallet_summary(currency: str | None) -> dict[str, Any]:
    """The three balances, each as its ledger's wallets add up. Commissions are
    earnings records and are not here."""
    from workeasy360.commercial.wallets.selectors import platform_totals

    return platform_totals(currency)


# --- trends --------------------------------------------------------------------------


def _month_starts(months: int, *, today: date) -> list[date]:
    index = today.year * 12 + today.month - 1
    return [
        date((index - i) // 12, (index - i) % 12 + 1, 1)
        for i in range(months - 1, -1, -1)
    ]


def _next_month(first: date) -> date:
    index = first.year * 12 + first.month
    return date(index // 12, index % 12 + 1, 1)


def _clean_months(raw: Any) -> int:
    try:
        months = int(raw) if raw else 6
    except (TypeError, ValueError) as exc:
        raise CatalogValidationError("Unknown range.", field="months") from exc
    if not 1 <= months <= MAX_TREND_MONTHS:
        raise CatalogValidationError("Unknown range.", field="months")
    return months


def growth_trend(ctx: TenantContext, *, months: Any = None) -> dict[str, Any]:
    """Customers, partners and running subscriptions at the end of each month (or
    today, for the month still running)."""
    authz.require(ctx, CUSTOMER_VIEW)
    count = _clean_months(months)
    now = timezone.now()
    starts = _month_starts(count, today=now.date())
    ends = [min(_at(_next_month(first)), now) for first in starts]
    result: dict[str, Any] = {
        "months": [f"{first.year:04d}-{first.month:02d}" for first in starts],
        "customers": [_customers_before(end) for end in ends],
        "subscriptions": [subscription_reads.running_at(end) for end in ends],
    }
    if authz.check(ctx, PARTNER_VIEW).allowed:
        result["partners"] = [partner_selectors.active_partner_count(end) for end in ends]
    return result


def money_trend(
    ctx: TenantContext, *, months: Any = None, currency: str | None = None
) -> dict[str, Any]:
    """Sales and collections per month, in one currency."""
    authz.require(ctx, PAYMENT_VIEW)
    count = _clean_months(months)
    now = timezone.now()
    starts = _month_starts(count, today=now.date())
    keys = [f"{first.year:04d}-{first.month:02d}" for first in starts]
    money = payment_reads.money_by_month(_at(starts[0]), _at(_next_month(starts[-1])))
    sales, received, refunded = money["sales"], money["received"], money["refunded"]
    seen = {*sales, *received, *refunded}
    chosen = _chosen_currency(currency, seen)
    return {
        "months": keys,
        "currency": chosen,
        "currencies": sorted({*seen, chosen}),
        "sales": [sales.get(chosen, {}).get(key, 0) for key in keys],
        "collections": [
            received.get(chosen, {}).get(key, 0) - refunded.get(chosen, {}).get(key, 0)
            for key in keys
        ],
    }


# --- pending actions -------------------------------------------------------------------


def pending_actions(ctx: TenantContext) -> list[dict[str, Any]]:
    """Each item only for someone who can act on it, always with its real count."""
    items: list[dict[str, Any]] = []
    if authz.check(ctx, KYC_VIEW).allowed:
        from workeasy360.commercial.partners.kyc import awaiting_review_count

        items.append(
            {
                "key": "kyc_reviews",
                "label": "Partner KYC Reviews",
                "hint": "Partner applications awaiting review",
                "count": awaiting_review_count(),
                "action": "Review",
                "href": "/admin/partners/applications",
            }
        )
    if authz.check(ctx, PAYMENT_VIEW).allowed:
        items.append(
            {
                "key": "failed_payments",
                "label": "Failed Payments / Activations",
                "hint": "Payments failed or activations pending",
                "count": payment_reads.failed_payment_count()
                + payment_reads.stuck_activation_count()
                + payment_reads.refunds_waiting_count(),
                "action": "Resolve",
                "href": "/admin/payments/failed",
            }
        )
    if authz.check(ctx, WALLET_VIEW).allowed:
        from workeasy360.commercial.wallets.selectors import low_balance_count

        items.append(
            {
                "key": "low_balance",
                "label": "Low-Balance Alerts",
                "hint": "Customers and partners with a low wallet balance",
                "count": low_balance_count(),
                "action": "Review",
                "href": "/admin/wallets?low=1",
            }
        )
    return items


# --- recent activity -------------------------------------------------------------------


def recent_activity(ctx: TenantContext, *, limit: int = 8) -> list[dict[str, Any]]:
    """The latest events from the records themselves, newest first. Each source
    behind the grant of the screen it comes from."""
    from workeasy360.platform.organizations.selectors import recently_created_customers

    events: list[dict[str, Any]] = []
    for row in recently_created_customers(limit=limit):
        events.append(
            {
                "kind": "customer_registered",
                "at": row["created_at"],
                "title": f"New customer: {row['name']}",
                "href": f"/admin/customers/{row['id']}",
            }
        )
    if authz.check(ctx, KYC_VIEW).allowed:
        from workeasy360.commercial.partners.kyc import recent_decisions

        for row in recent_decisions(limit=limit):
            events.append(
                {
                    "kind": f"partner_{row['decision']}",
                    "at": row["decided_at"],
                    "title": f"Partner {row['partner_name']}: {row['words']}",
                    "href": f"/admin/partners/applications/{row['partner_id']}",
                }
            )
    if authz.check(ctx, PAYMENT_VIEW).allowed:
        events.extend(payment_reads.recent_activity(limit=limit))
    events.sort(key=lambda event: event["at"], reverse=True)
    return events[:limit]


# --- top products ------------------------------------------------------------------------


def top_products(
    window: dict[str, Any], currency: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Products by customers on a running subscription now, with what they sold in the
    period in this currency."""
    counts = subscription_reads.running_by_product()
    revenue = payment_reads.revenue_by_product(window["start"], window["end"], currency)
    codes = sorted(
        {*counts, *revenue},
        key=lambda code: (-counts.get(code, 0), -revenue.get(code, 0), code),
    )
    names = catalog_selectors.product_names(codes)
    return [
        {
            "code": code,
            "name": names.get(code, code),
            "active_subscriptions": counts.get(code, 0),
            "revenue_minor": revenue.get(code, 0),
            "currency": currency,
        }
        for code in codes[:limit]
    ]
