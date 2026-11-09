"""What the rest of ``commercial`` may use of this module: the names of the three
balances and of the movements, and a few reads, never a model (F-6).

The movements themselves go through ``ledger``."""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from workeasy360.commercial.wallets.models import CreditPack, Wallet, WalletEntry

PLATFORM: Final = Wallet.Kind.PLATFORM.value
AI_CREDITS: Final = Wallet.Kind.AI_CREDITS.value
CONSUMPTION: Final = Wallet.Kind.CONSUMPTION.value

ORGANIZATION: Final = Wallet.OwnerType.ORGANIZATION.value
PARTNER: Final = Wallet.OwnerType.PARTNER.value

TOP_UP: Final = WalletEntry.Type.TOP_UP.value
CREDIT_PURCHASE: Final = WalletEntry.Type.CREDIT_PURCHASE.value
SUBSCRIPTION: Final = WalletEntry.Type.SUBSCRIPTION.value
REFUND: Final = WalletEntry.Type.REFUND.value


def kind_label(kind: str) -> str:
    return str(Wallet.Kind(kind).label)


def active_pack(code: Any) -> dict[str, Any] | None:
    """A credit pack on sale, or ``None``."""
    pack = CreditPack.objects.filter(code=str(code or ""), status="active").first()
    if pack is None:
        return None
    return {
        "code": pack.code,
        "name": pack.name,
        "credits": pack.credits,
        "price_minor": pack.price_minor,
        "currency": pack.currency,
    }


def entry_id_for_key(key: str) -> UUID | None:
    """The entry a key produced, if it ran."""
    return (
        WalletEntry.objects.filter(idempotency_key=key)
        .values_list("id", flat=True)
        .first()
    )


def balance_for_entry(entry_id: UUID) -> int:
    """What the wallet behind this entry holds now."""
    return WalletEntry.objects.select_related("wallet").get(id=entry_id).wallet.balance


def organization_wallet(
    organization_id: UUID, kind: str, currency: str
) -> dict[str, Any] | None:
    """This customer's wallet of this kind and currency, if it has one."""
    row = Wallet.objects.filter(
        owner_type=ORGANIZATION, owner_id=organization_id, kind=kind, currency=currency
    ).first()
    return {"id": row.id, "balance": row.balance} if row else None
