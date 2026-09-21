"""L0 — contracts.

The vocabulary that lets modules talk without touching each other.

Rules (01-domain-boundaries.md §4, enforced by import-linter and by
tests/architecture/test_contracts_are_pure.py):

- Zero imports from platform/, integration/, commercial/, products/,
  interfaces/ or django.
- Only stdlib, dataclasses, decimal and typing.
- Additive changes only.
"""
