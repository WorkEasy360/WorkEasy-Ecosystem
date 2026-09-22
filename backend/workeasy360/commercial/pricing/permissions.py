"""Pricing permission declarations.

Split from catalog management on purpose. In practice the person who defines
what a plan contains is not always the person authorised to decide what it
costs, and bundling the two forces over-privileging - the same reasoning that
keeps ``platform.billing.manage`` out of the admin role.

``commercial.pricing.manage`` is granted by no system role and additionally
requires an internal platform identity, because a price book applies to every
tenant at once.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)

PRICING_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey("commercial.pricing.view"),
        label="View price books and prices",
        category="commercial",
    ),
    PermissionDeclaration(
        key=PermissionKey("commercial.pricing.manage"),
        label="Publish prices and grant negotiated contract prices",
        category="commercial",
        dangerous=True,
        implies=("commercial.pricing.view",),
    ),
)

permission_registry.register_all(PRICING_PERMISSIONS)
