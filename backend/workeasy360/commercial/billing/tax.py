"""Tax on what a customer pays (ADR-058): GST on INR invoices.

The owner's rule (2026-11-05): every INR price is **before** GST, and 18% GST
is added on top - on the Store's product page, on the checkout's quote, on the
order's invoice and on every renewal invoice - so the page, the payment and
the invoice show one and the same total. Other currencies carry no tax line.

GST is charged on what is actually owed: the plan's price for the period (or
its prorated rest), less a coupon, plus any one-time set-up fee. It is rounded
to whole rupees, half up (₹1,511.82 -> ₹1,512), the way Indian invoices
commonly round tax. The rate is ``settings.GST_RATE_BP`` (1800 = 18%); 0 turns
it off.

Pure and database-free: the callers (``payments.orders``, ``payments.invoicing``,
the Store's product page) decide where the line goes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.conf import settings

#: The currency GST applies to, and how many minor units its whole unit has.
GST_CURRENCY = "INR"
_MINOR_PER_RUPEE = 100


@dataclass(frozen=True)
class Tax:
    label: str
    rate_bp: int
    amount_minor: int

    @property
    def description(self) -> str:
        """``GST (18%)`` - the invoice line's and the summary's wording."""
        return f"{self.label} ({_percent(self.rate_bp)})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "rate_bp": self.rate_bp,
            "amount_minor": self.amount_minor,
            "description": self.description,
        }


def _percent(rate_bp: int) -> str:
    whole, rest = divmod(rate_bp, 100)
    return f"{whole}%" if rest == 0 else f"{rate_bp / 100:g}%"


def gst_rate_bp() -> int:
    rate = int(getattr(settings, "GST_RATE_BP", 1800) or 0)
    return max(0, min(rate, 10_000))


def terms(currency: str | None) -> dict[str, Any] | None:
    """What a page shows before anything is priced: ``{"label", "rate_bp",
    "description"}`` for a taxed currency, else ``None``."""
    rate = gst_rate_bp()
    if (currency or "").upper() != GST_CURRENCY or rate == 0:
        return None
    return {
        "label": "GST",
        "rate_bp": rate,
        "description": f"GST ({_percent(rate)})",
    }


def tax_for(currency: str, taxable_minor: int) -> Tax | None:
    """The tax on ``taxable_minor`` in ``currency``, or ``None`` when nothing
    is taxed (another currency, GST off, or nothing owed)."""
    rate = gst_rate_bp()
    if currency.upper() != GST_CURRENCY or rate == 0 or taxable_minor <= 0:
        return None
    exact = Decimal(taxable_minor) * Decimal(rate) / Decimal(10_000)
    rupees = (exact / _MINOR_PER_RUPEE).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return Tax(label="GST", rate_bp=rate, amount_minor=int(rupees) * _MINOR_PER_RUPEE)
