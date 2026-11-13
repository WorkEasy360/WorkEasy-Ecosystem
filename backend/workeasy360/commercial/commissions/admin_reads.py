"""The Admin console's reports on earnings. Commission entries and payouts are earnings
records: nothing here is, or touches, a spendable balance. Callers have authorized
(``controlplane/reports.py``). Names are looked up once per page."""

from __future__ import annotations

from typing import Any

from django.db.models import Q

from workeasy360.commercial.commissions.models import CommissionAccrual, Payout, Referral
from workeasy360.platform.organizations.selectors import organization_names


def _partner_names(ids: set[Any]) -> dict[Any, str]:
    from workeasy360.commercial.partners.selectors import partner_names

    return partner_names(ids)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _partners_matching(search: str) -> Any:
    from workeasy360.commercial.partners.selectors import partner_ids_matching

    return partner_ids_matching(search)


def report_conversions(
    *, start: Any, end: Any, product: Any, search: Any, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    rows = Referral.objects.filter(converted_at__gte=start, converted_at__lt=end)
    if search:
        rows = rows.filter(partner_id__in=_partners_matching(search))
    total = rows.count()
    page = list(rows.order_by("-converted_at")[offset : offset + limit])
    partners = _partner_names({r.partner_id for r in page})
    customers = organization_names({r.organization_id for r in page})
    return (
        [
            {
                "id": str(r.id),
                "partner_id": str(r.partner_id),
                "partner_name": partners.get(r.partner_id, ""),
                "organization_id": str(r.organization_id) if r.organization_id else None,
                "organization_name": customers.get(r.organization_id, ""),
                "status": r.status,
                "captured_at": _iso(r.captured_at),
                "registered_at": _iso(r.registered_at),
                "converted_at": _iso(r.converted_at),
            }
            for r in page
        ],
        total,
    )


def report_commissions(
    *, start: Any, end: Any, product: Any, search: Any, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    rows = CommissionAccrual.objects.filter(created_at__gte=start, created_at__lt=end)
    if product:
        rows = rows.filter(product_code=product)
    if search:
        rows = rows.filter(
            Q(partner_id__in=_partners_matching(search))
            | Q(reason_code__icontains=search)
        )
    total = rows.count()
    page = list(rows.order_by("-created_at")[offset : offset + limit])
    partners = _partner_names({r.partner_id for r in page})
    return (
        [
            {
                "id": str(r.id),
                "partner_id": str(r.partner_id),
                "partner_name": partners.get(r.partner_id, ""),
                "kind": r.kind,
                "earning": r.earning,
                "amount_minor": r.amount_minor,
                "currency": r.currency,
                "clears_at": _iso(r.clears_at),
                "reason_code": r.reason_code,
                "reverses": str(r.reverses_id) if r.reverses_id else None,
                "created_at": _iso(r.created_at),
            }
            for r in page
        ],
        total,
    )


def report_payouts(
    *, start: Any, end: Any, product: Any, search: Any, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    rows = Payout.objects.filter(created_at__gte=start, created_at__lt=end)
    if search:
        rows = rows.filter(
            Q(partner_id__in=_partners_matching(search))
            | Q(paid_reference__icontains=search)
        )
    total = rows.count()
    page = list(rows.order_by("-created_at")[offset : offset + limit])
    partners = _partner_names({r.partner_id for r in page})
    return (
        [
            {
                "id": str(r.id),
                "partner_id": str(r.partner_id),
                "partner_name": partners.get(r.partner_id, ""),
                "status": r.status,
                "amount_minor": r.payable_minor,
                "currency": r.currency,
                "external_reference": r.paid_reference,
                "paid_at": _iso(r.paid_at),
                "created_at": _iso(r.created_at),
            }
            for r in page
        ],
        total,
    )
