"""Identifiers for the control plane itself (ADR-027).

The platform organization is the tenant anchor for control-plane writes that
belong to **no customer**: creating a partner, granting a capability,
approving a payout, publishing a catalog version. Those actions still have to
be audited in the one audit log, guarded by the one idempotency ledger and
announced through the one outbox, and all three are keyed on an organization.
Giving the control plane an organization of its own reuses every one of them
unchanged, where a parallel "control-plane audit table" would be a second
audit system to keep append-only, hash-chained and reviewed.

It holds **no customer data and no members**: nobody can be a member of it
(no membership rows exist), so no one can reach it through the tenant path,
and the customer directory, dashboards and sweeps exclude it by this id.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

PLATFORM_ORGANIZATION_ID: Final = UUID("00000000-0000-4000-8000-000000000360")
PLATFORM_ORGANIZATION_SLUG: Final = "workeasy360-platform"

#: The system identity that owns the platform organization row. Disabled: it
#: cannot authenticate, and exists only because ``Organization.created_by`` is
#: a required foreign key.
PLATFORM_SYSTEM_USER_EMAIL: Final = "platform-control-plane@workeasy360.invalid"
