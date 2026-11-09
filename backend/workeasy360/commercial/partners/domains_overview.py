"""Every partner's domains and brands, for Settings > Domains & Apps. Caller authorizes."""

from __future__ import annotations

from typing import Any

from workeasy360.commercial.partners.models import PartnerBrand, PartnerDomain


def partner_domains(*, limit: int = 200) -> list[dict[str, Any]]:
    rows = PartnerDomain.objects.select_related("partner").order_by("hostname")[:limit]
    return [
        {
            "id": str(row.id),
            "hostname": row.hostname,
            "status": row.status,
            "partner_id": str(row.partner_id),
            "partner_name": row.partner.display_name,
            "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        }
        for row in rows
    ]


def partner_brands(*, limit: int = 200) -> list[dict[str, Any]]:
    rows = PartnerBrand.objects.select_related("partner").order_by("partner__display_name")[:limit]
    return [
        {
            "partner_id": str(row.partner_id),
            "partner_name": row.partner.display_name,
            "brand_name": row.brand_name,
            "has_logo": row.logo_file_id is not None,
        }
        for row in rows
    ]
