"""The one webhook-specific permission (Task 56).

Replaying a dead delivery re-sends an event the organization already chose
to send, to a destination it already configured - it widens nothing. So it
is **not** ``dangerous``: an operator clearing a backlog after an endpoint
outage should not need an extra confirmation for every click. It is still
separate from ``platform.connection.view`` because it causes an external
side effect, and separate from ``manage`` so it can be delegated to an
on-call operator who must not be able to repoint an endpoint.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.integration.connectors.permissions import CONNECTION_VIEW
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)

WEBHOOK_REPLAY: Final = "platform.webhook.replay"

WEBHOOK_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey(WEBHOOK_REPLAY),
        label="Replay failed webhook deliveries",
        category="integrations",
        implies=(CONNECTION_VIEW,),
    ),
)

permission_registry.register_all(WEBHOOK_PERMISSIONS)
