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
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

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
    """One order and its open invoice, in the customer's own scope (staff)."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise CatalogValidationError("A command id is required.", field="idempotency_key")
    return _place_in(
        context_for_organization(actor, organization_id),
        record=record,
        description=description,
        amount_minor=amount_minor,
        currency=currency,
        idempotency_key=idempotency_key,
        why="Staff places a wallet order",
    )


def _place_in(
    rebound: TenantContext,
    *,
    record: dict[str, Any],
    description: str,
    amount_minor: int,
    currency: str,
    idempotency_key: str,
    why: str,
    pending: str = "This customer has another order awaiting payment. Settle or cancel it first.",
) -> dict[str, Any]:
    """The order and its invoice for the organization ``rebound`` names."""
    from workeasy360.commercial.payments import orders
    from workeasy360.commercial.payments.models import Invoice, InvoiceLine, Order
    from workeasy360.platform.tenancy.context import tenant_context

    organization_id = rebound.organization_id
    now = timezone.now()
    expires = now + timedelta(hours=max(1, int(settings.ORDER_TTL_HOURS)))
    with (
        _scope(organization_id, reason=why),
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
            placed_by_user_id=rebound.user_id,
            idempotency_key=idempotency_key,
            expires_at=expires,
        )
        try:
            with transaction.atomic():
                order.save()
        except IntegrityError as exc:
            raise PaymentNotAllowed(pending, reason="order_pending") from exc
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


# --- the customer's own top-ups (User Admin Panel, 2026-11-11) -------------------------
#
# The same order, invoice and fulfilment as the staff path above; the organization is
# the member's verified tenant context, never an argument. ``platform.billing.manage``
# (owners, administrators and billing managers), the permission that pays invoices.
# The money reaches the wallet only when the invoice is paid (``credit_for_order``),
# so an abandoned or failed payment moves no balance.

MEMBER_SPEND = "platform.billing.manage"
MEMBER_PENDING = (
    "Another order is waiting for payment. Pay or cancel it in Orders & Payments first."
)


def _member_currency(ctx: TenantContext, currency: Any) -> str:
    """A customer tops up in its billing currency only: the wallet its products charge."""
    from workeasy360.commercial.billing import services as billing

    account = billing.billing_account_or_none(ctx.organization_id)
    if account is None:
        raise CatalogValidationError(
            "Set up your billing profile before adding money to a wallet.",
            field="billing_account",
        )
    code = str(currency or "").strip().upper()
    if code and code != account.currency:
        raise CatalogValidationError(
            f"Wallets are topped up in your billing currency ({account.currency}).",
            field="currency",
        )
    return str(account.currency)


def _member_key(idempotency_key: str) -> str:
    if not idempotency_key or len(idempotency_key) > 128:
        raise CatalogValidationError("A command id is required.", field="idempotency_key")
    return idempotency_key


def member_top_up(
    ctx: TenantContext,
    *,
    wallet_kind: Any,
    amount_minor: Any,
    currency: Any,
    idempotency_key: str,
) -> dict[str, Any]:
    """A member's order for money into its own Platform or Consumption wallet."""
    authz.require(ctx, MEMBER_SPEND)
    key = _member_key(idempotency_key)
    if wallet_kind not in TOP_UP_KINDS:
        raise CatalogValidationError("Choose a wallet to top up.", field="wallet_kind")
    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or not 0 < amount_minor <= MAX_TOP_UP_MINOR
    ):
        raise CatalogValidationError("Enter the amount to add.", field="amount_minor")
    code = _member_currency(ctx, currency)
    label = wallets.kind_label(wallet_kind)
    return _place_in(
        ctx,
        record={"wallet_kind": wallet_kind},
        description=f"{label} top-up",
        amount_minor=amount_minor,
        currency=code,
        idempotency_key=key,
        why="A member places a wallet order",
        pending=MEMBER_PENDING,
    )


