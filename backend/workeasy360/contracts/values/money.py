"""Money as (integer minor units, currency). Never a float.

R-08: a float amount anywhere in pricing, invoicing, commission or ledger code
produces rounding errors that compound. Customers are billed incorrectly and
partner commissions stop reconciling, and correcting it retroactively is
extremely expensive. This type makes the wrong representation unavailable
rather than merely discouraged.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Final


class CurrencyCode(str, Enum):
    """ISO 4217. Extended as markets are added.

    The minor-unit exponent table below must stay in step; ``exponent`` raises
    a KeyError rather than assuming 2 if a member is added without one.
    """

    GBP = "GBP"
    USD = "USD"
    EUR = "EUR"
    AUD = "AUD"
    CAD = "CAD"
    INR = "INR"
    JPY = "JPY"
    AED = "AED"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.value

    @property
    def exponent(self) -> int:
        """Decimal places this currency subdivides into."""
        return _MINOR_UNIT_EXPONENT[self]


# JPY has no minor unit. Hard-coding two decimal places everywhere is a common
# and expensive bug in multi-currency systems.
_MINOR_UNIT_EXPONENT: Final[dict[CurrencyCode, int]] = {
    CurrencyCode.GBP: 2,
    CurrencyCode.USD: 2,
    CurrencyCode.EUR: 2,
    CurrencyCode.AUD: 2,
    CurrencyCode.CAD: 2,
    CurrencyCode.INR: 2,
    CurrencyCode.JPY: 0,
    CurrencyCode.AED: 2,
}


class CurrencyMismatch(ValueError):
    """Arithmetic was attempted across two currencies.

    Refused rather than converted: a conversion needs a rate, a rate needs a
    date and a source, and silently choosing one produces numbers nobody can
    reconcile later.
    """


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact monetary amount.

    ``minor_units`` is an integer: 1099 GBP minor units is 10.99 GBP. Every
    operation is integer arithmetic, so it is exact by construction.
    """

    minor_units: int
    currency: CurrencyCode

    def __post_init__(self) -> None:
        # bool is a subclass of int; Money(True, GBP) is always a bug.
        if not isinstance(self.minor_units, int) or isinstance(self.minor_units, bool):
            raise TypeError(
                f"Money.minor_units must be int, got "
                f"{type(self.minor_units).__name__}. Floats are forbidden (R-08)."
            )
        if not isinstance(self.currency, CurrencyCode):
            raise TypeError("Money.currency must be a CurrencyCode.")

    # --- construction -----------------------------------------------------

    @classmethod
    def zero(cls, currency: CurrencyCode) -> Money:
        return cls(0, currency)

    @classmethod
    def from_major(cls, amount: Decimal | int | str, currency: CurrencyCode) -> Money:
        """Build from a major-unit amount such as ``Decimal("10.99")``.

        Accepts str, int or Decimal but never float: passing a float would
        reintroduce exactly the imprecision this type exists to prevent.
        """
        if isinstance(amount, float):
            raise TypeError(
                'Money.from_major refuses float. Pass Decimal("10.99") or '
                '"10.99" - binary floats cannot represent most decimal '
                "fractions exactly (R-08)."
            )
        dec = amount if isinstance(amount, Decimal) else Decimal(amount)
        scaled = dec.scaleb(currency.exponent)
        if scaled != scaled.to_integral_value():
            raise ValueError(
                f"{dec} carries more precision than {currency.value} supports "
                f"({currency.exponent} decimal places)."
            )
        return cls(int(scaled), currency)

    # --- projection -------------------------------------------------------

    def to_major(self) -> Decimal:
        """Exact major-unit value, for display and decimal-column storage.

        Never used as an arithmetic intermediate - arithmetic stays in
        minor units.
        """
        return Decimal(self.minor_units).scaleb(-self.currency.exponent)

    def as_dict(self) -> dict[str, object]:
        """Wire form.

        Always (integer, code) - never a formatted string and never a bare
        decimal. The R-08 fitness test asserts this shape in event payloads
        and serializers.
        """
        return {"minor_units": self.minor_units, "currency": self.currency.value}

    # --- arithmetic -------------------------------------------------------

    def _check(self, other: object) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"Cannot combine Money with {type(other).__name__}.")
        if self.currency is not other.currency:
            raise CurrencyMismatch(
                f"Refusing to combine {self.currency.value} and "
                f"{other.currency.value}. Convert explicitly with a dated rate."
            )

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor_units, self.currency)

    def __mul__(self, factor: int) -> Money:
        """Multiply by a whole quantity such as seats or units.

        Only int is accepted. Percentages and rates go through
        :meth:`apply_rate`, which forces an explicit rounding decision.
        """
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError(
                "Money * factor accepts int only. For rates use apply_rate()."
            )
        return Money(self.minor_units * factor, self.currency)

    __rmul__ = __mul__

    def apply_rate(self, basis_points: int, *, rounding: str = ROUND_HALF_UP) -> Money:
        """Apply a rate expressed in integer basis points (1 bp = 0.01%).

        Rates are basis points rather than floats for the same reason amounts
        are minor units. Rounding is an explicit argument because "who absorbs
        the half-penny" is a business decision, not a default.
        """
        if not isinstance(basis_points, int) or isinstance(basis_points, bool):
            raise TypeError("basis_points must be int (1 bp = 0.01%).")
        exact = (Decimal(self.minor_units) * Decimal(basis_points)) / Decimal(10_000)
        return Money(int(exact.quantize(Decimal(1), rounding=rounding)), self.currency)

    def allocate(self, weights: list[int]) -> list[Money]:
        """Split across weights while losing no minor unit.

        The remainder is distributed a unit at a time to the largest weights
        first, so 5p split three ways is 2p/2p/1p and the parts always sum
        back to the whole. Naive division silently loses or invents money.
        """
        if not weights or any(w < 0 for w in weights):
            raise ValueError("allocate() needs at least one non-negative weight.")
        total_weight = sum(weights)
        if total_weight == 0:
            raise ValueError("allocate() weights must not sum to zero.")

        parts = [self.minor_units * w // total_weight for w in weights]
        remainder = self.minor_units - sum(parts)
        order = sorted(range(len(weights)), key=lambda i: weights[i], reverse=True)
        step = 1 if remainder >= 0 else -1
        for i in range(abs(remainder)):
            parts[order[i % len(order)]] += step
        return [Money(p, self.currency) for p in parts]

    # --- comparison -------------------------------------------------------

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units >= other.minor_units

    @property
    def is_zero(self) -> bool:
        return self.minor_units == 0

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.to_major()} {self.currency.value}"
