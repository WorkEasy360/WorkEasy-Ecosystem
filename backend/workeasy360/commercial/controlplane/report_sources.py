"""Report sources that need names from more than one module, composed here at the top
of the commercial layers. Callers have authorized (``reports.py``)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.wallets import admin as wallet_admin
from workeasy360.commercial.wallets import api as wallet_api
from workeasy360.commercial.wallets.reports import movements as wallet_movement_rows
from workeasy360.platform.organizations.selectors import organization_names


def owner_names(rows: list[dict[str, Any]]) -> dict[str, str]:
    """A display name for each wallet owner in these rows, whichever kind it is."""
    organizations = {
        UUID(row["owner_id"])
        for row in rows
        if row["owner_type"] == wallet_api.ORGANIZATION
    }
    partners = {
        row["owner_id"] for row in rows if row["owner_type"] == wallet_api.PARTNER
    }
    names = {str(k): v for k, v in organization_names(organizations).items()}
    for partner_id in partners:
        partner = partner_selectors.partner_row(UUID(partner_id))
        if partner is not None:
            names[partner_id] = partner.display_name
    return names


def wallet_movements(
    *, start: Any, end: Any, product: Any, search: Any, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    rows, total = wallet_movement_rows(
        start=start, end=end, search=search, limit=limit, offset=offset
    )
    names = owner_names(rows)
    for row in rows:
        row["owner_name"] = names.get(row["owner_id"], "")
    return rows, total


def usage(
    *, start: Any, end: Any, product: Any, search: Any, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    rows = wallet_admin.usage_by_product(start, end)
    names = catalog_selectors.product_names(
        sorted({r["product_code"] for r in rows if r["product_code"]})
    )
    out = []
    for row in rows:
        name = names.get(row["product_code"], row["product_code"] or "No product")
        if product and row["product_code"] != product:
            continue
        if search and search.lower() not in f"{name} {row['meter']}".lower():
            continue
        out.append({**row, "product_name": name})
    return out[offset : offset + limit], len(out)