def member_credit_pack(
    ctx: TenantContext, *, pack_code: Any, idempotency_key: str
) -> dict[str, Any]:
    """A member's order for an AI credit pack on sale, at the pack's price."""
    authz.require(ctx, MEMBER_SPEND)
    key = _member_key(idempotency_key)
    pack = wallets.active_pack(pack_code)
    if pack is None:
        raise CatalogValidationError("Choose a credit pack on sale.", field="pack_code")
    _member_currency(ctx, pack["currency"])
    return _place_in(
        ctx,
        record={
            "wallet_kind": wallets.AI_CREDITS,
            "credits": pack["credits"],
            "pack_code": pack["code"],
        },
        description=f"{pack['name']}: {pack['credits']} AI credits",
        amount_minor=pack["price_minor"],
        currency=pack["currency"],
        idempotency_key=key,
        why="A member places a wallet order",
        pending=MEMBER_PENDING,
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


# --- a customer paying from its own Platform Wallet (ADR-065) ------------------------
#
# The Digital Store's checkout (and the console's) may pay an invoice in full from the
# Platform Wallet, or part from the wallet and the rest through a payment provider.
#
# - **In full**: a payment attempt with the provider ``wallet``, the deduction and the
#   settlement in one transaction - the same path staff use above, for the member's
#   own organization and with ``platform.billing.manage``.
# - **Split**: the wallet's part is *held* (``ledger.hold``) in the same transaction
#   that starts the provider payment for the rest. The provider's confirmed payment
#   captures the hold inside the settlement's transaction, as a second, wallet,
#   attempt - so the invoice is paid by exactly the two amounts and a refund names
#   each. A payment that fails, expires or is cancelled, and an invoice voided with
#   its order, release the hold: no balance ever moved.
#
# Never for a wallet top-up or an AI credit pack: money for a wallet is paid online.

#: How long past its provider payment a hold still reserves money, as a backstop
#: only - every way a payment ends releases its hold explicitly.
HOLD_GRACE = timedelta(minutes=30)

WALLET_NOT_ELIGIBLE = (
    "Wallet top-ups and AI credit packs are paid online, not from the Platform Wallet."
)


def _refuse_wallet_order(invoice: Any) -> None:
    """A wallet's own order (a top-up or a credit pack) is never paid from a wallet."""
    from workeasy360.commercial.payments.models import Order

    if invoice.order_id is None:
        return
    with _scope(invoice.organization_id, reason="Check what an invoice's order buys"):
        kind = (
            Order.unscoped.filter(
                id=invoice.order_id, organization_id=invoice.organization_id
            )
            .values_list("kind", flat=True)
            .first()
        )
    if kind == Order.Kind.WALLET:
        raise PaymentNotAllowed(WALLET_NOT_ELIGIBLE, reason="wallet_not_eligible")


def _platform_wallet(organization_id: UUID, currency: str) -> dict[str, Any] | None:
    return wallets.organization_wallet(organization_id, wallets.PLATFORM, currency)


def _not_enough() -> PaymentNotAllowed:
    return PaymentNotAllowed(
        "The Platform Wallet does not hold enough for that. Lower the wallet amount "
        "or add money to the wallet first.",
        reason="insufficient_balance",
    )


def wallet_part(attempt: Any) -> dict[str, Any]:
    """What of an attempt's invoice the Platform Wallet pays alongside it: the held,
    captured or released amount. Empty for an attempt that is itself the wallet's."""
    if attempt.provider_key == WALLET_PROVIDER:
        return {"wallet_amount_minor": attempt.amount_minor, "wallet_status": "captured"}
    found = ledger.hold_for_attempt(attempt.id)
    if found is None:
        return {"wallet_amount_minor": 0, "wallet_status": None}
    return {"wallet_amount_minor": found["amount"], "wallet_status": found["status"]}


def hold_for_payment(
    ctx: TenantContext, invoice: Any, attempt: Any, amount_minor: int
) -> dict[str, Any]:
    """Reserve the wallet's part of a split payment. Inside ``checkout.start_payment``'s
    transaction, the invoice locked: a hold that cannot be placed undoes the attempt."""
    _refuse_wallet_order(invoice)
    wallet = _platform_wallet(invoice.organization_id, invoice.currency)
    if wallet is None or ledger.available(wallet["id"]) < amount_minor:
        raise _not_enough()
    try:
        held = ledger.hold(
            wallet["id"],
            amount=amount_minor,
            invoice_id=invoice.id,
            attempt_id=attempt.id,
            reason=f"Reserved for {invoice.number}"[:255],
            idempotency_key=f"hold:{attempt.id}",
            expires_at=attempt.expires_at + HOLD_GRACE,
            actor_user_id=ctx.user_id,
        )
    except ledger.InsufficientBalance as exc:
        raise _not_enough() from exc
    audit.record(
        ctx,
        "billing.wallet_hold_placed",
        resource_type="payments.attempt",
        resource_id=attempt.id,
        changes={
            "invoice": invoice.number,
            "amount_minor": amount_minor,
            "currency": invoice.currency,
        },
    )
    return held


def capture_for_attempt(attempt: Any, invoice: Any, *, now: Any) -> int:
    """The provider confirmed its part: take the wallet's. Inside
    ``settlement.record_success``, the attempt and the invoice locked. Returns what
    the wallet paid (``0`` when this payment held nothing, or its hold is gone and
    the wallet no longer holds that much - the invoice then still owes it)."""
    import uuid

    from workeasy360.commercial.payments.models import PaymentAttempt

    found = ledger.hold_for_attempt(attempt.id)
    if found is None or found["status"] == "captured":
        return 0
    hold_id = found["id"]
    if found["status"] != "held":
        # Released (the payment had expired) before this late success arrived:
        # take it again if the wallet still can, otherwise the invoice owes it.
        wallet = _platform_wallet(invoice.organization_id, invoice.currency)
        if (
            wallet is None
            or ledger.holds_for(invoice_id=invoice.id)
            or ledger.available(wallet["id"]) < found["amount"]
        ):
            logger.warning(
                "payments.wallet_part_unpaid",
                attempt_id=str(attempt.id),
                invoice=invoice.number,
            )
            return 0
        hold_id = ledger.hold(
            wallet["id"],
            amount=found["amount"],
            invoice_id=invoice.id,
            attempt_id=attempt.id,
            reason=f"Reserved again for {invoice.number}"[:255],
            idempotency_key=f"hold-again:{attempt.id}",
            expires_at=now + HOLD_GRACE,
            actor_user_id=attempt.created_by_user_id,
        )["id"]
    # The wallet's own attempt, so a refund can name what it returns. Its id is
    # known first: the ledger entry's key names it (``refund_to_wallet``).
    wallet_attempt_id = uuid.uuid4()
    entry = ledger.capture(
        hold_id,
        entry_type=wallets.SUBSCRIPTION,
        reason=f"Payment of {invoice.number}"[:255],
        idempotency_key=f"invoice-attempt:{wallet_attempt_id}",
        source_type="payment",
        source_id=wallet_attempt_id,
    )
    if entry is None:
        return 0
    PaymentAttempt.unscoped.create(
        id=wallet_attempt_id,
        organization_id=invoice.organization_id,
        invoice=invoice,
        provider_key=WALLET_PROVIDER,
        status="succeeded",
        amount_minor=found["amount"],
        currency=invoice.currency,
        idempotency_key=f"wallet-part:{attempt.id}",
        provider_payment_id=f"wallet:{entry.id}"[:128],
        created_by_user_id=attempt.created_by_user_id,
        reference_note=f"Platform Wallet part of payment {attempt.id}"[:255],
        expires_at=now,
        completed_at=now,
        return_portal=attempt.return_portal,
    )
    audit.record_system(
        invoice.organization_id,
        "billing.wallet_hold_captured",
        resource_type="payments.attempt",
        resource_id=wallet_attempt_id,
        changes={
            "invoice": invoice.number,
            "amount_minor": found["amount"],
            "currency": invoice.currency,
            "with_payment": str(attempt.id),
        },
        reason="The payment provider confirmed the rest of the payment.",
    )
    return int(found["amount"])


def release_for_attempt(attempt: Any, *, why: str) -> None:
    """A payment ended without success: what it held is spendable again."""
    for found in ledger.holds_for(attempt_id=attempt.id):
        if ledger.release(found["id"], why=why):
            _audit_release(attempt.organization_id, found, why)


def release_for_invoice(invoice: Any, *, why: str) -> None:
    """An invoice was voided (with its order): every hold on it is released."""
    for found in ledger.holds_for(invoice_id=invoice.id):
        if ledger.release(found["id"], why=why):
            _audit_release(invoice.organization_id, found, why)


def _audit_release(organization_id: UUID, found: dict[str, Any], why: str) -> None:
    audit.record_system(
        organization_id,
        "billing.wallet_hold_released",
        resource_type="payments.attempt",
        resource_id=found["attempt_id"],
        changes={"amount_minor": found["amount"], "why": why},
        reason="The payment did not complete, so the wallet's reservation ended.",
    )


def member_pay_from_wallet(
    ctx: TenantContext,
    *,
    invoice_id: UUID,
    idempotency_key: str,
    return_portal: str = "",
) -> dict[str, Any]:
    """Pay all an invoice still owes from the member's own Platform Wallet: the
    deduction and the settlement in one transaction. Any provider payment still in
    flight for it is withdrawn first (money that still arrives there is refunded
    automatically, as for any invoice no longer open)."""
    from workeasy360.commercial.payments import checkout
    from workeasy360.commercial.payments.models import Invoice, PaymentAttempt

    authz.require(ctx, MEMBER_SPEND)
    key = _member_key(idempotency_key)
    after = settlement.AfterCommit()
    now = timezone.now()
    # The settlement writes through the unscoped managers (as every payment does),
    # for the member's own organization - from the verified context, never an argument.
    with (
        _scope(ctx.organization_id, reason="A member pays an invoice from the wallet"),
        transaction.atomic(),
    ):
        replay = PaymentAttempt.objects.filter(idempotency_key=key).first()
        if replay is not None:
            if replay.invoice_id != invoice_id:
                raise CatalogValidationError(
                    "This Idempotency-Key was used for another invoice.",
                    field="idempotency_key",
                )
            return checkout.attempt_view(replay)
        invoice = Invoice.objects.select_for_update().filter(id=invoice_id).first()
        if invoice is None:
            raise PaymentRecordNotFound("No such invoice.")
        if invoice.status != Invoice.Status.OPEN or invoice.amount_due_minor <= 0:
            raise PaymentNotAllowed("This invoice has nothing to pay.")
        _refuse_wallet_order(invoice)
        for in_flight in PaymentAttempt.objects.select_for_update().filter(
            invoice=invoice, status__in=("pending", "awaiting_payment")
        ):
            settlement.record_failure(
                in_flight,
                status="cancelled",
                failure_code="paid_from_wallet",
                after=after,
            )
        due = invoice.amount_due_minor
        wallet = _platform_wallet(invoice.organization_id, invoice.currency)
        if wallet is None or ledger.available(wallet["id"]) < due:
            raise _not_enough()
        attempt = PaymentAttempt(
            organization_id=invoice.organization_id,
            invoice=invoice,
            provider_key=WALLET_PROVIDER,
            amount_minor=due,
            currency=invoice.currency,
            idempotency_key=key,
            created_by_user_id=ctx.user_id,
            reference_note="Paid from the Platform Wallet",
            expires_at=now,
            return_portal=return_portal if return_portal == "store" else "",
        )
        attempt.save()
        try:
            entry = ledger.post(
                wallet["id"],
                amount=-due,
                entry_type=wallets.SUBSCRIPTION,
                reason=f"Payment of {invoice.number}"[:255],
                idempotency_key=f"invoice-attempt:{attempt.id}",
                actor_user_id=ctx.user_id,
                source_type="payment",
                source_id=attempt.id,
            )
        except ledger.InsufficientBalance as exc:
            raise _not_enough() from exc
        settlement.record_success(
            attempt,
            amount_minor=due,
            currency=invoice.currency,
            payment_id=f"wallet:{entry.id}"[:128],
            after=after,
        )
        audit.record(
            ctx,
            "billing.invoice_paid_from_wallet",
            resource_type="payments.invoice",
            resource_id=invoice.id,
            changes={"number": invoice.number, "amount_minor": due},
        )
    after.run(ctx.organization_id)
    attempt.refresh_from_db()
    return checkout.attempt_view(attempt)


def member_pay_invoice(
    ctx: TenantContext,
    *,
    invoice_id: UUID,
    idempotency_key: str,
    wallet_amount_minor: Any = 0,
    return_portal: str = "",
) -> dict[str, Any]:
    """Pay an invoice the way the payer chose: through a provider (``0`` from the
    wallet), all from the Platform Wallet, or split between them. The amounts are the
    server's: the wallet's part is checked against what the invoice still owes and
    what the wallet holds, never trusted from the browser."""
    from workeasy360.commercial.payments import checkout
    from workeasy360.commercial.payments.models import Invoice, PaymentAttempt

    # A retried request is the payment it started, whatever the invoice owes now.
    replay = PaymentAttempt.objects.filter(
        idempotency_key=str(idempotency_key or "")
    ).first()
    if replay is not None and replay.invoice_id == invoice_id:
        authz.require(ctx, MEMBER_SPEND)
        return checkout.attempt_view(replay)
    if wallet_amount_minor in (None, ""):
        wallet_amount_minor = 0
    if (
        isinstance(wallet_amount_minor, bool)
        or not isinstance(wallet_amount_minor, int)
        or wallet_amount_minor < 0
    ):
        raise CatalogValidationError(
            "The wallet amount must be a whole number of minor units.",
            field="wallet_amount_minor",
        )
    if wallet_amount_minor == 0:
        return checkout.start_payment(
            ctx,
            invoice_id=invoice_id,
            idempotency_key=idempotency_key,
            return_portal=return_portal,
        )
    invoice = Invoice.objects.filter(id=invoice_id).first()
    if invoice is None:
        raise PaymentRecordNotFound("No such invoice.")
    if wallet_amount_minor > invoice.amount_due_minor:
        raise CatalogValidationError(
            "The wallet amount is more than this invoice still owes.",
            field="wallet_amount_minor",
        )
    if wallet_amount_minor == invoice.amount_due_minor:
        return member_pay_from_wallet(
            ctx,
            invoice_id=invoice_id,
            idempotency_key=idempotency_key,
            return_portal=return_portal,
        )
    authz.require(ctx, MEMBER_SPEND)
    return checkout.start_payment(
        ctx,
        invoice_id=invoice_id,
        idempotency_key=idempotency_key,
        return_portal=return_portal,
        wallet_minor=wallet_amount_minor,
    )
