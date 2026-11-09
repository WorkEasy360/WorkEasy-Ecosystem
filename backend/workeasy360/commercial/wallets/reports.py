"""The Wallet Movements report's rows. Its caller has authorized."""

from __future__ import annotations

from typing import Any

from django.db.models import Q

from workeasy360.commercial.wallets.admin import entry_row
from workeasy360.commercial.wallets.models import WalletEntry


def movements(
    *, start: Any, end: Any, search: Any, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    rows = WalletEntry.objects.select_related("wallet").filter(
        created_at__gte=start, created_at__lt=end
    )
    if search:
        rows = rows.filter(Q(reason__icontains=search) | Q(source_id__icontains=search))
    total = rows.count()
    page = rows.order_by("-created_at", "-id")[offset : offset + limit]
    return (
        [
            {
                **entry_row(row),
                "wallet_kind": row.wallet.get_kind_display(),
                "owner_type": row.wallet.owner_type,
                "owner_id": str(row.wallet.owner_id),
                "currency": row.wallet.currency,
            }
            for row in page
        ],
        total,
    )
