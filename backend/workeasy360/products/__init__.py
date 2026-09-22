"""L3 - a reserved, permanently empty namespace.

Business products (CRM, Books, Inventory, Projects, ...) are **separate
applications in their own repositories** and integrate with this platform
through the contracts in ``docs/architecture/16-ecosystem-product-boundary.md``
(ADR-017). No product code lives here.

The packages below exist only so the import-linter independence contracts
(F-1, F-2) and ``test_products_are_structural_placeholders_only`` have
modules to bind to. Any file other than ``__init__.py`` appearing in one of
them fails the build - that assertion is unconditional, and no exemption
should be added to it.
"""
