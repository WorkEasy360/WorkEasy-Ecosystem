"""Grants for help content and customer setup (2026-11-09). Control plane: grants, no
policy. Help content belongs to no customer, and the setup queue spans customers."""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import grant_app_role


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0014_wallets_and_kyc"),
        ("administration", "0003_help_and_company"),
        ("provisioning", "0003_customer_setup"),
    ]

    operations = [
        grant_app_role("administration_help_article"),
        grant_app_role("provisioning_setup_requirement"),
        grant_app_role("provisioning_customer_setup"),
    ]
