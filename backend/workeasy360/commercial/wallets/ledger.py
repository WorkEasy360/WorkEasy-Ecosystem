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

from workeasy360.commercial.wallets.models import Wallet, WalletEntry
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
) -> WalletEntry:
    """Move ``amount`` (signed). The same key returns the original entry, whatever
    else this call says: a retry can never move money twice."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
        raise WalletConflict("The amount must be a whole number other than zero.")
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().filter(id=wallet_id).first()
        if wallet is None:
            raise CommercialRecordNotFound("No such wallet.")
        replay = WalletEntry.objects.filter(idempotency_key=idempotency_key).first()
        if replay is not None:
            if replay.wallet_id != wallet.id or replay.amount != amount:
                raise WalletConflict("This key was already used for a different movement.")
            return replay
        after = wallet.balance + amount
        if after < 0:
            raise InsufficientBalance(
                "The wallet does not hold enough for this.", balance=wallet.balance
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


def reversible_amount(entry: WalletEntry) -> int:
    """How much of ``entry`` has not been reversed yet, as a positive number."""
    already = (
        WalletEntry.objects.filter(reverses_id=entry.id).aggregate(total=Sum("amount"))["total"]
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
        original = WalletEntry.objects.select_related("wallet").filter(id=entry_id).first()
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
            raise WalletConflict(f"At most {left} of this transaction can still be reversed.")
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
    totals = dict(WalletEntry.objects.values_list("wallet_id").annotate(total=Sum("amount")))
    return [
        {
            "wallet_id": str(wallet.id),
            "balance": wallet.balance,
            "ledger_total": int(totals.get(wallet.id, 0) or 0),
        }
        for wallet in Wallet.objects.all()
        if wallet.balance != int(totals.get(wallet.id, 0) or 0)
    ]
