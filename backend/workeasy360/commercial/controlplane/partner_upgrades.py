"""Approving a partner upgrade: where a request meets the grants (ADR-049).

The request lifecycle lives in ``partners.upgrades``. Approving it is the one act
that changes what a partner may do, so it lives here, above the partner layer,
where the whole profile can be checked. It is a thin sequence over commands that
already exist and already audit themselves:

1. lock the partner, then the request, and require the request to be waiting for
   a decision and the partner to be active;
2. grant the approved bundle and make it the landing, through
   ``experience.transition_experience`` - the same service the Experience panel
   uses. It is **additive**: the affiliate grants stay, because a referral link
   keeps working only while its owner holds ``referral.create`` and an
   affiliate's published links must not die on the day it grows;
3. for White-label, apply the proposed brand and domain through the brand and
   domain commands (the same ones the partner's own portal calls);
4. require the profile to be complete for everything the partner now holds - the
   one rule of ADR-048, not a second one - and roll the whole transaction back
   with the list of what is missing if it is not;
5. stamp the request approved with what changed.

Nothing is granted unless every step passes, and nothing a frontend sends can
reach step 2 without ``platform.staff_partner.manage`` and a verified second
factor (the staff view). No price, fee or amount is stored or charged here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.controlplane import profile_completion
from workeasy360.commercial.partners import (
    branding,
    capabilities,
    domains,
    experience,
    inputs,
    selectors,
    services,
    upgrades,
)
from workeasy360.commercial.partners.domain import clean_text
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import PartnerUpgradeConflict, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope


def _products(row: Any, chosen: Any) -> list[str]:
    """The products to grant: staff's own list when they send one, else what the
    partner asked for. A list of codes either way; never free text."""
    raw = chosen if chosen is not None else (row.proposal or {}).get("products") or []
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        raise PartnerValidationError(
            "products must be a list of product codes.", field="products"
        )
    return list(dict.fromkeys(raw))


def _apply_white_label(
    ctx: TenantContext, partner_id: UUID, proposal: dict[str, Any]
) -> None:
    """The brand and domain the partner proposed, through the commands the
    partner's own portal uses. An existing brand is merged, never wiped: its logo
    and the colours the partner did not change stay."""
    brand = proposal.get("brand") or {}
    if brand.get("brand_name"):
        existing = selectors.brand_for(partner_id)
        theme = dict(existing.theme or {}) if existing is not None else {}
        if brand.get("primary_color"):
            theme["primary"] = brand["primary_color"]
        if brand.get("secondary_color"):
            theme["accent"] = brand["secondary_color"]
        branding.update_brand(
            ctx,
            brand_name=brand["brand_name"],
            accent_color=brand.get("primary_color")
            or (existing.accent_color if existing is not None else ""),
            theme=theme,
            support_email=brand.get("support_email")
            or (existing.support_email if existing is not None else ""),
            support_url=existing.support_url if existing is not None else "",
            logo_file_id=existing.logo_file_id if existing is not None else None,
            expected_version=existing.version if existing is not None else None,
            partner_id=partner_id,
        )
    hostname = (proposal.get("domain") or {}).get("hostname")
    if hostname:
        # A request only: nothing is served until the DNS proof is seen and the
        # domain is separately activated, whoever asked.
        domains.request_domain(ctx, hostname=hostname, partner_id=partner_id)


def _require_ready(partner_id: UUID, status: str) -> None:
    """The profile must be complete for everything the partner now holds."""
    result = profile_completion.for_partners(
        [partner_id], timezone.now(), statuses={partner_id: status}
    )[partner_id]
    if result["complete"]:
        return
    missing = result["missing"]
    raise PartnerValidationError(
        "Not ready to approve - still needed: "
        + "; ".join(f"{m['label'].lower()} ({m['detail'].rstrip('.')})" for m in missing)
        + ".",
        field=missing[0]["key"],
        missing=[m["key"] for m in missing],
    )


@in_scope
def approve(
    ctx: TenantContext,
    partner_id: UUID,
    request_id: UUID,
    *,
    products: Any = None,
    commercial_reference: Any = "",
    note: Any = "",
) -> dict[str, Any]:
    """Approve a submitted upgrade: grant the bundle, make it the landing, apply
    the brand and domain, check the profile, stamp the request - or change
    nothing at all."""
    authz.require(ctx, upgrades.STAFF_MANAGE)
    reference = clean_text(
        commercial_reference, field="commercial_reference", max_length=120, required=False
    )
    text = inputs.clean_note(note, field="note", max_length=upgrades.MAX_NOTE)
    with transaction.atomic():
        partner = services._lock_partner(partner_id)
        if partner.status != PartnerStatus.ACTIVE.value:
            raise PartnerUpgradeConflict(
                "Only an active partner can be upgraded.", partner_status=partner.status
            )
        row = upgrades.lock_for_decision(
            partner_id, request_id, allowed=(upgrades.PENDING_APPROVAL,)
        )
        target = row.to_experience
        codes = _products(row, products)
        moved = experience.transition_experience(
            ctx,
            partner_id,
            to=target,
            products=codes,
            reason=f"Upgrade approved: {row.from_experience or 'no experience'} to {target}",
        )
        if target == capabilities.WHITE_LABEL:
            _apply_white_label(ctx, partner_id, row.proposal or {})
        _require_ready(partner_id, partner.status)
        return upgrades.record_approval(
            ctx,
            row,
            note=text,
            commercial_reference=reference,
            added=moved.capabilities_granted,
            removed=moved.capabilities_revoked,
            products=codes,
        )
