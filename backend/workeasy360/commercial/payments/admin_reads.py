"""The Admin console's reads across customers, from the payments projections.

Every list here reads a control-plane projection (``models.py``), never a tenant
table, and is authorized by its caller or by ``platform.staff_payment.view`` here.

**Definitions, stated once.** Money stays per currency.

- **Sales**: invoices issued in the period, tax included, except void invoices and a
  checkout's invoice nobody has paid yet (an abandoned checkout sold nothing).
- **Collections**: money received in the period, less refunds confirmed in the period.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timezone as dt_timezone
from typing import Any

from django.db.models import Q, Sum
from django.db.models.functions import TruncMonth

from workeasy360.commercial.payments.models import (
    CreditNote,
    InvoiceLineSummary,
    InvoiceSummary,
    OrderSummary,
    PaymentSummary,
    RefundSummary,
)
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.cursor import decode_cursor, encode_cursor
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.organizations.selectors import organization_names

VIEW = "platform.staff_payment.view"
MAX_PAGE = 100

# --- sales and collections ----------------------------------------------------------


def sales_rows(start: Any, end: Any) -> Any:
    """The invoices that count as sales in ``[start, end)``."""
    return (
        InvoiceSummary.objects.filter(issued_at__gte=start, issued_at__lt=end)
        .exclude(status="void")
        .filter(Q(order_id__isnull=True) | Q(status="paid"))
    )


def sales_by_currency(start: Any, end: Any) -> dict[str, int]:
    rows = sales_rows(start, end).values("currency").annotate(total=Sum("total_minor"))
    return {row["currency"]: int(row["total"] or 0) for row in rows}


def _received(start: Any, end: Any) -> Any:
    return PaymentSummary.objects.filter(
        status="succeeded", completed_at__gte=start, completed_at__lt=end
    )


def _refunded(start: Any, end: Any) -> Any:
    return RefundSummary.objects.filter(
        status="succeeded", completed_at__gte=start, completed_at__lt=end
    )


def collections_by_currency(start: Any, end: Any) -> dict[str, int]:
    """Received less refunded, per currency, in ``[start, end)``."""
    totals: Counter[str] = Counter()
    for row in _received(start, end).values("currency").annotate(total=Sum("amount_minor")):
        totals[row["currency"]] += int(row["total"] or 0)
    for row in _refunded(start, end).values("currency").annotate(total=Sum("amount_minor")):
        totals[row["currency"]] -= int(row["total"] or 0)
    return dict(totals)


def _by_month(rows: Any, field: str, amount: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(dict)
    grouped = (
        rows.annotate(month=TruncMonth(field, tzinfo=dt_timezone.utc))
        .values("month", "currency")
        .annotate(total=Sum(amount))
    )
    for row in grouped:
        out[row["currency"]][row["month"].strftime("%Y-%m")] = int(row["total"] or 0)
    return out


def money_by_month(start: Any, end: Any) -> dict[str, dict[str, dict[str, int]]]:
    """``{"sales"|"received"|"refunded": {currency: {"YYYY-MM": minor}}}``."""
    return {
        "sales": _by_month(sales_rows(start, end), "issued_at", "total_minor"),
        "received": _by_month(_received(start, end), "completed_at", "amount_minor"),
        "refunded": _by_month(_refunded(start, end), "completed_at", "amount_minor"),
    }


def revenue_by_product(start: Any, end: Any, currency: str) -> dict[str, int]:
    """What each product sold in the period, in this currency. Tax and set-up lines
    are not a product's revenue here."""
    invoices = sales_rows(start, end).filter(currency=currency).values("invoice_id")
    rows = (
        InvoiceLineSummary.objects.filter(invoice_id__in=invoices, kind="charge")
        .exclude(product_code="")
        .values("product_code")
        .annotate(total=Sum("amount_minor"))
    )
    return {row["product_code"]: int(row["total"] or 0) for row in rows}


# --- what waits for someone ------------------------------------------------------------


def failed_invoice_ids() -> Any:
    """Invoices still open with at least one failed payment. A failure later paid by a
    retry no longer waits for anyone."""
    return (
        PaymentSummary.objects.filter(
            status="failed",
            invoice_id__in=InvoiceSummary.objects.filter(status="open").values("invoice_id"),
        )
        .values_list("invoice_id", flat=True)
        .distinct()
    )


def failed_payment_count() -> int:
    return failed_invoice_ids().count()


def stuck_activation_count() -> int:
    """Orders that were paid but never became subscriptions, or that failed."""
    return OrderSummary.objects.filter(status__in=("paid", "failed")).count()


# --- recent activity --------------------------------------------------------------------


