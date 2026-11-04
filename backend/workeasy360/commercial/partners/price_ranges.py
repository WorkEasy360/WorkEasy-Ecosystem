"""Super Admin's price ranges over partner pricing (ADR-056).

The owner's rule: *a white-label partner may change customer-facing pricing
only when Super Admin explicitly allows it, and only inside the range Super
Admin set* - CRM Basic between INR 500 and INR 900 lets the partner choose 699
but never 100 or 1,500. The same applies to Resellers.

Two halves, one boundary:

- **Staff** set a range per programme (Reseller / White-label), plan, billing
  cycle and currency, with the switch ``partner_may_set``
  (``platform.staff_pricing.manage``). Each change is audited with the value it
  replaced.
- **A partner** sets its own customer price for a plan it may sell
  (``platform.partner_product.manage``, an active partner holding the sell
  capability). The amount must lie in the range of the programme it sells under
  (``selectors.selling_experience_at``, the same rule its margin uses). The
  price is published into the partner's own price book through
  ``pricing.publish_own_partner_price`` - the ordinary price rules: immutable,
  superseded not edited, never backdated - and ``resolve_price`` then charges it
  to the partner's customers exactly like a staff-set partner price.

Nothing here computes a price or a margin; it only decides whether a partner's
chosen amount is allowed.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.partners import capabilities, selectors
from workeasy360.commercial.partners.access import require_transacting
from workeasy360.contracts.enums import BillingInterval
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

STAFF_MANAGE: Final = "platform.staff_pricing.manage"
STAFF_VIEW: Final = "platform.staff_catalog.view"
PARTNER_MANAGE: Final = "platform.partner_product.manage"
PARTNER_VIEW: Final = "platform.partner_pricing.view"
EXPERIENCES: Final = (capabilities.RESELLER, capabilities.WHITE_LABEL)
#: The same ceiling the staff partner-price command uses.
MAX_MINOR: Final = 10**12


def _experience(value: Any) -> str:
    exp = str(value or "").strip()
    if exp not in EXPERIENCES:
        raise PartnerValidationError(
            "experience is reseller or white_label.", field="experience"
        )
    return exp


def _interval(value: Any) -> BillingInterval:
    try:
        cycle = BillingInterval(str(value))
    except ValueError as exc:
        raise PartnerValidationError(
            "interval is not a supported billing cycle.", field="interval"
        ) from exc
    if cycle is BillingInterval.ONE_TIME:
        raise PartnerValidationError(
            "A price range is for a recurring billing cycle.", field="interval"
        )
    return cycle


def _currency(value: Any) -> CurrencyCode:
    try:
        return CurrencyCode(str(value or "").upper())
    except ValueError as exc:
        raise PartnerValidationError(
            "currency is not supported.", field="currency"
        ) from exc


def _amount(value: Any, field: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_MINOR
    ):
        raise PartnerValidationError(
            f"{field} must be a whole number of minor units, zero or more.",
            field=field,
        )
    return value


def range_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "experience": row.experience,
        "plan_id": str(row.plan_id),
        "interval": row.interval,
        "currency": row.currency,
        "min_minor": row.min_minor,
        "max_minor": row.max_minor,
        "partner_may_set": row.partner_may_set,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _terms(row: Any) -> dict[str, Any]:
    return {
        "min_minor": row.min_minor,
        "max_minor": row.max_minor,
        "partner_may_set": row.partner_may_set,
    }


# --- reads -----------------------------------------------------------------------


def ranges(*, plan_ids: list[UUID] | None = None) -> list[dict[str, Any]]:
    """Every range, or those of some plans - for staff screens and the partner
    pricing read (callers authorize)."""
    from workeasy360.commercial.partners.models import SellingPriceRange

    rows = SellingPriceRange.objects.all()
    if plan_ids is not None:
        rows = rows.filter(plan_id__in=plan_ids)
    return [range_view(r) for r in rows.order_by("plan_id", "experience", "interval")]


def range_for(
    *, experience: str, plan_id: UUID, interval: str, currency: str
) -> dict[str, Any] | None:
    from workeasy360.commercial.partners.models import SellingPriceRange

    row = SellingPriceRange.objects.filter(
        experience=experience, plan_id=plan_id, interval=interval, currency=currency
    ).first()
    return range_view(row) if row is not None else None


def ranges_for_partner(partner_id: UUID) -> dict[tuple[str, str, str], dict[str, Any]]:
    """``{(plan_id, interval, currency): range}`` of the programme this partner
    sells under now - what its pricing screen shows next to each plan."""
    from workeasy360.commercial.partners.models import SellingPriceRange

    experience = selectors.selling_experience_at(partner_id, timezone.now())
    return {
        (str(r.plan_id), r.interval, r.currency): range_view(r)
        for r in SellingPriceRange.objects.filter(experience=experience)
    }


# --- staff -----------------------------------------------------------------------


@in_scope
def set_range(
    ctx: TenantContext,
    *,
    experience: Any,
    plan_id: Any,
    interval: Any,
    currency: Any,
    min_minor: Any,
    max_minor: Any = None,
    partner_may_set: Any = True,
) -> dict[str, Any]:
    """Create or change one range. Idempotent; audited with the previous terms."""
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.partners.models import SellingPriceRange

    authz.require(ctx, STAFF_MANAGE)
    exp = _experience(experience)
    cycle = _interval(interval)
    money = _currency(currency)
    low = _amount(min_minor, "min_minor")
    high = _amount(max_minor, "max_minor", optional=True)
    if high is not None and low is not None and high < low:
        raise PartnerValidationError(
            "The maximum must be at least the minimum.", field="max_minor"
        )
    if not isinstance(partner_may_set, bool):
        raise PartnerValidationError(
            "partner_may_set must be true or false.", field="partner_may_set"
        )
    try:
        plan_uuid = UUID(str(plan_id))
    except ValueError as exc:
        raise PartnerValidationError("plan_id is not valid.", field="plan_id") from exc
    if not catalog.plan_exists(plan_uuid):
        raise PartnerRecordNotFound("No such plan.", plan_id=str(plan_uuid))

    with transaction.atomic():
        row = (
            SellingPriceRange.objects.select_for_update()
            .filter(
                experience=exp,
                plan_id=plan_uuid,
                interval=cycle.value,
                currency=money.value,
            )
            .first()
        )
        wanted = {"min_minor": low, "max_minor": high, "partner_may_set": partner_may_set}
        if row is not None and _terms(row) == wanted:
            return range_view(row)
        previous = _terms(row) if row is not None else None
        if row is None:
            row = SellingPriceRange(
                experience=exp,
                plan_id=plan_uuid,
                interval=cycle.value,
                currency=money.value,
            )
        for key, value in wanted.items():
            setattr(row, key, value)
        row.updated_by_id = ctx.user_id
        row.save()
        audit.record(
            ctx,
            "commercial.selling_price_range.set",
            resource_type="partners.selling_price_range",
            resource_id=row.id,
            changes={
                "experience": exp,
                "plan_id": str(plan_uuid),
                "interval": cycle.value,
                "currency": money.value,
                "previous": previous,
                "new": wanted,
            },
        )
    return range_view(row)


@in_scope
def remove_range(ctx: TenantContext, *, range_id: Any) -> None:
    """Withdraw a range: the partner's price for that plan goes back to being
    Super Admin's to set. Prices already published stay as they are."""
    from workeasy360.commercial.partners.models import SellingPriceRange

    authz.require(ctx, STAFF_MANAGE)
    with transaction.atomic():
        row = SellingPriceRange.objects.select_for_update().filter(id=range_id).first()
        if row is None:
            raise PartnerRecordNotFound("No such price range.")
        before = {**range_view(row)}
        row.delete()
        audit.record(
            ctx,
            "commercial.selling_price_range.removed",
            resource_type="partners.selling_price_range",
            resource_id=range_id,
            changes={"previous": before},
        )


