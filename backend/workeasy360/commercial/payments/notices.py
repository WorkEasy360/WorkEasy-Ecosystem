"""Payment emails: invoice issued, payment received, payment failed.

Registered with the platform's notification templates (so the Super Admin's
Communication page can reword them) and queued through ``notify`` - never sent
inline. Addressed to the billing account's email, or the owner when it has none.

Best effort, deliberately: a notification that cannot be queued (the
organization is read-only, the owner has no address) is logged and skipped -
it must never fail the invoice or the payment it describes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import transaction

from workeasy360.platform.identity import portals as portal_links
from workeasy360.platform.notifications.templates import Template, register
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

INVOICE_ISSUED = register(
    Template(
        key="commercial.invoice.issued",
        subject="Invoice {invoice_number} for {organization_name}",
        body_text=(
            "A new invoice is ready for {organization_name}.\n\n"
            "Invoice: {invoice_number}\nAmount due: {amount}\nDue: {due_on}\n\n"
            "View and pay it here: {billing_url}"
        ),
        required=(
            "organization_name",
            "invoice_number",
            "amount",
            "due_on",
            "billing_url",
        ),
        description="Sent when an invoice is issued for a subscription period.",
        samples=(
            ("organization_name", "Acme Ltd"),
            ("invoice_number", "INV-000123"),
            ("amount", "INR 4,999.00"),
            ("due_on", "1 October 2026"),
            ("billing_url", "https://app.example.com/o/acme/billing"),
        ),
    )
)

PAYMENT_RECEIVED = register(
    Template(
        key="commercial.payment.received",
        subject="Payment received for {invoice_number}",
        body_text=(
            "Thank you. We received {amount} for invoice {invoice_number} "
            "({organization_name})."
        ),
        required=("organization_name", "invoice_number", "amount"),
        description="Sent when a payment settles an invoice.",
        samples=(
            ("organization_name", "Acme Ltd"),
            ("invoice_number", "INV-000123"),
            ("amount", "INR 4,999.00"),
        ),
    )
)

PAYMENT_FAILED = register(
    Template(
        key="commercial.payment.failed",
        subject="Payment for {invoice_number} did not go through",
        body_text=(
            "A payment for invoice {invoice_number} ({organization_name}) did "
            "not complete. Nothing was charged. You can try again here: "
            "{billing_url}"
        ),
        required=("organization_name", "invoice_number", "billing_url"),
        description="Sent when a payment attempt fails.",
        samples=(
            ("organization_name", "Acme Ltd"),
            ("invoice_number", "INV-000123"),
            ("billing_url", "https://app.example.com/o/acme/billing"),
        ),
    )
)


def format_amount(minor: int, currency: str) -> str:
    from workeasy360.contracts.values.money import CurrencyCode

    try:
        exponent = CurrencyCode(currency).exponent
    except ValueError:
        exponent = 2
    major = Decimal(minor).scaleb(-exponent)
    return f"{currency} {major:,.{exponent}f}"


def _send(
    organization_id: UUID, template: str, context: dict[str, Any], key: str
) -> None:
    from workeasy360.commercial.payments.invoicing import billing_account
    from workeasy360.contracts.ports.notifications import NotificationRequest
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.notifications.services import notify
    from workeasy360.platform.organizations.selectors import (
        organization_summary,
        owner_email,
    )
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    ctx = routing_context(organization_id)
    if ctx is None or not ctx.is_write_allowed:
        return
    account = billing_account(organization_id)
    try:
        with (
            transaction.atomic(),
            tenant_context(ctx),
            organization_db_scope(organization_id),
        ):
            summary = organization_summary(ctx) or {}
            recipient = (
                account.billing_email if account is not None else ""
            ) or owner_email(ctx)
            if not recipient:
                return
            notify(
                NotificationRequest(
                    organization_id=organization_id,
                    template_key=template,
                    recipient_email=recipient,
                    context={
                        "organization_name": summary.get("display_name", ""),
                        "billing_url": f"{portal_links.app_url()}/o/"
                        f"{summary.get('slug', '')}/billing",
                        **context,
                    },
                    idempotency_key=key,
                )
            )
    except Exception as exc:
        logger.warning(
            "payments.notice.skipped", template=template, error_type=type(exc).__name__
        )


def invoice_issued(organization_id: UUID, invoice: Any) -> None:
    _send(
        organization_id,
        INVOICE_ISSUED.key,
        {
            "invoice_number": invoice.number,
            "amount": format_amount(invoice.total_minor, invoice.currency),
            "due_on": invoice.due_at.strftime("%d %B %Y"),
        },
        key=f"invoice:{invoice.id}:issued",
    )


def payment_received(organization_id: UUID, invoice: Any, attempt: Any) -> None:
    _send(
        organization_id,
        PAYMENT_RECEIVED.key,
        {
            "invoice_number": invoice.number,
            "amount": format_amount(attempt.amount_minor, attempt.currency),
        },
        key=f"payment:{attempt.id}:received",
    )


def payment_failed(organization_id: UUID, invoice: Any, attempt: Any) -> None:
    _send(
        organization_id,
        PAYMENT_FAILED.key,
        {"invoice_number": invoice.number},
        key=f"payment:{attempt.id}:failed",
    )
