"""Entitlement services, split by direction.

``read`` is what products import. ``write`` is importable **only** by
``commercial/provisioning``, enforced by an import-linter contract:

    [importlinter:contract:entitlements-single-writer]
    forbidden_modules = workeasy360.platform.entitlements.services.write

The split is at module level rather than by convention precisely so a linter
can see it.
"""
