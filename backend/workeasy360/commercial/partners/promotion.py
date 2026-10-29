"""Which products a partner may promote with referral links (ADR-054).

**Promote is not sell and not use.** ``PartnerProductGrant`` lets a partner
*sell* a product; an entitlement lets a customer *use* one. A promotion grant
lets a partner make referral links for a product, and nothing else: it moves
no money, shows no customer and grants no access.

Only staff with ``platform.staff_partner.manage`` (Super Admin, or a staff
member given that one grant) assign or revoke, and only for a partner holding
the referral capability and a product open to the affiliate channel. The
check that matters is on the server: ``referrals.create_referral_code``
refuses a product without an active grant, whatever a screen shows.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.services import MANAGE, _lock_partner
from workeasy360.contracts.enums import GrantStatus, PartnerStatus
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW = "platform.staff_partner.view"


def promotable_codes(partner_id: UUID) -> frozenset[str]:
    """The product codes this partner holds an active promotion grant for."""
    from workeasy360.commercial.partners.models import PartnerPromotionGrant

    return frozenset(
        PartnerPromotionGrant.objects.filter(
            partner_id=partner_id, status=GrantStatus.ACTIVE.value
        ).values_list("product__code", flat=True)
    )


def may_promote(partner_id: UUID, product_code: str) -> bool:
    from workeasy360.commercial.partners.models import PartnerPromotionGrant

    return PartnerPromotionGrant.objects.filter(
        partner_id=partner_id,
        product__code=str(product_code),
        status=GrantStatus.ACTIVE.value,
    ).exists()


def grants(partner_id: UUID) -> list[dict[str, Any]]:
    """Every promotion grant of one partner, live first then newest - the
    history of what it could promote and when."""
    from workeasy360.commercial.partners.models import PartnerPromotionGrant

    rows = (
        PartnerPromotionGrant.objects.filter(partner_id=partner_id)
        .select_related("product")
        .order_by("status", "-granted_at")
    )
    return [
        {
            "id": str(g.id),
            "product_code": g.product.code,
            "product_name": g.product.name,
            "status": g.status,
            "granted_at": g.granted_at.isoformat(),
            "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
        }
        for g in rows
    ]


@in_scope
def list_grants(ctx: TenantContext, partner_id: UUID) -> list[dict[str, Any]]:
    authz.require(ctx, VIEW)
    return grants(partner_id)


@in_scope
def grant_promotion(ctx: TenantContext, partner_id: UUID, *, product_code: Any) -> Any:
    """Let a partner promote a product. Idempotent: granting a product the
    partner already holds returns the live grant."""
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.partners.models import PartnerPromotionGrant

    authz.require(ctx, MANAGE)
    code = str(product_code or "").strip()
    product_id, _plan = catalog.resolve_product_and_plan(code, None)
    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if partner.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot be granted anything.", field="partner_id"
            )
        if capabilities.REFERRAL_CREATE not in partner_selectors.active_capabilities(
            partner_id
        ):
            raise PartnerValidationError(
                "This partner does not hold the referral capability, so it cannot "
                "promote products.",
                field="partner_id",
            )
        if "affiliate" not in catalog.channels_open(code):
            raise PartnerValidationError(
                "This product is not open to affiliates. Allow Affiliate in the "
                "product's Availability first.",
                field="product_code",
            )
        existing = (
            PartnerPromotionGrant.objects.select_for_update()
            .filter(
                partner_id=partner_id,
                product_id=product_id,
                status=GrantStatus.ACTIVE.value,
            )
            .first()
        )
        if existing is not None:
            return existing
        grant = PartnerPromotionGrant.objects.create(
            partner_id=partner_id,
            product_id=product_id,
            granted_by_id=ctx.user_id,
            granted_at=timezone.now(),
        )
        audit.record(
            ctx,
            "partner.promotion_granted",
            resource_type="partners.promotion_grant",
            resource_id=grant.id,
            changes={"partner_id": str(partner_id), "product_code": code},
        )
    return grant


@in_scope
def revoke_promotion(ctx: TenantContext, partner_id: UUID, *, grant_id: UUID) -> Any:
    """Withdraw a promotion grant. New links for the product are refused at
    once; existing links follow the programme's ``revoked_links`` policy.
    Referrals and commission already recorded are untouched."""
    from workeasy360.commercial.partners.models import PartnerPromotionGrant

    authz.require(ctx, MANAGE)
    with transaction.atomic():
        _lock_partner(partner_id)
        grant = (
            PartnerPromotionGrant.objects.select_for_update()
            .select_related("product")
            .filter(id=grant_id, partner_id=partner_id, status=GrantStatus.ACTIVE.value)
            .first()
        )
        if grant is None:
            raise PartnerRecordNotFound(
                "No such promotion grant.", grant_id=str(grant_id)
            )
        grant.status = GrantStatus.REVOKED.value
        grant.revoked_at = timezone.now()
        grant.revoked_by_id = ctx.user_id
        grant.save(update_fields=["status", "revoked_at", "revoked_by"])
        audit.record(
            ctx,
            "partner.promotion_revoked",
            resource_type="partners.promotion_grant",
            resource_id=grant.id,
            changes={"partner_id": str(partner_id), "product_code": grant.product.code},
        )
    return grant
