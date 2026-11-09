"""Money reaching a wallet, and an invoice paid from one (2026-11-09).

**A top-up and a credit pack are orders.** They reuse everything: the order's invoice,
the hosted checkout or a recorded bank transfer, the webhook, the refund. There is no
second way to pay. A paid order credits the wallet once (the ledger's key is the order),
and if the wallet cannot be credited the order fails and is refunded like any other.

**Paying an invoice from the Platform Wallet** is a payment attempt with the provider
``wallet``, settled through the same path a provider's is, so the invoice, its order and
the subscription behave exactly as if a card had paid. The deduction and the settlement
share one transaction: there is no state where one happened without the other.

**A refund always names what it reverses.** Refunding a top-up takes the money back out
of the wallet (refused up front if it has already been spent). Refunding a wallet payment
puts it back in the wallet at once, because there is no provider to wait for. Both
ledger entries reference the original entry.

Open, because the owner's documents were not available: tax on a top-up or a credit pack
(none is charged here), and whether a renewal pays from the wallet by itself (it does
not: a person chooses it).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.payments import settlement
from workeasy360.commercial.wallets import api as wallets
from workeasy360.commercial.wallets import ledger
from workeasy360.contracts.errors import (
    CatalogValidationError,
    PaymentNotAllowed,
    PaymentRecordNotFound,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import context_for_organization

MANAGE = "platform.staff_wallet.manage"
WALLET_PROVIDER = "wallet"

#: What can be topped up with money. AI Credits come in packs, never free amounts.
TOP_UP_KINDS = (wallets.PLATFORM, wallets.CONSUMPTION)
MAX_TOP_UP_MINOR = 100_000_000_00


def _scope(organization_id: UUID, *, reason: str) -> Any:
    from workeasy360.commercial.payments.invoicing import organization_scope

    return organization_scope(organization_id, reason=reason)


def _order_line(order: Any) -> dict[str, Any]:
    return (order.lines or [{}])[0]


def _place(
    actor: TenantContext,
    organization_id: UUID,
    *,
    record: dict[str, Any],
    description: str,
    amount_minor: int,
    currency: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """One order and its open invoice, in the customer's own scope."""
    from workeasy360.commercial.payments import orders
    from workeasy360.commercial.payments.models import Invoice, InvoiceLine, Order
    from workeasy360.platform.tenancy.context import tenant_context

    if not idempotency_key or len(idempotency_key) > 128:
        raise CatalogValidationError("A command id is required.", field="idempotency_key")
    rebound = context_for_organization(actor, organization_id)
    now = timezone.now()
    expires = now + timedelta(hours=max(1, int(settings.ORDER_TTL_HOURS)))
    with (
        _scope(organization_id, reason="Staff places a wallet order"),
        tenant_context(rebound),
        transaction.atomic(),
    ):
        replay = Order.objects.filter(idempotency_key=idempotency_key).first()
        if replay is not None:
            return orders.order_view(replay)
        order = Order(
            organization_id=organization_id,
            number=orders._next_number(),
            kind=Order.Kind.WALLET,
            status=Order.Status.PENDING_PAYMENT,
            quantity=1,
            billing_interval="one_time",
            currency=currency,
            description=description[:255],
            product_codes=[],
            amount_due_minor=amount_minor,
            lines=[record],
            placed_by_user_id=actor.user_id,
            idempotency_key=idempotency_key,
            expires_at=expires,
        )
        try:
            with transaction.atomic():
                order.save()
        except IntegrityError as exc:
            raise PaymentNotAllowed(
                "This customer has another order awaiting payment. Settle or cancel it first.",
                reason="order_pending",
            ) from exc
        invoice = Invoice.objects.create(
            order=order,
            number=orders._invoice_number(),
            currency=currency,
            total_minor=amount_minor,
            billing_interval="one_time",
            collection_mode="automatic",
            period_start=now,
            period_end=now + timedelta(days=1),
            issued_at=now,
            due_at=expires,
        )
        # Its own kind of line, so the commission ledger, which earns only on product
        # charges, never pays a share of a top-up.
        InvoiceLine.objects.create(
            invoice=invoice,
            kind=InvoiceLine.Kind.WALLET,
            description=description[:255],
            quantity=1,
            amount_minor=amount_minor,
        )
        orders._sync_summary(order)
        audit.record(
            rebound,
            "wallet.order_placed",
            resource_type="payments.order",
            resource_id=order.id,
            changes={
                "number": order.number,
                "wallet": record["wallet_kind"],
                "amount_minor": amount_minor,
                "currency": currency,
                "credits": record.get("credits"),
            },
        )
        return orders.order_view(order)


