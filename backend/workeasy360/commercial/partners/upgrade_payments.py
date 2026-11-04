"""Paying an upgrade's set-up fee online (2026-11-03).

The partner chose online payment on the upgrade's Review step. This opens the
provider's hosted checkout for exactly the fee the request fixed when it
opened, and remembers the payment on the request. It pays nothing and submits
nothing: the provider's verified callback does both, through
``payments.partner_fees`` -> ``upgrades.fee_paid`` (registered at app start).

Kept apart from ``upgrades`` on purpose: the request module owns the request
table and never talks to a payment provider; this module talks to
``payments.partner_fees`` and asks ``upgrades`` to read and write the request.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.partners import capabilities, upgrades
from workeasy360.commercial.payments import partner_fees
from workeasy360.contracts.values import portals
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.identity import portals as portal_links

#: The workspace address a partner is in, by the experience it upgrades from.
_WORKSPACE = {capabilities.AFFILIATE: "affiliate", capabilities.RESELLER: "reseller"}
_TARGET = {capabilities.RESELLER: "reseller", capabilities.WHITE_LABEL: "white-label"}


def pay_online(
    ctx: TenantContext, request_id: UUID, *, expected_version: Any, email: str
) -> dict[str, Any]:
    """Open (or return the one already open) hosted checkout for the request's
    set-up fee. The browser is sent to ``payment.checkout_url``; it comes back
    to the upgrade page, which shows what the provider has confirmed."""
    terms = upgrades.online_payment_terms(
        ctx, request_id, expected_version=expected_version
    )
    workspace = _WORKSPACE.get(terms["from_experience"], "affiliate")
    target = _TARGET.get(terms["to_experience"], "reseller")
    payment = partner_fees.start(
        purpose=partner_fees.PARTNER_UPGRADE,
        subject_id=terms["request_id"],
        partner_id=terms["partner_id"],
        amount_minor=terms["amount_minor"],
        currency=terms["currency"],
        email=email,
        user_id=ctx.user_id,
        description=(
            "Workeasy360 partner upgrade to "
            f"{upgrades.EXPERIENCE_LABEL.get(terms['to_experience'], 'a new level')}"
        ),
        return_url=(
            f"{portal_links.origin(portals.PARTNER)}/partner/{workspace}/upgrade/{target}"
        ),
    )
    request = upgrades.attach_online_payment(ctx, request_id, UUID(payment["id"]))
    return {"request": request, "payment": payment}
