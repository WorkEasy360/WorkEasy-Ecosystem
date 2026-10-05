"""Invoices: one per subscription period, built from what was agreed.

**When.** After every commercial change (``on_commercial_change`` - a purchase,
an activation, a plan change) and on the payments clock (a period rolled). Both
call :func:`ensure_invoices`, which is idempotent: ``(subscription,
period_start)`` is unique, so a second call - or a second worker - issues
nothing.

**What.** The subscription's *priced* lines in force now (the price snapshot
each line carries, frozen when it was sold - ``subscriptions.sale_facts``),
summed. A bundle is invoiced as its one priced line; its components carry no
price. Nothing is re-priced here: the invoice reproduces the commitment.

**Not invoiced:** trials (nothing is owed yet), cancelled or ended
subscriptions, a partner-billed account (the partner bills its customer), and a
zero total (a free plan owes nothing - an invoice for zero would only generate
a payment request nobody can make).

**Known limit (recorded as debt):** no proration. A mid-period upgrade's new
line is invoiced from the next period; the current period's invoice is
unchanged once issued.

Runs with no actor, so it writes through the unscoped managers inside exactly
one organization's database scope (RLS still confines it), audited as
``SYSTEM`` with a reason - the same discipline ``subscriptions/system.py`` uses.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from workeasy360.contracts.enums import CollectionMode, SubscriptionStatus
from workeasy360.platform.audit import services as audit
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Statuses whose current period is owed. A past-due subscription's *next*
#: period is still invoiced when it rolls - the debt does not stop the clock.
INVOICEABLE = frozenset(
    {SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE}
)


@contextmanager
def organization_scope(organization_id: UUID, *, reason: str) -> Iterator[None]:
    """Both isolation layers for one organization, with no actor."""
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason=reason, module="commercial.payments"),
        organization_db_scope(organization_id),
    ):
        yield


@contextmanager
def read_scope(organization_id: UUID) -> Iterator[None]:
    """A context for *reading* another module's tenant rows through its own
    selectors (which use the scoped managers) - a routing context that names
    no actor, exactly as the due-work sweeper builds one. Never used for this
    module's writes, which go through the unscoped managers so that a
    read-only (suspended) organization can still be invoiced and paid."""
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    ctx = routing_context(organization_id)
    if ctx is None:
        raise LookupError("organization gone")
    with (
        transaction.atomic(),
        tenant_context(ctx),
        organization_db_scope(organization_id),
    ):
        yield


def billing_account(organization_id: UUID) -> Any:
    from workeasy360.commercial.billing.services import billing_account_or_none

    try:
        with read_scope(organization_id):
            return billing_account_or_none(organization_id)
    except LookupError:
        return None


def on_commercial_change(organization_id: UUID) -> None:
    """The commercial-change listener. A failure here is logged by the hook
    registry and repaired by the clock - never a failed purchase."""
    ensure_invoices(organization_id)


def next_invoice_number() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('payments_invoice_number_seq')")
        (value,) = cursor.fetchone()
    return f"INV-{int(value):06d}"


def ensure_invoices(organization_id: UUID, *, at: datetime | None = None) -> list[UUID]:
    """Issue any invoice now due for this organization. Returns the ids issued."""
    from workeasy360.commercial.subscriptions.selectors import (
        commercial_state,
        sale_facts,
    )

    moment = at or timezone.now()
    subscriptions = [
        s for s in commercial_state(organization_id) if s.status in INVOICEABLE
    ]
    if not subscriptions:
        return []
    account = billing_account(organization_id)
    if account is None or account.collection_mode == CollectionMode.PARTNER_BILLED.value:
        return []
    facts = sale_facts(organization_id)
    issued: list[UUID] = []
    for subscription in subscriptions:
        lines = [
            fact
            for fact in facts
            if fact.subscription_id == subscription.id
            and fact.added_at <= moment
            and (fact.removed_at is None or fact.removed_at > moment)
        ]
        invoice_id = _issue(
            organization_id,
            subscription=subscription,
            lines=lines,
            collection_mode=account.collection_mode,
            moment=moment,
        )
        if invoice_id is not None:
            issued.append(invoice_id)
    return issued


def _issue(
    organization_id: UUID,
    *,
    subscription: Any,
    lines: list[Any],
    collection_mode: str,
    moment: datetime,
) -> UUID | None:
    from workeasy360.commercial.payments.models import Invoice, InvoiceLine

    total = sum(line.total_amount_minor for line in lines)
    if total <= 0:
        return None
    mismatched = [line for line in lines if line.currency != subscription.currency]
    if mismatched:
        # Unreachable through the subscription services, which refuse a price
        # in another currency. Refused here too: an invoice mixing currencies
        # is not a number anyone can pay.
        logger.error(
            "payments.invoice.currency_mismatch",
            organization_id=str(organization_id),
            subscription_id=str(subscription.id),
        )
        metrics.increment("payments_invoice_skipped_total", reason="currency_mismatch")
        return None
    terms = (
        settings.PAYMENT_INVOICE_TERMS_DAYS
        if collection_mode == CollectionMode.INVOICE.value
        else settings.PAYMENT_TERMS_DAYS
    )
    with (
        organization_scope(
            organization_id, reason="Issue an invoice for a billing period"
        ),
        transaction.atomic(),
    ):
        if Invoice.unscoped.filter(
            organization_id=organization_id,
            subscription_id=subscription.id,
            period_start=subscription.current_period_start,
        ).exists():
            return None
        invoice = Invoice(
            organization_id=organization_id,
            subscription_id=subscription.id,
            number=next_invoice_number(),
            currency=subscription.currency,
            total_minor=total,
            billing_interval=subscription.billing_interval.value,
            collection_mode=collection_mode,
            period_start=subscription.current_period_start,
            period_end=subscription.current_period_end,
            issued_at=moment,
            due_at=moment + timedelta(days=int(terms)),
        )
        try:
            # Savepoint: losing the issuance race to another worker is the
            # expected outcome of a duplicate call, not an error.
            with transaction.atomic():
                invoice.save()
        except IntegrityError:
            return None
        InvoiceLine.unscoped.bulk_create(
            [
                InvoiceLine(
                    organization_id=organization_id,
                    invoice=invoice,
                    subscription_item_id=line.item_id,
                    description=_describe(line),
                    product_code=line.product_code,
                    plan_code=line.plan_code,
                    bundle_code=line.bundle_code,
                    quantity=line.quantity,
                    unit_amount_minor=None,
                    amount_minor=line.total_amount_minor,
                )
                for line in lines
            ]
        )
        audit.record_system(
            organization_id,
            "billing.invoice_issued",
            resource_type="payments.invoice",
            resource_id=invoice.id,
            changes={
                "number": invoice.number,
                "total_minor": total,
                "currency": invoice.currency,
                "period_start": invoice.period_start.isoformat(),
            },
            reason="A billing period started under a paid subscription.",
        )
        from workeasy360.commercial.payments import notices

        notices.invoice_issued(organization_id, invoice)
    metrics.increment("payments_invoices_issued_total")
    return invoice.id


def _describe(line: Any) -> str:
    if line.bundle_code:
        return f"{line.bundle_code} ({line.interval})"[:255]
    return f"{line.product_code} - {line.plan_code} x{line.quantity} ({line.interval})"[
        :255
    ]
