"""App configuration for commercial/payments (Phase 8 extension).

**What it is.** The collection side of billing: an invoice per subscription
period (from the price snapshots the subscription already holds), payment of it
through a provider's **hosted** checkout page, the provider's signed webhooks,
refunds, and dunning. Card data never reaches Workeasy360 - the customer types
it on the provider's page - so nothing here stores, logs or transmits a card
number.

**Where it sits.** Beside ``provisioning``, above ``subscriptions``: it reads
subscriptions and billing, and moves a subscription's status only through
``subscriptions/system.py`` (the single system write pathway). Nothing below
imports it; commissions and partners do not need it.

**Providers are adapters**, sandboxed like the AI providers
(``.importlinter``: no database, settings, network or credentials): an adapter
builds one HTTP request, parses one response and verifies one webhook, from
what it is handed. Credentials are references to environment variables read
live per call (``credentials.py``), never stored.
"""

from __future__ import annotations

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.payments"
    label = "payments"
    verbose_name = "Commercial | Payments"

    def ready(self) -> None:
        from workeasy360.commercial.payments import notices, tasks  # noqa: F401
        from workeasy360.commercial.payments.invoicing import on_commercial_change
        from workeasy360.commercial.subscriptions.hooks import (
            on_commercial_change as register,
        )

        register("payments.invoices", on_commercial_change)
        tasks.register_dispatchers()
