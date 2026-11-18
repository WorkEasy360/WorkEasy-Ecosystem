"""The Wallets & Credits screens: the ledger's rows with their owners' names.

Composes ``wallets`` (which knows no names) with organizations and partners. Every read
and write is authorized by the wallets service it calls.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.controlplane.report_sources import owner_names
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.wallets import admin as wallets
from workeasy360.commercial.wallets import api as wallet_api
from workeasy360.contracts.errors import CatalogValidationError, CommercialRecordNotFound
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.organizations import selectors as org_selectors


def _named(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = owner_names(rows)
    return [{**row, "owner_name": names.get(row["owner_id"], "")} for row in rows]


def _owners_matching(search: str) -> list[UUID]:
    customers = org_selectors.organizations_directory_page(search=search, limit=100).items
    partners = partner_selectors.partners_page(query=search, limit=100).items
    return [UUID(row["organization_id"]) for row in customers] + [
        UUID(row["id"]) for row in partners
    ]


def list_wallets(
    ctx: TenantContext,
    *,
    kind: Any = None,
    owner_type: Any = None,
    search: Any = None,
    low: bool = False,
    limit: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    term = " ".join(str(search or "").split())[:80]
    page = wallets.wallets(
        ctx,
        kind=kind,
        owner_type=owner_type,
        owner_ids=_owners_matching(term) if term else None,
        low=low,
        limit=limit,
        cursor=cursor,
    )
    return {**page, "results": _named(page["results"])}


def wallet_detail(ctx: TenantContext, wallet_id: UUID) -> dict[str, Any]:
    row = wallets.wallet(ctx, wallet_id)
    return _named([row])[0]


def list_entries(ctx: TenantContext, **filters: Any) -> dict[str, Any]:
    page = wallets.entries(ctx, **filters)
    return {**page, "results": _named(page["results"])}


def list_events(ctx: TenantContext, **filters: Any) -> dict[str, Any]:
    page = wallets.events(ctx, **filters)
    names = org_selectors.organization_names(
        {UUID(r["organization_id"]) for r in page["results"]}
    )
    return {
        **page,
        "results": [
            {**row, "organization_name": names.get(UUID(row["organization_id"]), "")}
            for row in page["results"]
        ],
    }


def open_wallet(
    ctx: TenantContext, *, owner_type: Any, owner_id: Any, kind: Any, currency: Any
) -> dict[str, Any]:
    """Open a wallet for an owner that really exists. Authorized before the owner is looked
    up, so the answer cannot say whether someone exists to a person who may not ask."""
    authz.require(ctx, "platform.staff_wallet.manage")
    try:
        owner = UUID(str(owner_id))
    except (TypeError, ValueError) as exc:
        raise CatalogValidationError("Choose an owner.", field="owner_id") from exc
    if owner_type == wallet_api.PARTNER:
        if partner_selectors.partner_row(owner) is None:
            raise CommercialRecordNotFound("No such partner.")
    elif not org_selectors.organization_names({owner}):
        raise CommercialRecordNotFound("No such customer.")
    row = wallets.open_wallet(
        ctx, owner_type=owner_type, owner_id=owner, kind=kind, currency=currency
    )
    return _named([row])[0]
