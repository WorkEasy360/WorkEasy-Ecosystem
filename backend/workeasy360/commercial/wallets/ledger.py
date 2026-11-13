"""The only code that changes a balance.

Every movement here, in one transaction: lock the wallet row, refuse a repeat of the
same key, refuse an overdraft, write the entry with the balance it leaves, then the
balance. The database repeats the two rules that matter (no negative balance, a
unique key), so a bug above this file still cannot create money or a second charge.

Partners' commissions never come through here (``apps.py``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from workeasy360.commercial.wallets.models import Wallet, WalletEntry, WalletHold
from workeasy360.contracts.errors import CommercialError, CommercialRecordNotFound


class InsufficientBalance(CommercialError):
    code = "insufficient_balance"
    http_status = 409


class WalletConflict(CommercialError):
    code = "wallet_conflict"
    http_status = 409


def kind_uses_currency(kind: str) -> bool:
    return kind != Wallet.Kind.AI_CREDITS


def ensure(*, owner_type: str, owner_id: UUID, kind: str, currency: str = "") -> Wallet:
    """This owner's wallet of this kind, opened at nothing if it had none."""
    wallet, _created = Wallet.objects.get_or_create(
        owner_type=owner_type,
        owner_id=owner_id,
        kind=kind,
        currency=currency if kind_uses_currency(kind) else "",
    )
    return wallet