# --- the partner -----------------------------------------------------------------


@in_scope
def set_own_price(
    ctx: TenantContext,
    *,
    plan_version_id: Any,
    interval: Any,
    amount_minor: Any,
    currency: Any = None,
) -> dict[str, Any]:
    """The acting partner's own customer price for one plan and cycle, inside
    Super Admin's range. Refused - with the reason - when the partner may not
    sell the plan, no range allows partner pricing, or the amount is outside it.
    """
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.pricing import services as pricing

    authz.require(ctx, PARTNER_MANAGE)
    require_transacting(ctx)
    partner_id = ctx.partner_id
    assert partner_id is not None  # require_transacting refuses otherwise
    if capabilities.SUBSCRIPTION_SELL not in selectors.active_capabilities(partner_id):
        raise PartnerValidationError(
            "Only a partner that sells may set its own prices.", field="partner"
        )
    cycle = _interval(interval)
    amount = _amount(amount_minor, "amount_minor")
    try:
        version_id = UUID(str(plan_version_id))
    except ValueError as exc:
        raise PartnerValidationError(
            "plan_version_id is not valid.", field="plan_version_id"
        ) from exc
    plan = catalog.plan_version(version_id)
    if plan is None or not selectors.may_sell(
        partner_id, product_code=plan.product_code, plan_code=plan.plan_code
    ):
        # One answer for "no such plan" and "not yours to sell".
        raise PartnerRecordNotFound("No such plan.", plan_version_id=str(version_id))

    with transaction.atomic():
        partner = services._lock_partner(partner_id)
        money = _currency(currency or partner.payout_currency)
        experience = selectors.selling_experience_at(partner_id, timezone.now())
        bounds = range_for(
            experience=experience,
            plan_id=plan.plan_id,
            interval=cycle.value,
            currency=money.value,
        )
        if bounds is None or not bounds["partner_may_set"]:
            raise PartnerValidationError(
                "Workeasy360 sets your price for this plan and billing cycle.",
                field="amount_minor",
                reason="partner_pricing_not_allowed",
            )
        low, high = bounds["min_minor"], bounds["max_minor"]
        if amount is None or amount < low or (high is not None and amount > high):
            raise PartnerValidationError(
                "The price must be within the allowed range.",
                field="amount_minor",
                reason="outside_allowed_range",
                min_minor=low,
                max_minor=high,
            )
        price = pricing.publish_own_partner_price(
            ctx,
            partner_id=partner_id,
            book_code=f"pb-{partner_id.hex[:12]}-{money.value.lower()}",
            book_name=f"{partner.display_name} ({money.value})",
            currency=money,
            plan_version_id=version_id,
            interval=cycle,
            amount_minor=amount,
        )
        audit.record(
            ctx,
            "partner.own_price_set",
            resource_type="partners.partner",
            resource_id=partner_id,
            changes={
                "plan_version_id": str(version_id),
                "interval": cycle.value,
                "currency": money.value,
                "amount_minor": amount,
                "range": {"min_minor": low, "max_minor": high},
                "experience": experience,
            },
        )
    return {
        "price_id": str(price.id),
        "plan_version_id": str(version_id),
        "interval": cycle.value,
        "currency": money.value,
        "amount_minor": price.amount_minor,
        "valid_from": price.valid_from.isoformat(),
    }
