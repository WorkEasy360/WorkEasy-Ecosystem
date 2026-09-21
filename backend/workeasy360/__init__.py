"""Workeasy360 — one ecosystem to run your entire business.

The six top-level modules documented in 01-domain-boundaries.md live inside
this package rather than at the repository root. The directory names and the
layer rules are exactly as Phase 0 specifies; only the import prefix differs.

Reason: a top-level package named ``platform`` shadows the Python standard
library ``platform`` module, which Django, psycopg and pip all import. See
docs/architecture/adrs/ADR-013-package-namespace.md.
"""
