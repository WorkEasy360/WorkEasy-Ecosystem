"""A person applying to become a partner, from the public form (owner decision
2026-11-09, reversing "partners are only created by staff" in ADR-047/048).

**Applying grants nothing.** The partner is a draft, which holds no permission at all
(``resolver.py``); no capability is granted and no product is offered. What the person
asked to become is only remembered on the application. The account is ``pending``
until the address is confirmed, like any signup.

**The answer never says whether the address is registered.** The HTTP layer returns
the same thing for every outcome, as signup does. An address that already has an
account is told so by email, and nothing is created for it.

Called only by ``controlplane.partner_applications``. It takes no acting context
because there is no actor yet: the applicant is who the transaction creates.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerContact,
    PartnerKyc,
    PartnerMember,
    PartnerProfile,
)
from workeasy360.contracts.enums import PartnerMemberRole
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz.control_plane import acting_scope, platform_context


def create_draft_for_applicant(
    *,
    user: Any,
    code: str,
    business_name: str,
    contact_name: str,
    contact_email: str,
    country: str,
    phone: str,
    payout_currency: str,
    requested_experience: str,
) -> Any:
    """The draft partner, its one administrator and its application, in the
    caller's transaction."""
    partner = PartnerAccount.objects.create(
        code=code,
        display_name=business_name,
        legal_name=business_name,
        contact_name=contact_name,
        contact_email=contact_email,
        payout_currency=payout_currency,
        created_by_id=user.pk,
    )
    PartnerProfile.objects.create(
        partner=partner,
        business_email=contact_email,
        phone=phone,
        country=country,
        updated_by_id=user.pk,
    )
    PartnerContact.objects.create(
        partner=partner,
        kind="primary",
        name=contact_name,
        email=contact_email,
        phone=phone,
        updated_by_id=user.pk,
    )
    PartnerMember.objects.create(
        partner=partner,
        user_id=user.pk,
        role=PartnerMemberRole.ADMIN.value,
        added_by_id=user.pk,
        added_at=timezone.now(),
    )
    PartnerKyc.objects.create(
        partner=partner,
        source="application",
        requested_experience=requested_experience,
        details={"legal_name": business_name, "country": country, "phone": phone},
    )
    # The applicant is the only actor there is: the audit trail names them.
    actor = platform_context(user_id=user.pk)
    with acting_scope(actor):
        audit.record(
            actor,
            "partner.applied",
            resource_type="partners.partner",
            resource_id=partner.id,
            # Kinds only: no name and no address in the audit trail.
            changes={"requested_experience": requested_experience, "source": "public_form"},
            reason="A person applied through the public partner form.",
        )
    return partner
