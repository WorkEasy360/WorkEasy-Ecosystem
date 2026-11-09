"""The Admin console's standard reports (2026-11-09).

Eight reports, one shape: columns, rows, a total, and the same filters (dates, product,
search, page). Every row is a real record read from the module that owns it; nothing is
estimated. Money stays per currency, so no report has a grand total across currencies.

``platform.staff_report.view`` opens them all. Commission and payout figures are earnings
records, kept apart from wallet movements.

**CSV.** The same query, up to ``CSV_LIMIT`` rows, audited because it takes data out of
the platform. A cell a spreadsheet would run as a formula is neutralised first.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

from django.utils import timezone

from workeasy360.commercial.commissions import admin_reads as commission_reads
from workeasy360.commercial.payments import admin_reads as payment_reads
from workeasy360.commercial.subscriptions import admin_reads as subscription_reads
from workeasy360.commercial.wallets import admin as wallet_admin
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import acting_scope

VIEW = "platform.staff_report.view"
CSV_LIMIT = 5000
MAX_PAGE = 100
DEFAULT_DAYS = 30

Column = dict[str, str]


def _column(key: str, label: str, kind: str = "text") -> Column:
    return {"key": key, "label": label, "kind": kind}


REPORTS: dict[str, dict[str, Any]] = {
    "sales": {
        "label": "Sales",
        "description": "Invoices issued in the period, tax included, except void invoices and unpaid checkouts.",
        "columns": [
            _column("number", "Invoice"),
            _column("organization_name", "Customer"),
            _column("issued_at", "Issued", "date"),
            _column("status", "Status", "status"),
            _column("total_minor", "Total", "money"),
            _column("amount_paid_minor", "Paid", "money"),
            _column("currency", "Currency"),
        ],
    },
    "payments": {
        "label": "Payments",
        "description": "Every payment attempt that reached an outcome in the period.",
        "columns": [
            _column("completed_at", "Completed", "date"),
            _column("organization_name", "Customer"),
            _column("invoice_number", "Invoice"),
            _column("provider", "Provider"),
            _column("status", "Status", "status"),
            _column("amount_minor", "Amount", "money"),
            _column("amount_refunded_minor", "Refunded", "money"),
            _column("currency", "Currency"),
        ],
    },
    "renewals": {
        "label": "Subscription Renewals",
        "description": "Running subscriptions whose current period ends in the period.",
        "columns": [
            _column("organization_name", "Customer"),
            _column("products", "Products"),
            _column("status", "Status", "status"),
            _column("billing_interval", "Billing"),
            _column("renewal_policy", "Renewal"),
            _column("current_period_end", "Period ends", "date"),
        ],
    },
    "wallet-movements": {
        "label": "Wallet Movements",
        "description": "Every movement on the three balances. Partner commissions are not here.",
        "columns": [
            _column("created_at", "When", "date"),
            _column("wallet_kind", "Wallet"),
            _column("owner_name", "Owner"),
            _column("type_label", "Type"),
            _column("amount", "Amount", "amount"),
            _column("balance_after", "Balance after", "amount"),
            _column("currency", "Currency"),
            _column("reason", "Reason"),
        ],
    },
    "usage": {
        "label": "Usage by Product",
        "description": "What products reported in the period, by product, meter and outcome.",
        "columns": [
            _column("product_name", "Product"),
            _column("meter", "Meter"),
            _column("outcome", "Outcome", "status"),
            _column("quantity", "Units", "number"),
            _column("amount", "Charged", "amount"),
        ],
    },
    "partner-conversions": {
        "label": "Partner Conversions",
        "description": "Referred visitors who became customers in the period.",
        "columns": [
            _column("partner_name", "Partner"),
            _column("organization_name", "Customer"),
            _column("captured_at", "Referred", "date"),
            _column("registered_at", "Registered", "date"),
            _column("converted_at", "Converted", "date"),
            _column("status", "Status", "status"),
        ],
    },
    "commissions": {
        "label": "Commissions",
        "description": "Commission entries earned or reversed in the period. Earnings records, not a wallet.",
        "columns": [
            _column("created_at", "When", "date"),
            _column("partner_name", "Partner"),
            _column("kind", "Kind", "status"),
            _column("earning", "Earning"),
            _column("amount_minor", "Amount", "money"),
            _column("currency", "Currency"),
            _column("clears_at", "Clears", "date"),
            _column("reason_code", "Reason"),
        ],
    },
    "payouts": {
        "label": "Payouts",
        "description": "Payouts prepared in the period and where each stands.",
        "columns": [
            _column("created_at", "Prepared", "date"),
            _column("partner_name", "Partner"),
            _column("status", "Status", "status"),
            _column("amount_minor", "Amount", "money"),
            _column("currency", "Currency"),
            _column("external_reference", "Reference"),
            _column("paid_at", "Paid", "date"),
        ],
    },
}


def catalog(ctx: TenantContext) -> list[dict[str, Any]]:
    authz.require(ctx, VIEW)
    return [
        {"key": key, "label": value["label"], "description": value["description"]}
        for key, value in REPORTS.items()
    ]


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=dt_timezone.utc)


def _window(raw_from: Any, raw_to: Any) -> tuple[datetime, datetime, date, date]:
    today = timezone.now().date()
    try:
        last = date.fromisoformat(raw_to) if raw_to else today
        first = date.fromisoformat(raw_from) if raw_from else last - timedelta(days=DEFAULT_DAYS - 1)
    except (TypeError, ValueError) as exc:
        raise CatalogValidationError("Use a date like 2026-11-09.", field="from") from exc
    if first > last:
        raise CatalogValidationError("The period ends before it starts.", field="from")
    return _at(first), _at(last + timedelta(days=1)), first, last


def _bounds(raw_limit: Any, raw_offset: Any) -> tuple[int, int]:
    try:
        limit = max(1, min(int(raw_limit or 25), MAX_PAGE))
        offset = max(0, int(raw_offset or 0))
    except (TypeError, ValueError):
        return 25, 0
    return limit, offset


def _runner(kind: str) -> Callable[..., tuple[list[dict[str, Any]], int]]:
    from workeasy360.commercial.controlplane import report_sources

    return {
        "sales": payment_reads.report_sales,
        "payments": payment_reads.report_payments,
        "renewals": subscription_reads.report_renewals,
        "wallet-movements": report_sources.wallet_movements,
        "usage": report_sources.usage,
        "partner-conversions": commission_reads.report_conversions,
        "commissions": commission_reads.report_commissions,
        "payouts": commission_reads.report_payouts,
    }[kind]


def _clean_filters(params: dict[str, Any]) -> dict[str, Any]:
    start, end, first, last = _window(params.get("from"), params.get("to"))
    return {
        "start": start,
        "end": end,
        "first": first,
        "last": last,
        "product": str(params.get("product") or "").strip()[:64] or None,
        "search": " ".join(str(params.get("q") or "").split())[:80] or None,
    }


def run(ctx: TenantContext, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """One page of one report."""
    authz.require(ctx, VIEW)
    report = REPORTS.get(kind)
    if report is None:
        raise CatalogValidationError("Unknown report.", field="kind")
    filters = _clean_filters(params)
    limit, offset = _bounds(params.get("limit"), params.get("offset"))
    rows, total = _runner(kind)(
        start=filters["start"],
        end=filters["end"],
        product=filters["product"],
        search=filters["search"],
        limit=limit,
        offset=offset,
    )
    return {
        "key": kind,
        "label": report["label"],
        "description": report["description"],
        "columns": report["columns"],
        "from": filters["first"].isoformat(),
        "to": filters["last"].isoformat(),
        "results": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _safe_cell(value: Any) -> Any:
    """Neutralise what a spreadsheet would run as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{value}"
    return "" if value is None else value


