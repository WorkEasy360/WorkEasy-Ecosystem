"""Grants for the guest-session table (2026-11-10, ADR-062).

**Control plane** - a grant, no policy: a guest view is a staff member looking at
one partner, and a partner belongs to no customer (``partners/models.py``).
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import grant_app_role


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0016_ledger_truncate_guard"),
        ("partners", "0019_guest_sessions"),
    ]

    operations = [grant_app_role("partners_guest_session")]
