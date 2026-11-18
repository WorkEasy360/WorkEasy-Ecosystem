"""Reads of the wallets, for screens whose callers have authorized."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, F, Sum

from workeasy360.commercial.wallets.models import Wallet


def platform_totals(currency: str | None) -> dict[str, Any]:
    """What the three balances add up to across owners. Money wallets for the chosen
    currency, never added across currencies. Credits are whole credits."""
    rows = (
        Wallet.objects.values("kind", "currency")
        .annotate(total=Sum("balance"), wallets=Count("id"))
        .order_by("kind", "currency")
    )
    by_kind: dict[str, list[dict[str, Any]]] = {kind.value: [] for kind in Wallet.Kind}
    for row in rows:
        by_kind[row["kind"]].append(
            {
                "currency": row["currency"],
                "balance": int(row["total"] or 0),
                "wallets": int(row["wallets"]),
            }
        )

    def chosen(kind: str) -> dict[str, Any]:
        options = by_kind[kind]
        if kind == Wallet.Kind.AI_CREDITS.value:
            hit = options[0] if options else None
        else:
            hit = next((o for o in options if o["currency"] == currency), None)
        return {
            "kind": kind,
            "label": Wallet.Kind(kind).label,
            "currency": "" if kind == Wallet.Kind.AI_CREDITS.value else (currency or ""),
            "balance": hit["balance"] if hit else 0,
            "wallets": hit["wallets"] if hit else 0,
            "other_currencies": [o for o in options if o is not hit and o["currency"]],
        }

    return {
        "platform": chosen(Wallet.Kind.PLATFORM.value),
        "ai_credits": chosen(Wallet.Kind.AI_CREDITS.value),
        "consumption": chosen(Wallet.Kind.CONSUMPTION.value),
    }


def low_balance_rows() -> Any:
    """Wallets below the threshold an Admin set for them."""
    return Wallet.objects.filter(
        low_balance_threshold__isnull=False, balance__lt=F("low_balance_threshold")
    )


def low_balance_count() -> int:
    return low_balance_rows().count()
