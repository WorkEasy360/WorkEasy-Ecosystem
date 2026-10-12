"""What a partner needs before it can be activated - one rule, two callers.

The Add Reseller review applies it to a draft that does not exist yet; the
partner's Overview applies it to the saved account. Both ask the same question
of the same facts, so the two can never disagree about "ready".

It is a *readiness* rule, not an authorization rule: it decides what the screens
offer and what the review reports as missing. The one thing the service itself
refuses is activating a partner with nobody to act for it
(``partners.services.set_partner_status``); the rest are advice that stops an
operator activating something half-built, never a way for a partner to do more.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Final

from workeasy360.commercial.partners import capabilities

#: A partner holding one of these is a seller: it can create customers or sell
#: to them, so it needs something to sell.
SELLING: Final = frozenset(
    {capabilities.SUBSCRIPTION_SELL, capabilities.CUSTOMER_PROVISION}
)


def evaluate(
    *,
    contact_name: str,
    contact_email: str,
    business_email: str,
    held: Collection[str],
    product_count: int,
    has_administrator: bool,
) -> dict[str, Any]:
    """``{"can_activate": bool, "missing": [{key, label, detail}]}``."""
    missing: list[dict[str, str]] = []

    def need(key: str, label: str, detail: str) -> None:
        missing.append({"key": key, "label": label, "detail": detail})

    if not contact_name or not contact_email:
        need(
            "primary_contact",
            "Primary contact",
            "Add the primary contact's name and email.",
        )
    if not business_email:
        need("business_email", "Business email", "Add the company's business email.")
    if not held:
        need("capabilities", "Capabilities", "Choose at least one capability.")
    if set(held) & SELLING and product_count == 0:
        need(
            "products",
            "Products",
            "This partner can sell but is allowed no product. Allow at least one.",
        )
    if not has_administrator:
        need(
            "administrator",
            "Partner administrator",
            "Name the person who will run the account.",
        )
    return {"can_activate": not missing, "missing": missing}
