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


# --- a partner's own Platform Wallet (ADR-063) --------------------------------------
#
# Reads for the partner portal, whose caller has already authorized the partner and
# the permission. Dictionaries only, never a model.

USAGE: Final = WalletEntry.Type.USAGE.value
ADJUSTMENT: Final = WalletEntry.Type.ADJUSTMENT.value
REVERSAL: Final = WalletEntry.Type.REVERSAL.value
ENTRY_TYPES: Final = tuple(t.value for t in WalletEntry.Type)


def entry_type_label(entry_type: str) -> str:
    try:
        return str(WalletEntry.Type(entry_type).label)
    except ValueError:
        return entry_type


def partner_wallets(partner_id: UUID) -> list[dict[str, Any]]:
    """Every Platform Wallet this partner holds, one per currency."""
    rows = Wallet.objects.filter(
        owner_type=PARTNER, owner_id=partner_id, kind=PLATFORM
    ).order_by("currency")
    return [
        {
            "wallet_id": str(row.id),
            "currency": row.currency,
            "balance_minor": row.balance,
            "low_balance_threshold": row.low_balance_threshold,
            "is_low": row.is_low,
        }
        for row in rows
    ]


def partner_wallet_id(partner_id: UUID, currency: str) -> UUID | None:
    return (
        Wallet.objects.filter(
            owner_type=PARTNER, owner_id=partner_id, kind=PLATFORM, currency=currency
        )
        .values_list("id", flat=True)
        .first()
    )


def _entry_dict(row: WalletEntry) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "currency": row.wallet.currency,
        "entry_type": row.entry_type,
        "entry_type_label": entry_type_label(row.entry_type),
        "amount_minor": row.amount,
        "balance_after_minor": row.balance_after,
        "reason": row.reason,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "reverses_id": str(row.reverses_id) if row.reverses_id else None,
        "created_at": row.created_at.isoformat(),
    }


def partner_entries(
    partner_id: UUID,
    *,
    currency: str | None = None,
    entry_type: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """This partner's Platform Wallet movements, newest first, a page at a time."""
    rows = WalletEntry.objects.select_related("wallet").filter(
        wallet__owner_type=PARTNER, wallet__owner_id=partner_id, wallet__kind=PLATFORM
    )
    if currency:
        rows = rows.filter(wallet__currency=currency)
    if entry_type in ENTRY_TYPES:
        rows = rows.filter(entry_type=entry_type)
    total = rows.count()
    page = list(rows.order_by("-created_at", "-id")[offset : offset + limit])
    return {
        "items": [_entry_dict(row) for row in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def entries_for_sources(
    source_type: str, source_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Movements naming these sources (orders, subscriptions), keyed by source id."""
    found: dict[str, list[dict[str, Any]]] = {}
    if not source_ids:
        return found
    rows = WalletEntry.objects.select_related("wallet").filter(
        source_type=source_type, source_id__in=source_ids
    )
    for row in rows.order_by("created_at"):
        found.setdefault(row.source_id, []).append(_entry_dict(row))
    return found
