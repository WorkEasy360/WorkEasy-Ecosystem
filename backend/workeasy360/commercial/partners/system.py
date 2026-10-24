"""Partner commands for an operator's own tooling (ADR-045).

``create_demo_partner`` is not a staff member holding ``platform.staff_partner.manage``;
it is an operator command run in a development terminal. These two functions are the
part of "create a partner and activate it" it needs that the staff commands gate behind
a staff grant, kept in the partners package (which owns the tables) rather than reaching
into them from the command. Nothing here is reachable over HTTP: no view imports this
module (pinned by ``tests/partner_panel/test_architecture.py``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.partners import services
from workeasy360.commercial.partners.models import PartnerAccount
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit


def ensure_partner(
    ctx: TenantContext,
    *,
    code: str,
    display_name: str,
    legal_name: str,
    contact_name: str,
    contact_email: str,
    payout_currency: str,
) -> tuple[Any, bool]:
    """The partner with this code, creating it (in draft) if there is none. Audited."""
    partner = PartnerAccount.objects.filter(code=code).first()
    if partner is not None:
        return partner, False
    partner = PartnerAccount.objects.create(
        code=code,
        display_name=display_name,
        legal_name=legal_name,
        contact_name=contact_name,
        contact_email=contact_email,
        payout_currency=payout_currency,
        created_by_id=ctx.user_id,
    )
    audit.record(
        ctx,
        "partner.created",
        resource_type="partners.partner",
        resource_id=partner.id,
        changes={"code": partner.code, "demo": True},
    )
    return partner, True


def activate(ctx: TenantContext, partner_id: UUID, *, reason: str) -> Any:
    """Draft to active, without a staff grant. Audited. A no-op for a partner
    that is not a draft."""
    with transaction.atomic():
        locked = services._lock_partner(partner_id)
        if locked.status_enum is not PartnerStatus.DRAFT:
            return locked
        locked.status = PartnerStatus.ACTIVE.value
        locked.status_reason = reason
        locked.activated_at = timezone.now()
        services._bump(locked, ["status", "status_reason", "activated_at"])
        audit.record(
            ctx,
            "partner.active",
            resource_type="partners.partner",
            resource_id=locked.id,
            changes={"from": "draft", "to": "active", "demo": True},
            reason=reason,
        )
    return locked
