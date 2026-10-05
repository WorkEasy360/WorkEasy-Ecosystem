"""An organization's own AI permissions (Task 88).

``platform.ai.*`` rather than a product prefix: AI settings belong to the
organization, not to any one product. ``view`` shows settings, limits and
usage metadata. ``manage`` is **dangerous** (AAL 2 within the step-up window,
refused under impersonation, refused while the organization is read-only):
it decides whether the organization's data may be sent to AI providers at
all, to which vendors, and how much may be spent doing it.

The Super Admin keys (``platform.staff_ai.*``) are declared with the other
control-plane permissions in ``platform/authz/permissions.py``.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.intelligence.ai.policy import MANAGE, VIEW
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)

AI_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey(VIEW),
        label="View the organization's AI settings, limits and usage",
        category="ai",
    ),
    PermissionDeclaration(
        key=PermissionKey(MANAGE),
        label="Turn AI on or off, set its monthly budget and restrict its providers",
        category="ai",
        implies=(VIEW,),
        dangerous=True,
    ),
)

permission_registry.register_all(AI_PERMISSIONS)