def recent_activity(*, limit: int = 8) -> list[dict[str, Any]]:
    payments = list(
        PaymentSummary.objects.filter(status__in=("succeeded", "failed")).order_by(
            "-created_at"
        )[:limit]
    )
    fulfilled = list(
        OrderSummary.objects.filter(status="fulfilled").order_by("-updated_at")[:limit]
    )
    names = organization_names(
        {p.organization_id for p in payments} | {o.organization_id for o in fulfilled}
    )
    events: list[dict[str, Any]] = []
    for payment in payments:
        who = names.get(payment.organization_id, "A customer")
        verb = "Payment received from" if payment.status == "succeeded" else "Payment failed for"
        events.append(
            {
                "kind": f"payment_{payment.status}",
                "at": (payment.completed_at or payment.created_at).isoformat(),
                "title": f"{verb} {who}",
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
                "href": f"/admin/customers/{payment.organization_id}",
            }
        )
    for order in fulfilled:
        who = names.get(order.organization_id, "A customer")
        events.append(
            {
                "kind": "subscription_activated",
                "at": order.updated_at.isoformat(),
                "title": f"Subscription activated for {who}",
                "detail": ", ".join(order.plan_names or []) or order.description,
                "href": f"/admin/customers/{order.organization_id}",
            }
        )
    return events


# --- search ----------------------------------------------------------------------------


def search_orders(term: str, *, limit: int) -> list[dict[str, str]]:
    rows = OrderSummary.objects.filter(
        Q(number__icontains=term) | Q(description__icontains=term)
    ).order_by("-created_at")[:limit]
    return [
        {"id": str(r.order_id), "number": r.number, "description": r.description} for r in rows
    ]


def search_invoices(term: str, *, limit: int) -> list[dict[str, str]]:
    rows = InvoiceSummary.objects.filter(number__icontains=term).order_by("-issued_at")[:limit]
    return [{"id": str(r.invoice_id), "number": r.number, "status": r.status} for r in rows]


# --- the lists --------------------------------------------------------------------------


def _limit(raw: Any) -> int:
    try:
        return max(1, min(int(raw or 25), MAX_PAGE))
    except (TypeError, ValueError):
        return 25


def _page(rows: Any, *, limit: Any, cursor: Any, time_field: str, id_field: str) -> tuple[list[Any], str | None, bool]:
    """A keyset page, newest first."""
    size = _limit(limit)
    after = decode_cursor(cursor)
    if after is not None:
        rows = rows.filter(
            Q(**{f"{time_field}__lt": after[0]})
            | Q(**{time_field: after[0], f"{id_field}__lt": after[1]})
        )
    page = list(rows.order_by(f"-{time_field}", f"-{id_field}")[: size + 1])
    more = len(page) > size
    page = page[:size]
    token = (
        encode_cursor(getattr(page[-1], time_field), getattr(page[-1], id_field))
        if more and page
        else None
    )
    return page, token, more


def _in_period(rows: Any, field: str, start: Any, end: Any) -> Any:
    if start is not None:
        rows = rows.filter(**{f"{field}__gte": start})
    if end is not None:
        rows = rows.filter(**{f"{field}__lt": end})
    return rows


def _choice(raw: Any, allowed: set[str], field: str) -> str | None:
    if not raw:
        return None
    if raw not in allowed:
        raise CatalogValidationError(f"Unknown {field}.", field=field)
    return str(raw)


def invoice_row(row: Any, names: dict[Any, str]) -> dict[str, Any]:
    return {
        "invoice_id": str(row.invoice_id),
        "organization_id": str(row.organization_id),
        "organization_name": names.get(row.organization_id, ""),
        "order_id": str(row.order_id) if row.order_id else None,
        "subscription_id": str(row.subscription_id) if row.subscription_id else None,
        "number": row.number,
        "status": row.status,
        "currency": row.currency,
        "total_minor": row.total_minor,
        "amount_paid_minor": row.amount_paid_minor,
        "amount_refunded_minor": row.amount_refunded_minor,
        "issued_at": row.issued_at.isoformat(),
        "due_at": row.due_at.isoformat(),
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
        "dunning_stage": row.dunning_stage,
    }


