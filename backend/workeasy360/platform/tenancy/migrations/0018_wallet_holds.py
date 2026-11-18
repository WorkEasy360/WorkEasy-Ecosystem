"""Grants for the wallet-hold table (2026-11-12, ADR-065).

**Control plane** - a grant, no policy, like ``wallets_wallet``: a hold names a
wallet, and a wallet's owner is an organization or a partner. Mutable on purpose:
a hold moves from held to captured or released; the money itself moves only in
the append-only ledger.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import grant_app_role


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0017_partner_guest_sessions"),
        ("wallets", "0002_wallet_hold"),
    ]

    operations = [grant_app_role("wallets_hold")]
