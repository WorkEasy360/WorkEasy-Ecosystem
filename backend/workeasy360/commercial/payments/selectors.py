"""The organization's own invoices and payments, for its billing page.

In the caller's tenant scope (the scoped managers plus RLS): another
organization's invoice and no invoice at all are the same 404.
``platform.billing.view`` - owners and billing managers.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.payments.checkout import attempt_view
from workeasy360.commercial.payments.staff import invoice_json
from workeasy360.contracts.errors import PaymentRecordNotFound
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

VIEW = "platform.billing.view"


def invoices(
    ctx: TenantContext,
    *,
    status: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """One page of the organization's invoices, newest first - keyset on
    ``(issued_at, id)``, so the next page is one indexed query however many
    invoices exist (E8; Phase 9 replaced a fixed cap of 100)."""
    from django.db.models import Q

    from workeasy360.commercial.payments.models import Invoice
    from workeasy360.contracts.values.cursor import decode_cursor, encode_cursor

    authz.require(ctx, VIEW)
    limit = max(1, min(int(limit or 50), 100))
    rows = Invoice.objects.order_by("-issued_at", "-id")
    if status in {s.value for s in Invoice.Status}:
        rows = rows.filter(status=status)
    after = decode_cursor(cursor)
    if after is not None:
        rows = rows.filter(
            Q(issued_at__lt=after[0]) | Q(issued_at=after[0], id__lt=after[1])
        )
    page = list(rows[: limit + 1])
    more = len(page) > limit
    page = page[:limit]
    return {
        "results": [invoice_json(i) for i in page],
        "next_cursor": encode_cursor(page[-1].issued_at, page[-1].id)
        if more and page
        else None,
        "has_more": more,
    }


def invoice(ctx: TenantContext, invoice_id: UUID) -> dict[str, Any]:
    from workeasy360.commercial.payments.models import Invoice

    authz.require(ctx, VIEW)
    row = (
        Invoice.objects.filter(id=invoice_id)
        .prefetch_related("lines", "attempts")
        .first()
    )
    if row is None:
        raise PaymentRecordNotFound("No such invoice.")
    return {
        **invoice_json(row),
        "lines": [
            {
                "description": line.description,
                "product_code": line.product_code,
                "plan_code": line.plan_code,
                "bundle_code": line.bundle_code,
                "quantity": line.quantity,
                "amount_minor": line.amount_minor,
            }
            for line in row.lines.all()
        ],
        "attempts": [
            attempt_view(a)
            for a in sorted(row.attempts.all(), key=lambda a: a.created_at, reverse=True)
        ],
    }


def order_summaries() -> Any:
    """Every order's control-plane summary row, as a queryset a caller may narrow
    (the partner panel narrows it to one partner's customers). Identifiers, codes
    and amounts only - see ``OrderSummary``."""
    from workeasy360.commercial.payments.models import OrderSummary

    return OrderSummary.objects.all()


def paid_item_lines(organization_id: UUID) -> dict[str, list[dict[str, Any]]]:
    """Every paid invoice line naming a subscription line, grouped by that
    subscription line and ordered by billing period - what recurring
    commission reads (ADR-052). Read in the organization's scope; amounts are
    the invoice's own, never re-derived."""
    from workeasy360.commercial.payments.models import Invoice, InvoiceLine
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    # Commission accrual runs outside a request (a listener, the sweep): both
    # isolation layers are bound here, as ``subscriptions.sale_facts`` does.
    with (
        unscoped_access(
            reason="Commission accrual reads paid renewal invoice lines",
            module="commercial.payments.selectors",
        ),
        organization_db_scope(organization_id),
    ):
        assert_tenant_scope(organization_id, what="paid_item_lines")
        rows = list(
            InvoiceLine.unscoped.filter(
                organization_id=organization_id,
                invoice__status=Invoice.Status.PAID,
                subscription_item_id__isnull=False,
            )
            .select_related("invoice")
            .order_by(
                "subscription_item_id", "invoice__period_start", "invoice__paid_at", "id"
            )
        )
    out: dict[str, list[dict[str, Any]]] = {}
    for line in rows:
        out.setdefault(str(line.subscription_item_id), []).append(
            {
                "line_id": str(line.id),
                "invoice_id": str(line.invoice_id),
                "product_code": line.product_code,
                "amount_minor": int(line.amount_minor),
                "currency": line.invoice.currency,
                "paid_at": line.invoice.paid_at,
                "period_start": line.invoice.period_start,
                "refunded": line.invoice.amount_refunded_minor > 0,
                # ADR-054: a partial refund reverses its share of a commission.
                "refunded_minor": int(line.invoice.amount_refunded_minor),
                "invoice_paid_minor": int(line.invoice.amount_paid_minor),
            }
        )
    return out