def invoices(
    ctx: TenantContext,
    *,
    status: Any = None,
    search: Any = None,
    limit: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    rows = InvoiceSummary.objects.all()
    chosen = _choice(status, {"open", "paid", "void"}, "status")
    if chosen:
        rows = rows.filter(status=chosen)
    if search:
        rows = rows.filter(number__icontains=str(search).strip()[:40])
    page, token, more = _page(
        rows, limit=limit, cursor=cursor, time_field="issued_at", id_field="invoice_id"
    )
    names = organization_names({r.organization_id for r in page})
    return {
        "results": [invoice_row(r, names) for r in page],
        "next_cursor": token,
        "has_more": more,
    }


def payment_row(row: Any, names: dict[Any, str], numbers: dict[Any, dict[str, Any]]) -> dict[str, Any]:
    invoice = numbers.get(row.invoice_id, {})
    return {
        "attempt_id": str(row.attempt_id),
        "organization_id": str(row.organization_id),
        "organization_name": names.get(row.organization_id, ""),
        "invoice_id": str(row.invoice_id),
        "invoice_number": invoice.get("number", ""),
        "invoice_status": invoice.get("status", ""),
        "invoice_version_hint": None,
        "provider": row.provider_key,
        "status": row.status,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "amount_refunded_minor": row.amount_refunded_minor,
        "failure_code": row.failure_code,
        "created_at": row.created_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _invoice_facts(ids: set[Any]) -> dict[Any, dict[str, Any]]:
    return {
        row["invoice_id"]: row
        for row in InvoiceSummary.objects.filter(invoice_id__in=ids).values(
            "invoice_id", "number", "status"
        )
    }


def payments(
    ctx: TenantContext,
    *,
    status: Any = None,
    failed_open: bool = False,
    limit: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    """Payments across customers. ``failed_open`` keeps only failures nobody has paid
    since, which is what the Failed Payments page works from."""
    authz.require(ctx, VIEW)
    rows = PaymentSummary.objects.all()
    chosen = _choice(
        status,
        {"pending", "awaiting_payment", "succeeded", "failed", "expired", "cancelled"},
        "status",
    )
    if chosen:
        rows = rows.filter(status=chosen)
    if failed_open:
        rows = rows.filter(status="failed", invoice_id__in=failed_invoice_ids())
    page, token, more = _page(
        rows, limit=limit, cursor=cursor, time_field="created_at", id_field="attempt_id"
    )
    names = organization_names({r.organization_id for r in page})
    facts = _invoice_facts({r.invoice_id for r in page})
    return {
        "results": [payment_row(r, names, facts) for r in page],
        "next_cursor": token,
        "has_more": more,
    }


def refunds(
    ctx: TenantContext, *, status: Any = None, limit: Any = None, cursor: Any = None
) -> dict[str, Any]:
    """Refunds across customers, each naming the payment and invoice it answers."""
    authz.require(ctx, VIEW)
    rows = RefundSummary.objects.all()
    chosen = _choice(
        status,
        {"requested", "submitting", "pending", "succeeded", "failed", "needs_review"},
        "status",
    )
    if chosen:
        rows = rows.filter(status=chosen)
    page, token, more = _page(
        rows, limit=limit, cursor=cursor, time_field="created_at", id_field="refund_id"
    )
    names = organization_names({r.organization_id for r in page})
    attempts = {
        row["attempt_id"]: row
        for row in PaymentSummary.objects.filter(
            attempt_id__in={r.attempt_id for r in page}
        ).values("attempt_id", "invoice_id", "provider_key", "amount_minor")
    }
    facts = _invoice_facts({a["invoice_id"] for a in attempts.values()})
    notes = {
        n["refund_id"]: n["number"]
        for n in CreditNote.objects.filter(refund_id__in={r.refund_id for r in page}).values(
            "refund_id", "number"
        )
    }

    def row_json(row: Any) -> dict[str, Any]:
        attempt = attempts.get(row.attempt_id, {})
        invoice = facts.get(attempt.get("invoice_id"), {})
        return {
            "refund_id": str(row.refund_id),
            "organization_id": str(row.organization_id),
            "organization_name": names.get(row.organization_id, ""),
            "attempt_id": str(row.attempt_id),
            "invoice_id": str(attempt["invoice_id"]) if attempt else None,
            "invoice_number": invoice.get("number", ""),
            "provider": attempt.get("provider_key", ""),
            "original_amount_minor": attempt.get("amount_minor"),
            "credit_note": notes.get(row.refund_id, ""),
            "amount_minor": row.amount_minor,
            "currency": row.currency,
            "reason": row.reason,
            "status": row.status,
            "failure_code": row.failure_code,
            "created_at": row.created_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    return {"results": [row_json(r) for r in page], "next_cursor": token, "has_more": more}


def credit_notes(
    ctx: TenantContext, *, search: Any = None, limit: Any = None, cursor: Any = None
) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    rows = CreditNote.objects.all()
    if search:
        term = str(search).strip()[:40]
        rows = rows.filter(Q(number__icontains=term) | Q(invoice_number__icontains=term))
    page, token, more = _page(rows, limit=limit, cursor=cursor, time_field="issued_at", id_field="id")
    names = organization_names({r.organization_id for r in page})
    return {
        "results": [
            {
                "id": str(r.id),
                "number": r.number,
                "organization_id": str(r.organization_id),
                "organization_name": names.get(r.organization_id, ""),
                "invoice_id": str(r.invoice_id),
                "invoice_number": r.invoice_number,
                "refund_id": str(r.refund_id),
                "attempt_id": str(r.attempt_id),
                "amount_minor": r.amount_minor,
                "currency": r.currency,
                "reason": r.reason,
                "issued_at": r.issued_at.isoformat(),
            }
            for r in page
        ],
        "next_cursor": token,
        "has_more": more,
    }
