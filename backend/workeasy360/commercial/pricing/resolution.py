"""``resolve_price`` - the single entry point for "what does this cost?".

No other code in the system computes a price. That is what makes "why is this
customer paying this?" answerable: the returned :class:`PriceQuote` carries
the id of the row that matched and **the reason it matched**, and both are
pinned onto the subscription item.

Resolution order, first match wins (05-commercial-model.md section 4.3):

1. ``ContractPrice`` for this organization, valid at ``at``
2. Partner price book for the **acting partner** (``partner_id``), when a
   partner is conducting the sale. Never selected by region, currency or the
   customer alone: a partner book applies only because a partner with a live
   relationship to this customer is the one buying or changing the plan, so
   it cannot leak reseller pricing to a direct customer
3. Regional price book for ``channel=direct`` matching region + currency
4. Default price book for ``channel=direct`` + currency
5. **Raise.** Never a fallback currency, never a fallback region, never zero.

Step 5 is the important one. A silent fallback bills someone incorrectly and
nobody finds out until a customer complains; a raised
:class:`PriceNotAvailable` is a catalog defect that surfaces at checkout,
which is where it can still be fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Prefetch, Q

from workeasy360.commercial.pricing.domain import Tier, TierCharge, compute_amount
from workeasy360.contracts.enums import (
    BillingInterval,
    PriceBookChannel,
    PriceModel,
    PriceSource,
    TaxBehaviour,
)
from workeasy360.contracts.errors import PriceNotAvailable
from workeasy360.contracts.values.money import CurrencyCode, Money


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """A resolved price, and the evidence for it.

    ``price_id`` and ``price_source`` are pinned onto the subscription item so
    a renewal, a dispute or a support question resolves against the exact row
    that was used - not against whatever the catalog says today.
    """

    price_id: UUID
    price_source: PriceSource
    reason: str

    unit_amount_minor: int | None
    total: Money
    currency: CurrencyCode
    interval: BillingInterval
    price_model: PriceModel
    quantity: int
    tax_behaviour: TaxBehaviour
    tiers: tuple[TierCharge, ...] = ()

    def as_snapshot(self) -> dict[str, Any]:
        """The commercial facts a subscription item must preserve.

        Denormalised onto the item deliberately: historical commercial facts
        must not be derivable *only* from catalog tables, because a catalog
        row can be superseded, closed or (in a disaster) restored from a
        different backup than the subscription.
        """
        return {
            "price_id": str(self.price_id),
            "price_source": self.price_source.value,
            "price_reason": self.reason,
            "unit_amount_minor": self.unit_amount_minor,
            "total_amount_minor": self.total.minor_units,
            "currency": self.currency.value,
            "interval": self.interval.value,
            "price_model": self.price_model.value,
            "tax_behaviour": self.tax_behaviour.value,
        }


def resolve_price(
    *,
    plan_version_id: UUID | None = None,
    bundle_version_id: UUID | None = None,
    organization_id: UUID | None,
    currency: CurrencyCode,
    interval: BillingInterval,
    quantity: int = 1,
    region: str = "",
    at: datetime,
    partner_id: UUID | None = None,
) -> PriceQuote:
    """Resolve the price of one plan version or bundle version.

    ``partner_id`` is supplied by the caller from a *verified* partner
    context (``TenantContext.partner_id``), never from request input.

    ``currency`` is required and is never inferred. Inferring it from the
    organization's region, or falling back to a "close enough" book, is how a
    customer ends up invoiced in the wrong currency - a mistake that is
    expensive to unwind because the payment has already settled.
    """
    if (plan_version_id is None) == (bundle_version_id is None):
        raise PriceNotAvailable(
            "resolve_price needs exactly one of plan_version_id or bundle_version_id."
        )
    if quantity < 1:
        raise PriceNotAvailable(f"Quantity must be at least 1, got {quantity}.")

    contract = _contract_price(
        plan_version_id=plan_version_id,
        bundle_version_id=bundle_version_id,
        organization_id=organization_id,
        currency=currency,
        interval=interval,
        at=at,
    )
    if contract is not None:
        return contract

    if partner_id is not None:
        partner_quote = _book_price(
            plan_version_id=plan_version_id,
            bundle_version_id=bundle_version_id,
            region="",
            currency=currency,
            interval=interval,
            quantity=quantity,
            at=at,
            source=PriceSource.PARTNER_BOOK,
            reason="partner price book",
            channel=PriceBookChannel.PARTNER,
            partner_id=partner_id,
        )
        if partner_quote is not None:
            return partner_quote

    if region:
        quote = _book_price(
            plan_version_id=plan_version_id,
            bundle_version_id=bundle_version_id,
            region=region,
            currency=currency,
            interval=interval,
            quantity=quantity,
            at=at,
            source=PriceSource.REGIONAL_BOOK,
            reason=f"regional price book for {region}",
        )
        if quote is not None:
            return quote

    quote = _book_price(
        plan_version_id=plan_version_id,
        bundle_version_id=bundle_version_id,
        region="",
        currency=currency,
        interval=interval,
        quantity=quantity,
        at=at,
        source=PriceSource.DEFAULT_BOOK,
        reason="default direct price book",
    )
    if quote is not None:
        return quote

    raise PriceNotAvailable(
        f"No price for "
        f"{'plan' if plan_version_id else 'bundle'} version "
        f"{plan_version_id or bundle_version_id} in {currency.value} on a "
        f"{interval.value} interval at {at.isoformat()}. This is a catalog "
        f"defect: nothing falls back to another currency or region.",
        currency=currency.value,
        interval=interval.value,
        region=region,
    )


def _effective(queryset: Any, at: datetime) -> Any:
    """``valid_from <= at < valid_to``, with NULL meaning open-ended."""
    return queryset.filter(valid_from__lte=at).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gt=at)
    )


def _contract_price(
    *,
    plan_version_id: UUID | None,
    bundle_version_id: UUID | None,
    organization_id: UUID | None,
    currency: CurrencyCode,
    interval: BillingInterval,
    at: datetime,
) -> PriceQuote | None:
    """Step 1. Tenant-scoped, so RLS applies exactly as it does everywhere."""
    from workeasy360.commercial.pricing.models import ContractPrice

    if organization_id is None:
        return None

    row = (
        _effective(
            ContractPrice.objects.filter(
                organization_id=organization_id,
                plan_version_id=plan_version_id,
                bundle_version_id=bundle_version_id,
                interval=interval.value,
            ),
            at,
        )
        .order_by("-valid_from")
        .first()
    )
    if row is None:
        return None

    row_currency = CurrencyCode(row.currency)
    if row_currency is not currency:
        # A negotiated rate in another currency is **not** a match. Converting
        # would need a rate, a date and a source; picking one silently
        # produces numbers nobody can reconcile.
        raise PriceNotAvailable(
            f"A contract price exists for this organization in "
            f"{row_currency.value} but {currency.value} was requested. "
            f"Cross-currency conversion is not supported; raise a contract "
            f"price in the requested currency.",
            currency=currency.value,
            contract_currency=row_currency.value,
        )

    total = Money(int(row.amount_minor), row_currency)
    return PriceQuote(
        price_id=row.id,
        price_source=PriceSource.CONTRACT,
        reason=f"negotiated contract price {row.contract_ref or row.id}",
        unit_amount_minor=row.amount_minor,
        total=total,
        currency=row_currency,
        interval=interval,
        price_model=PriceModel(row.model),
        quantity=1,
        tax_behaviour=TaxBehaviour(row.tax_behaviour),
    )


def _book_price(
    *,
    plan_version_id: UUID | None,
    bundle_version_id: UUID | None,
    region: str,
    currency: CurrencyCode,
    interval: BillingInterval,
    quantity: int,
    at: datetime,
    source: PriceSource,
    reason: str,
    channel: PriceBookChannel = PriceBookChannel.DIRECT,
    partner_id: UUID | None = None,
) -> PriceQuote | None:
    """Steps 2-4. One query for the price, one for its tiers.

    A direct book carries no partner (``partner_id IS NULL``); a partner book
    carries exactly the acting partner. Internal books are never selectable.
    """
    from workeasy360.commercial.pricing.models import PlanPrice, PriceBook, PriceTier

    books = _effective(
        PriceBook.objects.filter(
            channel=channel.value,
            partner_id=partner_id,
            currency=currency.value,
            region=region,
            is_active=True,
        ),
        at,
    )

    row = (
        _effective(
            PlanPrice.objects.filter(
                price_book__in=books,
                plan_version_id=plan_version_id,
                bundle_version_id=bundle_version_id,
                interval=interval.value,
                published_at__isnull=False,
                min_quantity__lte=quantity,
            ).filter(Q(max_quantity__isnull=True) | Q(max_quantity__gte=quantity)),
            at,
        )
        .select_related("price_book")
        .prefetch_related(Prefetch("tiers", queryset=PriceTier.objects.all()))
        .order_by("price_book__priority", "-valid_from")
        .first()
    )
    if row is None:
        return None

    book_currency = row.price_book.currency_enum
    price_model = PriceModel(row.model)
    total, charges = compute_amount(
        model=price_model,
        currency=book_currency,
        quantity=quantity,
        amount_minor=row.amount_minor,
        tiers=tuple(
            Tier(
                up_to=tier.up_to,
                unit_amount_minor=tier.unit_amount_minor,
                flat_amount_minor=tier.flat_amount_minor,
            )
            for tier in row.tiers.all()
        ),
    )

    return PriceQuote(
        price_id=row.id,
        price_source=source,
        reason=f"{reason} ({row.price_book.code})",
        unit_amount_minor=row.amount_minor,
        total=total,
        currency=book_currency,
        interval=interval,
        price_model=price_model,
        quantity=quantity,
        tax_behaviour=TaxBehaviour(row.tax_behaviour),
        tiers=charges,
    )
