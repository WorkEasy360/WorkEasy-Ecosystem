"""A partner's experience: which workspace it opens first, and changing it later.

**An experience is a label and a landing, not a kind of account** (ADR-047). The
three experiences - Reseller, White-label, Affiliate - are what a partner's
capability grants *earn* (``capabilities.EXPERIENCES``, derived on every read);
this module adds the two things that sit on top of that and nothing more:

- :func:`set_default_experience` records which of the workspaces the grants
  already open the portal lands on - a presentation preference, validated
  against the grants and read by no authorization decision;
- :func:`grant_experience`, :func:`retire_experience` and
  :func:`transition_experience` are the one explicit **capability transition
  service** for moving a partner between experiences after creation.

A transition is made of the ordinary ``grant_capability`` / ``revoke_capability``
/ ``grant_product`` commands, each with its own audit entry and event, plus one
summary entry here. Three rules keep it from losing anything:

- it is **additive by default**: giving a partner the Reseller experience never
  removes what its Affiliate experience held;
- history is never deleted: a revoked grant is stamped and kept, and nothing in
  the referral, ledger or payout tables is read or written here;
- the money screens survive: ``commission.earn``, ``commission.view`` and
  ``payout.view`` sit in all three bundles, so retiring an experience while
  another remains never takes a partner's view of what it is owed away - and the
  last experience cannot be retired at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.db import transaction

from workeasy360.commercial.partners import capabilities, selectors, services
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

MANAGE = services.MANAGE

#: Never revoked by retiring an experience: they are how a partner sees money it
#: has earned, and every bundle holds them.
_MONEY: frozenset[str] = frozenset(
    {
        capabilities.COMMISSION_EARN,
        capabilities.COMMISSION_VIEW,
        capabilities.PAYOUT_VIEW,
    }
)

#: Experiences that sell, and so may be given products to sell. The affiliate
#: bundle holds none of the keys that make a partner a seller.
_SELLING: frozenset[str] = frozenset({capabilities.RESELLER, capabilities.WHITE_LABEL})


@dataclass(frozen=True, slots=True)
class Transition:
    """What a transition changed."""

    experience: str
    capabilities_granted: list[str]
    capabilities_revoked: list[str]
    products_granted: list[str]
    default_experience: str
    #: Product grants ended because no selling experience is left. The grant rows
    #: are stamped revoked and kept; customers and their subscriptions are untouched.
    products_revoked: list[str] = field(default_factory=list)


def _editable_partner(partner_id: UUID) -> Any:
    partner = services._lock_partner(partner_id)
    if partner.status_enum is PartnerStatus.TERMINATED:
        raise PartnerValidationError(
            "A terminated partner cannot be changed.", field="partner_id"
        )
    return partner


@in_scope
def set_default_experience(
    ctx: TenantContext, partner_id: UUID, *, experience: Any, reason: Any = ""
) -> Any:
    """Choose which workspace the partner's portal opens first.

    Refused unless the partner's grants **already earn** that workspace: the
    preference selects among workspaces and opens none (grant the experience
    first). Idempotent. Audited as a change of presentation.
    """
    authz.require(ctx, MANAGE)
    key = capabilities.require_experience(experience)
    why = _reason(reason)
    with transaction.atomic():
        partner = _editable_partner(partner_id)
        held = selectors.active_capabilities(partner_id)
        if key not in capabilities.workspaces(held):
            raise PartnerValidationError(
                "This partner does not hold the capabilities of that experience. "
                "Give it the experience first.",
                field="experience",
            )
        _set_default(ctx, partner, key, why)
    return partner


def _set_default(ctx: TenantContext, partner: Any, key: str, why: str) -> bool:
    if partner.default_experience == key:
        return False
    before = partner.default_experience
    partner.default_experience = key
    services._bump(partner, ["default_experience"])
    audit.record(
        ctx,
        "partner.default_experience_changed",
        resource_type="partners.partner",
        resource_id=partner.id,
        changes={"partner_id": str(partner.id), "from": before, "to": key},
        reason=why,
    )
    return True


def _reason(reason: Any) -> str:
    from workeasy360.commercial.partners.domain import clean_reason

    return clean_reason(reason or "Experience changed by Workeasy360 staff")


@in_scope
def grant_experience(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    experience: Any,
    reason: Any = "",
    make_default: bool = False,
    products: Any = (),
) -> Transition:
    """Give a partner the capabilities of an experience (and, for a selling one,
    products to sell). Additive and idempotent: whatever it already holds is left
    alone, so a retry after a lost response changes nothing."""
    authz.require(ctx, MANAGE)
    key = capabilities.require_experience(experience)
    why = _reason(reason)
    codes = _product_codes(key, products)
    with transaction.atomic():
        partner = _editable_partner(partner_id)
        result = _grant(ctx, partner, key, why, codes, make_default)
    return result


def _product_codes(key: str, products: Any) -> list[str]:
    if products and not isinstance(products, list | tuple):
        # A string would be split into letters and a number would crash.
        raise PartnerValidationError(
            "products must be a list of product codes.", field="products"
        )
    raw = list(products or ())
    if not all(isinstance(code, str) for code in raw):
        raise PartnerValidationError(
            "products must be a list of product codes.", field="products"
        )
    codes = list(dict.fromkeys(raw))
    if codes and key not in _SELLING:
        raise PartnerValidationError(
            "Only a selling experience can be given products to sell. An "
            "affiliate promotes products; it is not allowed to sell them.",
            field="products",
        )
    return codes


def _grant(
    ctx: TenantContext,
    partner: Any,
    key: str,
    why: str,
    codes: list[str],
    make_default: bool,
) -> Transition:
    held = selectors.active_capabilities(partner.id)
    granted: list[str] = []
    for capability in capabilities.experience_bundle(key):
        if capability not in held:
            services._grant_capability(ctx, partner.id, capability=capability, reason=why)
            granted.append(capability)
    for code in codes:
        services._grant_product(ctx, partner.id, product_code=code)
    if make_default:
        _set_default(ctx, partner, key, why)
    audit.record(
        ctx,
        "partner.experience_granted",
        resource_type="partners.partner",
        resource_id=partner.id,
        changes={
            "partner_id": str(partner.id),
            "experience": key,
            "capabilities": granted,
            "products": codes,
            "default": bool(make_default),
        },
        reason=why,
    )
    return Transition(
        experience=key,
        capabilities_granted=granted,
        capabilities_revoked=[],
        products_granted=codes,
        default_experience=partner.default_experience,
    )


@in_scope
def retire_experience(
    ctx: TenantContext, partner_id: UUID, *, experience: Any, reason: Any = ""
) -> Transition:
    """Withdraw the capabilities only ``experience`` needs.

    Capabilities another held experience's bundle also contains stay, and the
    money capabilities always stay, so a partner keeps its view of what it has
    earned. Retiring Reseller from a partner that also wears White-label therefore
    withdraws only what White-label does not need (the sub-partner capability):
    White-label contains the rest. The last remaining experience cannot be
    retired. Referrals, commissions, payouts and customers are untouched: nothing
    here reads them.
    """
    authz.require(ctx, MANAGE)
    key = capabilities.require_experience(experience)
    why = _reason(reason)
    with transaction.atomic():
        partner = _editable_partner(partner_id)
        result = _retire(ctx, partner, key, why)
    return result


def _retire(ctx: TenantContext, partner: Any, key: str, why: str) -> Transition:
    held = selectors.active_capabilities(partner.id)
    # The experiences the grants earn - every label, not the workspaces. White-label
    # contains the Reseller workspace, so a partner wearing both has one workspace
    # but two experiences: counting only workspaces would forget the Reseller set
    # and withdraw it along with the White-label one.
    wearing = [item["key"] for item in capabilities.experience_labels(held)]
    if key not in wearing:
        raise PartnerValidationError(
            "This partner does not wear that experience.", field="experience"
        )
    if len(wearing) < 2:
        raise PartnerValidationError(
            "This is the partner's only experience. Add another before retiring it.",
            field="experience",
        )
    kept: set[str] = set(_MONEY)
    for other in wearing:
        if other != key:
            kept.update(capabilities.experience_bundle(other))
    revoked: list[str] = []
    for capability in capabilities.experience_bundle(key):
        if capability in held and capability not in kept:
            services._revoke_capability(
                ctx, partner.id, capability=capability, reason=why
            )
            revoked.append(capability)
    # A stored preference for a workspace the grants no longer open would only
    # be resolved away on every read; move it to the first workspace left.
    now_held = selectors.active_capabilities(partner.id)
    after = capabilities.workspaces(now_held)
    if after and partner.default_experience and partner.default_experience not in after:
        _set_default(ctx, partner, after[0], why)
    # Products are sold, not promoted: once no selling experience is left there is
    # nothing the grants would authorize, so they end (stamped, never deleted) and
    # the partner's own product screens stop listing them. Existing customers and
    # subscriptions are not touched.
    still_sells = _SELLING & {
        item["key"] for item in capabilities.experience_labels(now_held)
    }
    ended: list[str] = []
    if not still_sells:
        for grant in selectors.granted_products(partner.id):
            services._revoke_product_grant(
                ctx, partner.id, grant_id=UUID(grant["grant_id"])
            )
            ended.append(grant["product_code"])
    audit.record(
        ctx,
        "partner.experience_retired",
        resource_type="partners.partner",
        resource_id=partner.id,
        changes={
            "partner_id": str(partner.id),
            "experience": key,
            "capabilities_revoked": revoked,
            "products_revoked": ended,
        },
        reason=why,
    )
    return Transition(
        experience=key,
        capabilities_granted=[],
        capabilities_revoked=revoked,
        products_granted=[],
        default_experience=partner.default_experience,
        products_revoked=ended,
    )


@in_scope
def transition_experience(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    to: Any,
    retire: Any = None,
    products: Any = (),
    reason: Any = "",
) -> Transition:
    """Move a partner to ``to``: give it that experience, make it the landing,
    optionally give it products, and optionally retire another experience - in
    one transaction, so a partner is never left half-way between the two."""
    authz.require(ctx, MANAGE)
    key = capabilities.require_experience(to)
    away = capabilities.require_experience(retire) if retire else None
    if away == key:
        raise PartnerValidationError(
            "An experience cannot replace itself.", field="retire"
        )
    why = _reason(reason)
    codes = _product_codes(key, products)
    with transaction.atomic():
        partner = _editable_partner(partner_id)
        granted = _grant(ctx, partner, key, why, codes, True)
        revoked: list[str] = []
        ended: list[str] = []
        if away is not None:
            retired = _retire(ctx, partner, away, why)
            revoked, ended = retired.capabilities_revoked, retired.products_revoked
        audit.record(
            ctx,
            "partner.experience_changed",
            resource_type="partners.partner",
            resource_id=partner.id,
            changes={
                "partner_id": str(partner.id),
                "to": key,
                "retired": away,
                "capabilities_granted": granted.capabilities_granted,
                "capabilities_revoked": revoked,
                "products_revoked": ended,
            },
            reason=why,
        )
    return Transition(
        experience=key,
        capabilities_granted=granted.capabilities_granted,
        capabilities_revoked=revoked,
        products_granted=codes,
        default_experience=partner.default_experience,
        products_revoked=ended,
    )
