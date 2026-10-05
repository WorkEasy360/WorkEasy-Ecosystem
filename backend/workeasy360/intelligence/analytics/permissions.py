"""The organization's analytics permission (Task 90).

``platform.analytics.view`` lets an organization's administrator query the
metrics of the products the organization holds, in the platform's minimal
analytics shell. It is a read and not dangerous. Products query their own
metrics over their credential and never through this permission.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)

VIEW: Final = "platform.analytics.view"

ANALYTICS_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey(VIEW),
        label="Query analytics for the products the organization holds",
        category="analytics",
    ),
)

permission_registry.register_all(ANALYTICS_PERMISSIONS)
