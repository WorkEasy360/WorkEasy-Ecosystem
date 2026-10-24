"""Subscription permission declarations.

Unlike catalog and pricing, these are **customer-facing**. An organization's
billing manager buys, upgrades and cancels their own subscription; nothing
here touches global configuration, and none of it requires internal staff.

``manage`` is deliberately **not** marked dangerous, which is worth
explaining because it commits the customer to money.

``dangerous`` blocks the action under impersonation, which is wrong here.

- (Phase 9 applied the same reasoning to ``platform.billing.manage``: paying
  happens on the provider's hosted page and no payment instrument is stored.)
- Blocking it under impersonation would stop support completing a purchase
  a customer has asked for, which is a normal, audited, break-glass-tagged
  action.

The controls that do apply are the permission itself, the tenancy write
block, the idempotency ledger and a mandatory audit entry on every command.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)

SUBSCRIPTION_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey("commercial.subscription.view"),
        label="View this organization's subscription",
        category="billing",
    ),
    PermissionDeclaration(
        key=PermissionKey("commercial.subscription.manage"),
        label="Buy, change and cancel subscriptions",
        category="billing",
        implies=("commercial.subscription.view", "commercial.catalog.view"),
    ),
)

permission_registry.register_all(SUBSCRIPTION_PERMISSIONS)