def post(
    wallet_id: UUID,
    *,
    amount: int,
    entry_type: str,
    reason: str,
    idempotency_key: str,
    actor_user_id: UUID | None = None,
    source_type: str = "",
    source_id: Any = "",
    reverses_id: UUID | None = None,
    capturing_hold_id: UUID | None = None,
) -> WalletEntry:
    """Move ``amount`` (signed). The same key returns the original entry, whatever
    else this call says: a retry can never move money twice.

    ADR-065: a debit may not spend money a hold reserves for a payment in
    progress - only that hold's own capture (``capturing_hold_id``) may."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
        raise WalletConflict("The amount must be a whole number other than zero.")
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().filter(id=wallet_id).first()
        if wallet is None:
            raise CommercialRecordNotFound("No such wallet.")
        replay = WalletEntry.objects.filter(idempotency_key=idempotency_key).first()
        if replay is not None:
            if replay.wallet_id != wallet.id or replay.amount != amount:
                raise WalletConflict(
                    "This key was already used for a different movement."
                )
            return replay
        after = wallet.balance + amount
        if after < 0:
            raise InsufficientBalance(
                "The wallet does not hold enough for this.", balance=wallet.balance
            )
        if amount < 0:
            reserved = _held(wallet.id, excluding=capturing_hold_id)
            if after < reserved:
                raise InsufficientBalance(
                    "Part of this wallet is reserved for a payment in progress.",
                    balance=wallet.balance,
                    reserved=reserved,
                )
        entry = WalletEntry.objects.create(
            wallet=wallet,
            entry_type=entry_type,
            amount=amount,
            balance_after=after,
            reason=reason[:255],
            actor_user_id=actor_user_id,
            source_type=source_type,
            source_id=str(source_id or "")[:64],
            reverses_id=reverses_id,
            idempotency_key=idempotency_key,
            created_at=timezone.now(),
        )
        wallet.balance = after
        wallet.save(update_fields=["balance", "updated_at"])
    return entry


# --- holds (ADR-065) ----------------------------------------------------------------


def _held(wallet_id: UUID, *, excluding: UUID | None = None) -> int:
    """What live holds reserve in this wallet now. A hold past its backstop time
    reserves nothing (every way a payment ends releases its hold explicitly)."""
    rows = WalletHold.objects.filter(
        wallet_id=wallet_id,
        status=WalletHold.Status.HELD,
        expires_at__gt=timezone.now(),
    )
    if excluding is not None:
        rows = rows.exclude(id=excluding)
    return int(rows.aggregate(total=Sum("amount"))["total"] or 0)


def available(wallet_id: UUID) -> int:
    """The balance less what is reserved: what may be spent or held now."""
    wallet = Wallet.objects.filter(id=wallet_id).first()
    if wallet is None:
        return 0
    return max(0, wallet.balance - _held(wallet.id))


def _hold_dict(row: WalletHold) -> dict[str, Any]:
    return {
        "id": row.id,
        "wallet_id": row.wallet_id,
        "invoice_id": row.invoice_id,
        "attempt_id": row.attempt_id,
        "amount": row.amount,
        "status": row.status,
        "entry_id": row.entry_id,
        "expires_at": row.expires_at,
    }


def hold(
    wallet_id: UUID,
    *,
    amount: int,
    invoice_id: UUID,
    attempt_id: UUID,
    reason: str,
    idempotency_key: str,
    expires_at: Any,
    actor_user_id: UUID | None = None,
) -> dict[str, Any]:
    """Reserve ``amount`` of this wallet for one payment. The same key returns the
    same hold; never more than the wallet holds less what is already reserved."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise WalletConflict("A hold must be a whole number above zero.")
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().filter(id=wallet_id).first()
        if wallet is None:
            raise CommercialRecordNotFound("No such wallet.")
        replay = WalletHold.objects.filter(idempotency_key=idempotency_key).first()
        if replay is not None:
            if replay.wallet_id != wallet.id or replay.amount != amount:
                raise WalletConflict("This key was already used for a different hold.")
            return _hold_dict(replay)
        if wallet.balance - _held(wallet.id) < amount:
            raise InsufficientBalance(
                "The wallet does not hold enough for this.", balance=wallet.balance
            )
        row = WalletHold.objects.create(
            wallet=wallet,
            invoice_id=invoice_id,
            attempt_id=attempt_id,
            amount=amount,
            reason=reason[:255],
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
    return _hold_dict(row)


def holds_for(
    *, attempt_id: UUID | None = None, invoice_id: UUID | None = None
) -> list[dict[str, Any]]:
    """The live holds of one payment attempt or one invoice."""
    rows = WalletHold.objects.filter(status=WalletHold.Status.HELD)
    if attempt_id is not None:
        rows = rows.filter(attempt_id=attempt_id)
    if invoice_id is not None:
        rows = rows.filter(invoice_id=invoice_id)
    return [_hold_dict(row) for row in rows.order_by("created_at")]


def hold_for_attempt(attempt_id: UUID) -> dict[str, Any] | None:
    """The hold an attempt was started with, in whatever state it is now."""
    row = WalletHold.objects.filter(attempt_id=attempt_id).order_by("-created_at").first()
    return _hold_dict(row) if row is not None else None


def capture(
    hold_id: UUID,
    *,
    entry_type: str,
    reason: str,
    idempotency_key: str,
    source_type: str = "",
    source_id: Any = "",
) -> WalletEntry | None:
    """Take a held amount: one ledger entry, and the hold marked captured. A hold
    already captured returns its entry; a released one captures nothing
    (``None``) - the caller decides what that payment still owes."""
    with transaction.atomic():
        row = WalletHold.objects.filter(id=hold_id).first()
        if row is None:
            raise CommercialRecordNotFound("No such hold.")
        # The ledger's order of locks: the wallet first, then the hold.
        Wallet.objects.select_for_update().filter(id=row.wallet_id).first()
        row = WalletHold.objects.select_for_update().get(id=hold_id)
        if row.status == WalletHold.Status.CAPTURED:
            return WalletEntry.objects.filter(id=row.entry_id).first()
        if row.status != WalletHold.Status.HELD:
            return None
        entry = post(
            row.wallet_id,
            amount=-row.amount,
            entry_type=entry_type,
            reason=reason,
            idempotency_key=idempotency_key,
            actor_user_id=row.actor_user_id,
            source_type=source_type,
            source_id=source_id,
            capturing_hold_id=row.id,
        )
        row.status = WalletHold.Status.CAPTURED
        row.entry = entry
        row.settled_at = timezone.now()
        row.save(update_fields=["status", "entry", "settled_at"])
    return entry


def release(hold_id: UUID, *, why: str) -> bool:
    """Give a held amount back to the wallet's spendable balance. True when this
    call released it; a hold already captured or released is left alone."""
    with transaction.atomic():
        row = WalletHold.objects.filter(id=hold_id).first()
        if row is None:
            return False
        Wallet.objects.select_for_update().filter(id=row.wallet_id).first()
        moved = WalletHold.objects.filter(
            id=hold_id, status=WalletHold.Status.HELD
        ).update(
            status=WalletHold.Status.RELEASED,
            settled_at=timezone.now(),
            release_reason=why[:64],
        )
    return moved == 1


def reversible_amount(entry: WalletEntry) -> int:
    """How much of ``entry`` has not been reversed yet, as a positive number."""
    already = (
        WalletEntry.objects.filter(reverses_id=entry.id).aggregate(total=Sum("amount"))[
            "total"
        ]
        or 0
    )
    return abs(entry.amount) - abs(already)


def reverse(
    entry_id: UUID,
    *,
    reason: str,
    idempotency_key: str,
    amount: int | None = None,
    actor_user_id: UUID | None = None,
    entry_type: str = WalletEntry.Type.REVERSAL,
    source_type: str = "",
    source_id: Any = "",
) -> WalletEntry:
    """Answer an entry with its opposite, in whole or in part, naming it. Never
    more than it moved, and never a reversal of a reversal."""
    with transaction.atomic():
        original = (
            WalletEntry.objects.select_related("wallet").filter(id=entry_id).first()
        )
        if original is None:
            raise CommercialRecordNotFound("No such transaction.")
        # The same order of locks as ``post``: the wallet first.
        Wallet.objects.select_for_update().filter(id=original.wallet_id).first()
        replay = WalletEntry.objects.filter(idempotency_key=idempotency_key).first()
        if replay is not None:
            return replay
        if original.reverses_id is not None:
            raise WalletConflict("A reversal cannot itself be reversed.")
        left = reversible_amount(original)
        wanted = left if amount is None else amount
        if isinstance(wanted, bool) or not isinstance(wanted, int) or wanted <= 0:
            raise WalletConflict("Nothing of this transaction is left to reverse.")
        if wanted > left:
            raise WalletConflict(
                f"At most {left} of this transaction can still be reversed."
            )
        sign = -1 if original.amount > 0 else 1
        return post(
            original.wallet_id,
            amount=sign * wanted,
            entry_type=entry_type,
            reason=reason,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            source_type=source_type,
            source_id=source_id,
            reverses_id=original.id,
        )


def reconcile() -> list[dict[str, Any]]:
    """Wallets whose balance is not what their entries add up to. Empty, always,
    unless someone wrote to these tables behind this file."""
    totals = dict(
        WalletEntry.objects.values_list("wallet_id").annotate(total=Sum("amount"))
    )
    return [
        {
            "wallet_id": str(wallet.id),
            "balance": wallet.balance,
            "ledger_total": int(totals.get(wallet.id, 0) or 0),
        }
        for wallet in Wallet.objects.all()
        if wallet.balance != int(totals.get(wallet.id, 0) or 0)
    ]
