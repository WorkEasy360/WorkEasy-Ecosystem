"""Permissions for an organization's own connections (Task 56).

Two, because two are needed. ``platform.connection.*`` rather than the
brief's ``integration.*`` example: ``platform.integration.*`` already names
the *staff* product-registration control plane (``platform/ecosystem``), and
one prefix for two unrelated trust models is how the wrong one gets granted.

**Dangerous: YES (Tasks 52/53).** ``manage`` is ``dangerous``
(blocked under impersonation, refused while
the organization is read-only), for a concrete reason each:

- choosing a destination URL is choosing where the organization's business
  events are sent. Anyone who can set it can exfiltrate every subscribed
  event to a server they control, silently and continuously;
- rotating a credential mints a secret that authenticates deliveries *as
  Workeasy360* to the customer's own systems;
- subscribing a connection to more events widens what leaves the building.

The mechanism already exists (``authz.check``) and is already used for the
equivalent staff actions. ``view`` is not dangerous:
reading metadata and delivery status discloses no secret (none is readable)
and is what an administrator does while diagnosing.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)

CONNECTION_VIEW: Final = "platform.connection.view"
CONNECTION_MANAGE: Final = "platform.connection.manage"

CONNECTION_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey(CONNECTION_VIEW),
        label="View integrations, webhook subscriptions and deliveries",
        category="integrations",
    ),
    PermissionDeclaration(
        key=PermissionKey(CONNECTION_MANAGE),
        label="Create, change, rotate and disable integrations and subscriptions",
        category="integrations",
        implies=(CONNECTION_VIEW,),
        dangerous=True,
    ),
)

permission_registry.register_all(CONNECTION_PERMISSIONS)
