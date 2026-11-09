"""App configuration for commercial/wallets (Admin Panel, 2026-11-09).

**What it is.** Exactly three spendable balances: the Platform Wallet (a customer's or
a partner's money), AI Credits (a customer's credits) and the Consumption Wallet (a
customer's money for messages, API calls and webhooks). Every movement is one row in
an append-only ledger, written with the balance under a row lock, behind an
idempotency key.

**What it is not.** A partner's commission is an earnings record in
``commercial/commissions`` and never a balance here: nothing in this package reads
or writes commissions.

**Where it sits.** Below ``payments``, which credits a wallet when a top-up order is
paid and pays an invoice from one. Nothing below imports it. ``intelligence`` may not
import it either: AI usage reaches it through ``interfaces``.
"""

from __future__ import annotations

from django.apps import AppConfig


class WalletsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.wallets"
    label = "wallets"
    verbose_name = "Commercial | Wallets"
