"""Catalog permission declarations.

The catalog is **global control-plane configuration**. A customer
administrator must never be able to change what products exist, what a plan
includes or what it costs - those decisions apply to every tenant at once.

Two independent barriers enforce that, because either alone is one mistake
away from failing:

1. ``manage`` permissions here are granted by **no system role**, so a
   customer cannot hold one even if a role is misconfigured.
2. Every catalog-mutating service additionally calls
   ``authz.require_platform_staff``, so holding the permission is not enough
   without an internal identity.

Asserted by ``test_no_system_role_grants_catalog_management`` and
``test_catalog_management_requires_platform_staff``.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)

CATALOG_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey("commercial.catalog.view"),
        label="View products, features, plans and bundles",
        category="commercial",
    ),
    PermissionDeclaration(
        key=PermissionKey("commercial.catalog.manage"),
        label="Create and change products, features, plans and bundles",
        category="commercial",
        # Publishing a plan version is irreversible and changes what every
        # future customer buys. No impersonation, like any other
        # dangerous permission.
        dangerous=True,
        implies=("commercial.catalog.view",),
    ),
)

permission_registry.register_all(CATALOG_PERMISSIONS)
