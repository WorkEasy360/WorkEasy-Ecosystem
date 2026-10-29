"""The control-tower summary and the control-plane audit trail (Tasks 73-75).

**Counts of concrete operational facts, nothing derived or scored.** There is no
MRR or ARR here, and that is a decision rather than an omission: a global
monthly figure needs currency conversion (this platform has none and refuses to
invent one), monthly normalisation of annual and one-time prices, trial and
suspension treatment, and partner-book versus list-price attribution - and a
number that is wrong in any of those ways is worse than no number. Amounts that
*are* well defined are reported per currency, unconverted.

No customer-health score and no AI: only statuses that are facts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.provisioning.selectors import status_counts
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.cursor import Page
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events.selectors import outbox_backlog
from workeasy360.platform.audit import selectors as audit_selectors
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.organizations.selectors import organization_status_counts

VIEW = "platform.staff_customer.view"
AUDIT_VIEW = "platform.staff_audit.view"
COMMISSION_VIEW = "platform.staff_commission.view"
PARTNER_VIEW = "platform.staff_partner.view"
USER_VIEW = "platform.staff_user.view"
PAYMENT_VIEW = "platform.staff_payment.view"
CATALOG_VIEW = "platform.staff_catalog.view"

#: How far back the dashboard's month picker reaches.
MONTHS_BACK = 24


def summary(ctx: TenantContext, *, month: str | None = None) -> dict[str, Any]:
    """Only the sections the caller's permissions cover are populated.
    ``month`` (``YYYY-MM``, default this month) picks the overview's month."""
    authz.require(ctx, VIEW)
    window = month_window(month, now=timezone.now())
    result: dict[str, Any] = {
        "organizations": organization_status_counts(),
        "subscriptions": status_counts(),
        "mrr": {
            "available": False,
            "reason": (
                "No safe global figure exists: amounts span currencies with no "
                "conversion, and intervals are not normalised across plans."
            ),
        },
        "outbox": outbox_backlog(),
    }
    if authz.check(ctx, PARTNER_VIEW).allowed:
        result["partners"] = partner_selectors.status_counts()
    if authz.check(ctx, COMMISSION_VIEW).allowed:
        result["pending_payouts"] = commission_selectors.pending_payout_summary()
    # Phase 8 extension: accounts and collections, each behind its own grant.
    if authz.check(ctx, USER_VIEW).allowed:
        from workeasy360.platform.identity.selectors import account_statistics

        result["users"] = {
            "window_days": 7,
            **account_statistics(since=timezone.now() - timedelta(days=7)),
        }
    if authz.check(ctx, "platform.staff_payment.view").allowed:
        from workeasy360.commercial.payments.staff import collections_summary

        result["collections"] = collections_summary(days=30)
    result["overview"] = overview(ctx, window)
    return result


def month_window(month: str | None, *, now: datetime) -> dict[str, Any]:
    """The overview's month in UTC days: its start, its end (exclusive) and the
    period it is compared with. A month still running is compared with the
    same number of days at the start of the month before (1-26 Oct against
    1-26 Sep), so a half-finished month never reads as a fall; a past month
    is compared with the whole month before."""
    this_month = date(now.year, now.month, 1)
    if month:
        try:
            year, number = (int(part) for part in month.split("-"))
            first = date(year, number, 1)
        except ValueError as exc:
            raise CatalogValidationError("Unknown month.", field="month") from exc
        if first > this_month or _months_between(first, this_month) > MONTHS_BACK:
            raise CatalogValidationError("Month out of range.", field="month")
    else:
        first = this_month
    following = _add_months(first, 1)
    previous = _add_months(first, -1)
    if first == this_month:
        last_shown = now.date()
        elapsed = (last_shown - first).days + 1
        compared_end = min(previous + timedelta(days=elapsed), first)
    else:
        last_shown = following - timedelta(days=1)
        compared_end = first
    return {
        "month": f"{first.year:04d}-{first.month:02d}",
        "start": first,
        "end": following,
        "last_shown": last_shown,
        "compared_start": previous,
        "compared_end": compared_end,
    }


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=dt_timezone.utc)


def _series(values: dict[str, int], window: dict[str, Any]) -> list[int | None]:
    """One value per day of the month; ``None`` for the days still to come."""
    out: list[int | None] = []
    day = window["start"]
    while day < window["end"]:
        out.append(
            values.get(day.isoformat(), 0) if day <= window["last_shown"] else None
        )
        day += timedelta(days=1)
    return out


