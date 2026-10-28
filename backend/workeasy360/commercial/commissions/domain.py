"""Commission rules as pure Python. No Django, no I/O (G3).

Everything here is a deterministic function of its arguments: the same sale,
the same rules and the same instant always produce the same answer, which is
what lets a ledger be *re-derived* and drift be *detected* rather than
trusted. There is no clock read, no database access and no randomness in this
file.

Money is integer minor units and integer basis points, computed through the
shared ``Money.apply_rate`` (Decimal internally, ``ROUND_HALF_UP``, explicit).
There is no float anywhere (R-08).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from workeasy360.contracts.enums import PayoutStatus
from workeasy360.contracts.errors import InvalidStateTransition
from workeasy360.contracts.values.money import CurrencyCode, Money

#: The single attribution rule this platform applies (Task 39). Stored on every
#: converted referral so a later change of default cannot reinterpret history.
ATTRIBUTION_RULE: Final = "first_touch"

#: Reversal reasons the system and staff may record. A closed set: a reversal's
#: reason is a reporting dimension, not free text.
REVERSAL_REASONS: Final = frozenset(
    {"cancellation", "refund", "chargeback", "downgrade_correction", "correction"}
)


@dataclass(frozen=True, slots=True)
class RuleView:
    """The columns of a rule that selection needs."""

    id: UUID
    code: str
    version: int
    partner_id: UUID | None
    product_code: str
    rate_bp: int
    effective_from: datetime
    retired_at: datetime | None
    #: Also earns on paid renewals (ADR-052).
    recurring: bool = False

    def applies_at(self, moment: datetime) -> bool:
        return self.effective_from <= moment and (
            self.retired_at is None or self.retired_at > moment
        )


def select_rule(
    rules: Iterable[RuleView], *, partner_id: UUID, product_code: str, at: datetime
) -> RuleView | None:
    """The one rule that governs a sale, or ``None``.

    Precedence, most specific first: a partner-specific rule over the
    programme default, a product-specific rule over an all-products one, then
    the latest ``effective_from``, then the highest version, then the id - a
    total order, so two rules can never tie and the answer never depends on
    row order.
    """
    candidates = [
        rule
        for rule in rules
        if rule.applies_at(at)
        and rule.partner_id in (None, partner_id)
        and rule.product_code in ("", product_code)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda r: (
            r.partner_id is not None,
            r.product_code != "",
            r.effective_from,
            r.version,
            str(r.id),
        ),
    )


@dataclass(frozen=True, slots=True)
class MarginView:
    """The columns of a margin rule that selection needs (ADR-053)."""

    id: UUID
    code: str
    version: int
    experience: str
    partner_id: UUID | None
    product_code: str
    discount_bp: int
    effective_from: datetime
    retired_at: datetime | None
    recurring: bool = True

    def applies_at(self, moment: datetime) -> bool:
        return self.effective_from <= moment and (
            self.retired_at is None or self.retired_at > moment
        )


def select_margin(
    margins: Iterable[MarginView],
    *,
    partner_id: UUID,
    experience: str,
    product_code: str,
    at: datetime,
) -> MarginView | None:
    """The one margin rule for a partner's sale, or ``None``.

    Precedence like :func:`select_rule`: the partner's own override over the
    programme default of the experience it sells under, a product-specific
    rule over an all-products one, then the latest version.
    """
    candidates = [
        m
        for m in margins
        if m.applies_at(at)
        and (
            m.partner_id == partner_id
            or (m.partner_id is None and m.experience == experience)
        )
        and m.product_code in ("", product_code)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda m: (
            m.partner_id is not None,
            m.product_code != "",
            m.effective_from,
            m.version,
            str(m.id),
        ),
    )


def partner_cost(*, list_minor: int, currency: CurrencyCode, discount_bp: int) -> int:
    """The partner's cost: list price less its discount, rounded half-up."""
    return Money(list_minor, currency).apply_rate(10000 - discount_bp).minor_units


def margin_bp(*, customer_minor: int, cost_minor: int) -> int:
    """The margin as basis points of the customer price (half-up), for display
    and for earning on a part of a sale (a plan upgrade's increase)."""
    if customer_minor <= 0:
        return 0
    margin = customer_minor - cost_minor
    return (margin * 10000 * 2 + customer_minor) // (customer_minor * 2)


def sale_basis(*, total_minor: int, predecessor_total_minor: int | None) -> int:
    """The amount a sale earns commission on.

    A first sale earns on its whole price. A line that *continues* another (a
    plan or quantity change) earns only on the **increase**: paying on the
    whole new price would pay twice for the same customer's same product.
    A decrease earns nothing and reverses nothing - the earlier sale happened
    and was paid for.
    """
    if predecessor_total_minor is None:
        return max(total_minor, 0)
    return max(total_minor - predecessor_total_minor, 0)


def commission_amount(*, basis_minor: int, currency: CurrencyCode, rate_bp: int) -> int:
    """``basis * rate``, rounded half-up to a whole minor unit."""
    return Money(basis_minor, currency).apply_rate(rate_bp).minor_units


def clears_at(event_at: datetime, holdback_days: int) -> datetime:
    """When a commission becomes payable: after the refund window."""
    return event_at + timedelta(days=holdback_days)


def accrual_key(source_ref: str) -> str:
    """The idempotency key for a sale's accrual - derived from the sale."""
    return f"accrual:{source_ref}"


def reversal_key(accrual_id: UUID, reason_code: str) -> str:
    return f"reversal:{accrual_id}:{reason_code}"


# --- payout lifecycle -----------------------------------------------------

P = PayoutStatus

#: Legal payout moves. ``paid`` and ``cancelled`` are terminal. There is no
#: edge from ``approved`` back to ``ready``: an approval is a decision that
#: was made, and undoing it is a cancellation, which is recorded as one.
PAYOUT_TRANSITIONS: Final[dict[PayoutStatus, frozenset[PayoutStatus]]] = {
    P.DRAFT: frozenset({P.READY, P.CANCELLED}),
    P.READY: frozenset({P.APPROVED, P.CANCELLED}),
    P.APPROVED: frozenset({P.PAID, P.CANCELLED}),
    P.PAID: frozenset(),
    P.CANCELLED: frozenset(),
}


def assert_payout_transition(current: PayoutStatus, target: PayoutStatus) -> None:
    if current is target:
        return
    if target not in PAYOUT_TRANSITIONS[current]:
        raise InvalidStateTransition(
            entity="commissions.Payout",
            from_status=current.value,
            to_status=target.value,
        )
