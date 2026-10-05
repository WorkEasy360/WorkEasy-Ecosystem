"""Payment provider adapters and their registry.

Two real providers - Stripe (Checkout Sessions) and Razorpay (Payment Links) -
because two is what proves the adapter boundary holds for genuinely different
APIs (form vs JSON bodies, bearer vs basic auth, timestamped vs plain HMAC
webhooks, idempotent vs non-idempotent refunds), plus the offline test provider
for development. Adding a provider is a new module here and one registry line.
"""

from __future__ import annotations

from workeasy360.commercial.payments.providers.contract import Adapter
from workeasy360.commercial.payments.providers.fake import FakeAdapter
from workeasy360.commercial.payments.providers.razorpay import RazorpayPaymentLinkAdapter
from workeasy360.commercial.payments.providers.stripe import StripeCheckoutAdapter

_ADAPTERS: dict[str, Adapter] = {
    adapter.key: adapter
    for adapter in (StripeCheckoutAdapter(), RazorpayPaymentLinkAdapter(), FakeAdapter())
}


def adapter_for(key: str) -> Adapter | None:
    return _ADAPTERS.get(key)