def _display(value: Any, column: Column) -> Any:
    if value is None:
        return ""
    if column["kind"] in ("money", "amount") and isinstance(value, int):
        # The stored integer, with its unit in the header. Never a converted float.
        return value
    return _safe_cell(value)


def export_csv(ctx: TenantContext, kind: str, params: dict[str, Any]) -> tuple[str, str]:
    """``(filename, body)``. Audited: a report leaving the platform is on the record."""
    authz.require(ctx, VIEW)
    report = REPORTS.get(kind)
    if report is None:
        raise CatalogValidationError("Unknown report.", field="kind")
    filters = _clean_filters(params)
    rows, total = _runner(kind)(
        start=filters["start"],
        end=filters["end"],
        product=filters["product"],
        search=filters["search"],
        limit=CSV_LIMIT,
        offset=0,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    columns = report["columns"]
    writer.writerow(
        [
            f"{c['label']} (minor units)" if c["kind"] == "money" else c["label"]
            for c in columns
        ]
    )
    for row in rows:
        writer.writerow([_display(row.get(c["key"]), c) for c in columns])
    with acting_scope(ctx):
        audit.record(
            ctx,
            "report.exported",
            resource_type="controlplane.report",
            resource_id=None,
            changes={
                "report": kind,
                "from": filters["first"].isoformat(),
                "to": filters["last"].isoformat(),
                "rows": len(rows),
                "truncated": total > CSV_LIMIT,
            },
        )
    return f"{kind}-{filters['first']}-to-{filters['last']}.csv", buffer.getvalue()


_ = wallet_admin
