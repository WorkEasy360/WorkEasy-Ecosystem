"""What partners are told by email about their KYC application."""

from __future__ import annotations

from typing import Final
from uuid import UUID, uuid4

from workeasy360.platform.notifications.templates import Template, register

TEMPLATE_KEY: Final = "platform.partner.kyc_decided"

_WORDS: Final = {
    "approved": "approved",
    "correction_required": "returned for corrections",
    "rejected": "not approved",
}

KYC_DECIDED = register(
    Template(
        key=TEMPLATE_KEY,
        subject="Your partner application was {outcome}",
        body_text=(
            "Your WorkEasy360 partner application for {partner_name} was {outcome}.\n\n"
            "{note}\n\n"
            "Sign in to your partner portal to see the details."
        ),
    )
)


def kyc_decided(partner_id: UUID, target: str, reason: str) -> int:
    """Email the partner's administrators. Returns how many were queued."""
    from workeasy360.commercial.partners import capabilities
    from workeasy360.commercial.partners.models import PartnerAccount, PartnerMember
    from workeasy360.platform.identity.selectors import emails_for_users
    from workeasy360.platform.notifications.services import notify_account

    partner = PartnerAccount.objects.filter(id=partner_id).first()
    if partner is None:
        return 0
    people = PartnerMember.objects.filter(
        partner_id=partner_id, status="active", role__in=sorted(capabilities.ADMIN_ROLES)
    ).values_list("user_id", flat=True)
    addresses = emails_for_users(list(people))
    sent = 0
    for user_id, email in addresses.items():
        notify_account(
            template_key=TEMPLATE_KEY,
            recipient_email=email,
            context={
                "partner_name": partner.display_name,
                "outcome": _WORDS.get(target, target),
                "note": reason or "No further action is needed.",
            },
            idempotency_key=f"partner-kyc:{partner_id}:{user_id}:{uuid4().hex}",
        )
        sent += 1
    return sent
