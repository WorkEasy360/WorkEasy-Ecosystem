"""Database controls added by the Phase 7 final audit.

**A staff grant's expiry is one of its terms.** ``authz.0006`` adds
``StaffGrant.expires_at``; the freeze trigger from ``0006_partner_channel_controls``
is re-issued with that column in its list, so a grant cannot be quietly
extended (or shortened) by an ``UPDATE`` - to change the terms, end the grant
and issue a new one. Reversing restores exactly the previous trigger rather
than dropping it, so a rollback never leaves the grant table unfrozen.

**The commission ledger refuses TRUNCATE.** The append-only trigger is a row
trigger, which a TRUNCATE never fires; the table owner could otherwise empty
the ledger with no deliberate step. See ``migrations_helpers.forbid_truncate``.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    forbid_truncate,
    freeze_columns,
)

_GRANT_TERMS = ["user_id", "permission", "granted_by_id", "granted_at", "reason"]


def _freeze_grant_terms(columns: list[str]) -> migrations.RunSQL:
    return freeze_columns("authz_staff_grant", columns=columns)


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0006_partner_channel_controls"),
        ("authz", "0006_staffgrant_expires_at"),
    ]

    operations = [
        migrations.RunSQL(
            sql=_freeze_grant_terms([*_GRANT_TERMS, "expires_at"]).sql,
            reverse_sql=_freeze_grant_terms(_GRANT_TERMS).sql,
            elidable=False,
        ),
        forbid_truncate("commissions_accrual"),
    ]
