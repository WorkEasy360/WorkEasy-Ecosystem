"""Authorized staff reads over the partner and commission modules.

Each function authorizes *here*, in the service layer, so a read reached by any
caller other than the HTTP adapter is checked identically (C7). All are bounded
and keyset-paginated; none returns a secret.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.commissions import accrual, payouts
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.partners import capabilities, domains
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.errors import PartnerRecordNotFound
from workeasy360.contracts.values.cursor import Page
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

PARTNER_VIEW = "platform.staff_partner.view"
COMMISSION_VIEW = "platform.staff_commission.view"


def partners(
    ctx: TenantContext,
    *,
    status: str | None,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> Page:
    authz.require(ctx, PARTNER_VIEW)
    return partner_selectors.partners_page(
        status=status, query=query, limit=limit, cursor=cursor
    )


def partner(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    authz.require(ctx, PARTNER_VIEW)
    detail = partner_selectors.partner_detail(partner_id)
    if detail is None:
        raise PartnerRecordNotFound("No such partner.")
    # The badges in the partner's header (Partner Control Center): computed by
    # the one labelling function from the grants in force, never stored.
    held = {c["capability"] for c in detail["capabilities"] if c["is_effective"]}
    detail["experience"] = capabilities.experience_labels(held)
    # The workspaces the grants open and the one the portal lands on (ADR-047):
    # for the "Change experience" action. A landing is a preference; nothing
    # here grants anything.
    detail["workspaces"] = [
        {"key": key, "label": capabilities.EXPERIENCES[key][0]}
        for key in capabilities.workspaces(held)
    ]
    detail["landing"] = capabilities.resolve_landing(detail["default_experience"], held)
    return detail


def partner_domains_list(ctx: TenantContext, partner_id: UUID) -> list[dict[str, Any]]:
    authz.require(ctx, PARTNER_VIEW)
    return [
        partner_selectors.domain_dict(
            d, txt_name=domains.verification_record_name(d.hostname)
        )
        for d in partner_selectors.domains_for(partner_id)
    ]


def rules_list(ctx: TenantContext, *, live_only: bool) -> list[dict[str, Any]]:
    authz.require(ctx, COMMISSION_VIEW)
    return commission_selectors.rules_list(live_only=live_only)


def accruals(
    ctx: TenantContext,
    *,
    partner_id: UUID | None,
    kind: str | None,
    limit: int,
    cursor: str | None,
    unpaid: bool = False,
) -> Page:
    authz.require(ctx, COMMISSION_VIEW)
    return commission_selectors.accruals_page(
        partner_id=partner_id,
        for_partner=False,
        kind=kind,
        unpaid=unpaid,
        limit=limit,
        cursor=cursor,
    )


def payouts_list(
    ctx: TenantContext,
    *,
    partner_id: UUID | None,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> Page:
    authz.require(ctx, COMMISSION_VIEW)
    return commission_selectors.payouts_page(
        partner_id=partner_id,
        for_partner=False,
        status=status,
        limit=limit,
        cursor=cursor,
    )


def partner_commission(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """What one partner earns: every rule that names it, where each stands now,
    the rule that would govern a sale today, and the version token the next
    assignment must carry (per product scope; ``""`` is "every product")."""
    from django.utils import timezone

    authz.require(ctx, COMMISSION_VIEW)
    if partner_selectors.partner_row(partner_id) is None:
        raise PartnerRecordNotFound("No such partner.")
    now = timezone.now()
    rules = commission_selectors.partner_rules(partner_id, now=now)
    return {
        "partner_earns": "commission.earn"
        in partner_selectors.active_capabilities(partner_id),
        "rules": rules,
        "live_versions": {
            r["product_code"]: r["version"] for r in rules if r["retired_at"] is None
        },
        "in_force": commission_selectors.effective_rule(
            partner_id, product_code="", at=now
        ),
    }


def commission_reconciliation(ctx: TenantContext) -> dict[str, Any]:
    authz.require(ctx, COMMISSION_VIEW)
    return accrual.reconcile().as_dict()


def payout_reconciliation(ctx: TenantContext, payout_id: UUID) -> dict[str, Any]:
    authz.require(ctx, COMMISSION_VIEW)
    return payouts.reconcile_payout(payout_id)
