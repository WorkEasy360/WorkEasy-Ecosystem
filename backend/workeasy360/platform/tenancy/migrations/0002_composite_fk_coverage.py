"""Close three gaps in Layer 4 composite foreign-key coverage.

Three tenant-to-tenant foreign keys were left with Django's plain
single-column constraint:

- ``RoleAssignment.granted_by``
- ``SeatAssignment.assigned_by``
- ``Invitation.invited_by``

All three are **nullable attribution fields**, which is exactly why they were
easy to miss: the eye goes to the structural relationships. But a cross-tenant
``granted_by`` is still representable without a composite constraint, and "who
granted this role" is not a field where a foreign tenant's id should be
storable (R-05).

Found by ``test_every_tenant_to_tenant_fk_has_a_composite_constraint``, which
derives the expected set from the live model registry and compares it against
the live schema. A hand-maintained list would have had the same three gaps.

``SET NULL`` rather than ``CASCADE``: removing the member who granted a role
must not remove the grant. Losing the attribution is correct; losing the
authorization is not.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import add_tenant_fk

COMPOSITE_FKS = [
    {
        "table": "authz_role_assignment",
        "column": "granted_by_id",
        "references_table": "organizations_membership",
        "on_delete": "SET NULL",
    },
    {
        "table": "entitlements_seat_assignment",
        "column": "assigned_by_id",
        "references_table": "organizations_membership",
        "on_delete": "SET NULL",
    },
    {
        "table": "organizations_invitation",
        "column": "invited_by_id",
        "references_table": "organizations_membership",
        "on_delete": "SET NULL",
    },
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0001_rls_policies"),
    ]

    operations = [add_tenant_fk(**fk) for fk in COMPOSITE_FKS]