def overview(ctx: TenantContext, window: dict[str, Any]) -> dict[str, Any]:
    """The dashboard's month: growth against the period before, a value per day
    for the charts, and orders per product. Counts and per-currency amounts
    only, each section behind the grant of the screen it summarises."""
    start, end = _at(window["start"]), _at(window["end"])
    before_start, before_end = _at(window["compared_start"]), _at(window["compared_end"])
    result: dict[str, Any] = {
        "month": window["month"],
        "first_day": window["start"].isoformat(),
        "last_day": (window["end"] - timedelta(days=1)).isoformat(),
        "compared_with": {
            "first_day": window["compared_start"].isoformat(),
            "last_day": (window["compared_end"] - timedelta(days=1)).isoformat(),
        },
    }
    if authz.check(ctx, USER_VIEW).allowed:
        from workeasy360.platform.identity.selectors import (
            accounts_created,
            signups_by_day,
        )

        daily = signups_by_day(start=start, end=end)
        result["users"] = {
            "total": accounts_created(start=None, end=end),
            "new": sum(daily.values()),
            "previous": accounts_created(start=before_start, end=before_end),
            "daily": _series(daily, window),
        }
    if authz.check(ctx, PARTNER_VIEW).allowed:
        result["partners"] = {
            "total": partner_selectors.created_count(start=None, end=end),
            "new": partner_selectors.created_count(start=start, end=end),
        }
    if authz.check(ctx, CATALOG_VIEW).allowed:
        counts = catalog_selectors.product_counts(start=start, end=end)
        result["products"] = {
            "total": counts["total"],
            "available": counts["available"],
            "new": counts["added"],
        }
    if authz.check(ctx, PAYMENT_VIEW).allowed:
        result.update(_sales(window, start, end, before_start, before_end))
    return result


def _sales(
    window: dict[str, Any],
    start: datetime,
    end: datetime,
    before_start: datetime,
    before_end: datetime,
) -> dict[str, Any]:
    """Paid orders per product and collections per currency for the month."""
    from workeasy360.commercial.payments.orders import (
        paid_order_count,
        paid_orders_by_product,
    )
    from workeasy360.commercial.payments.staff import collections_by_day

    sales = paid_orders_by_product(start=start, end=end)
    names = catalog_selectors.product_names(list(sales["by_product"]))
    earlier = {
        row["currency"]: row["amount_minor"]
        for row in collections_by_day(start=before_start, end=before_end)
    }
    return {
        "orders": {
            "paid": sales["orders"],
            "previous": paid_order_count(start=before_start, end=before_end),
            "by_product": sorted(
                (
                    {"code": code, "name": names.get(code, code), "orders": n}
                    for code, n in sales["by_product"].items()
                ),
                key=lambda row: (-row["orders"], row["name"]),
            ),
        },
        "revenue": [
            {
                "currency": row["currency"],
                "amount_minor": row["amount_minor"],
                "payments": row["payments"],
                "previous_minor": earlier.get(row["currency"], 0),
                "daily": _series(row["daily"], window),
            }
            for row in collections_by_day(start=start, end=end)
        ],
    }


def attention(ctx: TenantContext) -> dict[str, Any]:
    """What is waiting for staff, for the console's notification bell: only the
    items the caller's grants cover, as counts (a zero is left out)."""
    items: list[dict[str, Any]] = []
    if authz.check(ctx, PARTNER_VIEW).allowed:
        from workeasy360.commercial.partners import upgrades

        items.append({"key": "partner_upgrades", "count": upgrades.pending_count()})
        items.append(
            {
                "key": "draft_partners",
                "count": partner_selectors.status_counts().get("draft", 0),
            }
        )
    if authz.check(ctx, COMMISSION_VIEW).allowed:
        payouts = commission_selectors.pending_payout_summary()
        items.append({"key": "payouts", "count": sum(row["count"] for row in payouts)})
    return {"items": [item for item in items if item["count"] > 0]}


@in_scope
def control_plane_audit(
    ctx: TenantContext,
    *,
    action_prefix: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> Page:
    """The platform organization's own audit log: partner, payout, commission,
    catalog and staff-permission actions."""
    authz.require(ctx, AUDIT_VIEW)
    return audit_selectors.audit_page(
        action_prefix=action_prefix, limit=limit, cursor=cursor
    )
