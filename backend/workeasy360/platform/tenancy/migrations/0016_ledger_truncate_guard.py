"""The Admin Panel's append-only ledgers refuse TRUNCATE too (2026-11-10).

``0014_wallets_and_kyc`` made the wallet ledger, the KYC decisions and the KYC
document-access log append-only with a revoked grant and a row trigger. A row
trigger cannot see a TRUNCATE, so the tables' owner could still empty any of them
in one statement. ``forbid_truncate`` closes that, as ``0007`` did for the
commission ledger: emptying one now needs an explicit ``DISABLE TRIGGER`` first.
Reversible: the reverse drops the triggers and leaves every row as it was.
"""

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import forbid_truncate

LEDGERS = [
    "wallets_entry",
    "partners_kyc_decision",
    "partners_kyc_document_access",
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0015_support_setup_help"),
    ]

    operations = [forbid_truncate(table) for table in LEDGERS]