def place_top_up(
    actor: TenantContext,
    organization_id: UUID,
    *,
    wallet_kind: Any,
    amount_minor: Any,
    currency: Any,
    idempotency_key: str,
) -> dict[str, Any]:
    """An order for money into the Platform or Consumption wallet."""
    authz.require(actor, MANAGE)
    if wallet_kind not in TOP_UP_KINDS:
        raise CatalogValidationError("Choose a wallet to top up.", field="wallet_kind")
    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or not 0 < amount_minor <= MAX_TOP_UP_MINOR
    ):
        raise CatalogValidationError("Enter the amount to top up.", field="amount_minor")
    code = str(currency or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise CatalogValidationError("Choose a currency.", field="currency")
    label = wallets.kind_label(wallet_kind)
    return _place(
        actor,
        organization_id,
        record={"wallet_kind": wallet_kind},
        description=f"{label} top-up",
        amount_minor=amount_minor,
        currency=code,
        idempotency_key=idempotency_key,
    )


def place_credit_pack(
    actor: TenantContext, organization_id: UUID, *, pack_code: Any, idempotency_key: str
) -> dict[str, Any]:
    """An order for an AI credit pack, at the pack's price as an Admin entered it."""
    authz.require(actor, MANAGE)
    pack = wallets.active_pack(pack_code)
    if pack is None:
        raise CatalogValidationError("Choose a credit pack on sale.", field="pack_code")
    return _place(
        actor,
        organization_id,
        record={
            "wallet_kind": wallets.AI_CREDITS,
            "credits": pack["credits"],
            "pack_code": pack["code"],
        },
        description=f"{pack['name']}: {pack['credits']} AI credits",
        amount_minor=pack["price_minor"],
        currency=pack["currency"],
        idempotency_key=idempotency_key,
    )


def _wallet_for(order: Any) -> Any:
    kind = _order_line(order)["wallet_kind"]
    return ledger.ensure(
        owner_type=wallets.ORGANIZATION,
        owner_id=order.organization_id,
        kind=kind,
        currency=order.currency,
    )


def credit_for_order(order: Any) -> Any:
    """Credit what a paid order bought. Called inside the order's fulfilment
    transaction; the key is the order, so a re-driven fulfilment credits nothing."""
    line = _order_line(order)
    is_pack = line["wallet_kind"] == wallets.AI_CREDITS
    return ledger.post(
        _wallet_for(order).id,
        amount=int(line["credits"]) if is_pack else order.amount_due_minor,
        entry_type=wallets.CREDIT_PURCHASE if is_pack else wallets.TOP_UP,
        reason=order.description,
        idempotency_key=f"order:{order.id}",
        actor_user_id=order.placed_by_user_id,
        source_type="order",
        source_id=order.number,
    )


def _original_entry(order: Any) -> UUID | None:
    return wallets.entry_id_for_key(f"order:{order.id}")


def _take_back(order: Any, refund_minor: int) -> int:
    """What of a wallet purchase a refund of ``refund_minor`` removes from the wallet:
    the same money, or the same share of the credits."""
    line = _order_line(order)
    if line["wallet_kind"] != wallets.AI_CREDITS:
        return refund_minor
    credits = int(line["credits"])
    if refund_minor >= order.amount_due_minor:
        return credits
    return credits * refund_minor // order.amount_due_minor


def assert_refundable(invoice: Any, refund_minor: int) -> None:
    """Refuse up front a refund of money the wallet no longer holds, before a provider
    is asked to return it. Called while requesting a refund."""
    from workeasy360.commercial.payments.models import Order

    if invoice.order_id is None:
        return
    order = Order.unscoped.filter(
        id=invoice.order_id, organization_id=invoice.organization_id
    ).first()
    if order is None or order.kind != Order.Kind.WALLET:
        return
    entry = _original_entry(order)
    if entry is None:
        return
    if wallets.balance_for_entry(entry) < _take_back(order, refund_minor):
        raise PaymentNotAllowed(
            "That much of this top-up has already been spent, so it cannot be refunded.",
            reason="wallet_spent",
        )


def reverse_for_refund(refund: Any, invoice: Any) -> Any:
    """A refund of a wallet purchase was confirmed: take it back out, naming the
    original credit. Inside the refund's own transaction."""
    from workeasy360.commercial.payments.models import Order

    if invoice.order_id is None:
        return None
    order = Order.unscoped.filter(
        id=invoice.order_id, organization_id=invoice.organization_id
    ).first()
    if order is None or order.kind != Order.Kind.WALLET:
        return None
    entry = _original_entry(order)
    if entry is None:
        return None
    return ledger.reverse(
        entry,
        reason=f"Refund of {invoice.number}: {refund.reason}"[:255],
        idempotency_key=f"refund:{refund.id}",
        amount=_take_back(order, refund.amount_minor),
        actor_user_id=refund.requested_by_user_id,
        entry_type=wallets.REFUND,
        source_type="refund",
        source_id=refund.id,
    )


# --- paying an invoice from the wallet ------------------------------------------------


def pay_invoice_from_wallet(
    actor: TenantContext,
    organization_id: UUID,
    invoice_id: UUID,
    *,
    reason: Any,
    expected_version: Any,
) -> dict[str, Any]:
    """Pay what an invoice owes from the customer's Platform Wallet."""
    from workeasy360.commercial.payments.models import Invoice, PaymentAttempt
    from workeasy360.commercial.payments.staff import invoice_json
    from workeasy360.platform.tenancy.context import tenant_context

    authz.require(actor, MANAGE)
    why = str(reason or "").strip()
    if len(why) < 10:
        raise CatalogValidationError(
            "Say why, in at least 10 characters.", field="reason"
        )
    rebound = context_for_organization(actor, organization_id)
    after = settlement.AfterCommit()
    with (
        _scope(organization_id, reason="Staff pays an invoice from the wallet"),
        tenant_context(rebound),
        transaction.atomic(),
    ):
        invoice = (
            Invoice.unscoped.select_for_update()
            .filter(id=invoice_id, organization_id=organization_id)
            .first()
        )
        if invoice is None:
            raise PaymentRecordNotFound("No such invoice.")
        if invoice.version != expected_version:
            raise PaymentNotAllowed("The invoice changed. Reload and retry.")
        if invoice.status != Invoice.Status.OPEN or invoice.amount_due_minor <= 0:
            raise PaymentNotAllowed("This invoice has nothing to pay.")
        wallet = wallets.organization_wallet(
            organization_id, wallets.PLATFORM, invoice.currency
        )
        if wallet is None or wallet["balance"] < invoice.amount_due_minor:
            raise PaymentNotAllowed(
                "The Platform Wallet does not hold enough to pay this invoice.",
                reason="insufficient_balance",
            )
        now = timezone.now()
        attempt = PaymentAttempt(
            organization_id=organization_id,
            invoice=invoice,
            provider_key=WALLET_PROVIDER,
            amount_minor=invoice.amount_due_minor,
            currency=invoice.currency,
            idempotency_key=f"wallet:{invoice.id}:{invoice.version}",
            created_by_user_id=actor.user_id,
            reference_note="Paid from the Platform Wallet",
            expires_at=now,
        )
        attempt.save()
        entry = ledger.post(
            wallet["id"],
            amount=-attempt.amount_minor,
            entry_type=wallets.SUBSCRIPTION,
            reason=f"Payment of {invoice.number}"[:255],
            idempotency_key=f"invoice-attempt:{attempt.id}",
            actor_user_id=actor.user_id,
            source_type="payment",
            source_id=attempt.id,
        )
        settlement.record_success(
            attempt,
            amount_minor=attempt.amount_minor,
            currency=attempt.currency,
            payment_id=f"wallet:{entry.id}"[:128],
            after=after,
        )
        audit.record(
            rebound,
            "billing.invoice_paid_from_wallet",
            resource_type="payments.invoice",
            resource_id=invoice.id,
            changes={"number": invoice.number, "amount_minor": attempt.amount_minor},
            reason=why,
        )
        invoice = Invoice.unscoped.get(id=invoice_id, organization_id=organization_id)
    after.run(organization_id)
    return invoice_json(invoice)


def refund_to_wallet(refund: Any, attempt: Any) -> None:
    """A wallet payment's refund goes straight back to the wallet it came from, naming
    the deduction. Called inside the refund's transaction, by ``refunds.request_refund``."""
    deduction = wallets.entry_id_for_key(f"invoice-attempt:{attempt.id}")
    if deduction is None:
        raise PaymentNotAllowed("The wallet payment behind this refund cannot be found.")
    ledger.reverse(
        deduction,
        reason=f"Refund: {refund.reason}"[:255],
        idempotency_key=f"refund:{refund.id}",
        amount=refund.amount_minor,
        actor_user_id=refund.requested_by_user_id,
        entry_type=wallets.REFUND,
        source_type="refund",
        source_id=refund.id,
    )
    settlement.record_refund(
        refund, succeeded=True, reference=f"wallet:{deduction}"[:128]
    )
