"""Automation permissions (Task 56).

``manage`` is ``dangerous`` for the same reason ``platform.connection.manage``
is: a rule's actions can route events to any active connection and notify
members, so creating or activating one is a way to send the organization's
events somewhere new. Step-up is the existing mechanism (``authz.check``),
not a parallel one.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)

AUTOMATION_VIEW: Final = "platform.automation.view"
AUTOMATION_MANAGE: Final = "platform.automation.manage"

AUTOMATION_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey(AUTOMATION_VIEW),
        label="View automation rules and their executions",
        category="automation",
    ),
    PermissionDeclaration(
        key=PermissionKey(AUTOMATION_MANAGE),
        label="Create, change, activate and pause automation rules",
        category="automation",
        implies=(AUTOMATION_VIEW,),
        dangerous=True,
    ),
)

permission_registry.register_all(AUTOMATION_PERMISSIONS)
