"""Price arithmetic as pure Python. No Django, no I/O (G3).

Every amount here is a :class:`Money` - integer minor units plus an explicit
currency - so the arithmetic is exact by construction and cross-currency
operations raise rather than silently producing a number. The most common
class of billing defect is eliminated by the type, not by discipline (R-08).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from workeasy360.contracts.enums import PriceModel
from workeasy360.contracts.errors import PriceNotAvailable
from workeasy360.contracts.values.money import CurrencyCode, Money


@dataclass(frozen=True, slots=True)
class Tier:
    """One band. ``up_to=None`` is the final, unbounded band."""

    up_to: int | None
    unit_amount_minor: int | None = None
    flat_amount_minor: int | None = None


@dataclass(frozen=True, slots=True)
class TierCharge:
    """What one band contributed, kept so an invoice can show its working."""

    up_to: int | None
    units: int
    unit_amount_minor: int | None
    flat_amount_minor: int | None
    subtotal_minor: int


def compute_amount(
    *,
    model: PriceModel,
    currency: CurrencyCode,
    quantity: int,
    amount_minor: int | None,
    tiers: tuple[Tier, ...] = (),
) -> tuple[Money, tuple[TierCharge, ...]]:
    """Total for ``quantity`` units under ``model``.

    Returns the total and the per-band breakdown. The breakdown is not
    decoration: "why is this the number?" is the single most common billing
    support question, and reconstructing it later from a total is impossible.
    """
    if quantity < 0:
        raise PriceNotAvailable(f"Quantity must not be negative, got {quantity}.")

    if model is PriceModel.FLAT:
        _require_amount(model, amount_minor)
        return Money(int(amount_minor), currency), ()

    if model is PriceModel.PER_SEAT:
        _require_amount(model, amount_minor)
        return Money(int(amount_minor), currency) * quantity, ()

    if model in (PriceModel.TIERED, PriceModel.VOLUME):
        if not tiers:
            raise PriceNotAvailable(
                f"A {model.value} price must declare tiers.", price_model=model.value
            )
        ordered = _ordered(tiers)
        if model is PriceModel.TIERED:
            return _graduated(ordered, quantity, currency)
        return _volume(ordered, quantity, currency)

    # PriceModel.USAGE. Reached only if a usage price were somehow published;
    # the publish guard refuses them, because charging for usage requires a
    # metering pipeline that does not exist. Failing here rather than
    # returning zero: a silent zero is an invoice that undercharges.
    raise PriceNotAvailable(
        "Usage-based pricing requires metered consumption, which is not "
        "implemented. Publish a flat or per-seat price instead.",
        price_model=model.value,
    )


def _require_amount(model: PriceModel, amount_minor: int | None) -> None:
    if amount_minor is None:
        raise PriceNotAvailable(
            f"A {model.value} price must carry an amount.", price_model=model.value
        )


def _ordered(tiers: tuple[Tier, ...]) -> tuple[Tier, ...]:
    """Bands ascending, with the unbounded one last.

    Sorting here rather than trusting the caller: a mis-ordered tier list
    charges the wrong rate for every unit, and the database ordering is not
    guaranteed to survive a prefetch.
    """
    return tuple(
        sorted(tiers, key=lambda t: (t.up_to is None, t.up_to if t.up_to else 0))
    )


def _graduated(
    tiers: tuple[Tier, ...], quantity: int, currency: CurrencyCode
) -> tuple[Money, tuple[TierCharge, ...]]:
    """Each band charged at its own rate."""
    total = Money.zero(currency)
    charges: list[TierCharge] = []
    consumed = 0

    for tier in tiers:
        if consumed >= quantity:
            break
        ceiling = quantity if tier.up_to is None else min(tier.up_to, quantity)
        units = max(ceiling - consumed, 0)
        if units == 0 and tier.flat_amount_minor is None:
            continue

        subtotal = Money.zero(currency)
        if tier.unit_amount_minor is not None:
            subtotal = subtotal + Money(int(tier.unit_amount_minor), currency) * units
        if tier.flat_amount_minor is not None and units > 0:
            subtotal = subtotal + Money(int(tier.flat_amount_minor), currency)

        total = total + subtotal
        charges.append(
            TierCharge(
                up_to=tier.up_to,
                units=units,
                unit_amount_minor=tier.unit_amount_minor,
                flat_amount_minor=tier.flat_amount_minor,
                subtotal_minor=subtotal.minor_units,
            )
        )
        consumed = ceiling

    if consumed < quantity:
        raise PriceNotAvailable(
            f"Tiers cover only {consumed} of {quantity} units. The final tier "
            f"must be unbounded (up_to=None) or the price has a gap.",
        )
    return total, tuple(charges)


def _volume(
    tiers: tuple[Tier, ...], quantity: int, currency: CurrencyCode
) -> tuple[Money, tuple[TierCharge, ...]]:
    """All units charged at the rate of the band the quantity reaches."""
    for tier in tiers:
        if tier.up_to is None or quantity <= tier.up_to:
            subtotal = Money.zero(currency)
            if tier.unit_amount_minor is not None:
                subtotal = (
                    subtotal + Money(int(tier.unit_amount_minor), currency) * quantity
                )
            if tier.flat_amount_minor is not None:
                subtotal = subtotal + Money(int(tier.flat_amount_minor), currency)
            return subtotal, (
                TierCharge(
                    up_to=tier.up_to,
                    units=quantity,
                    unit_amount_minor=tier.unit_amount_minor,
                    flat_amount_minor=tier.flat_amount_minor,
                    subtotal_minor=subtotal.minor_units,
                ),
            )

    raise PriceNotAvailable(
        f"No volume tier covers {quantity} units. The final tier must be "
        f"unbounded (up_to=None)."
    )


#: Models whose price is expressed on tiers rather than a single amount.
TIERED_MODELS: Final[frozenset[PriceModel]] = frozenset(
    {PriceModel.TIERED, PriceModel.VOLUME}
)
