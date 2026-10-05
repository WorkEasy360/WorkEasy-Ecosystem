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
