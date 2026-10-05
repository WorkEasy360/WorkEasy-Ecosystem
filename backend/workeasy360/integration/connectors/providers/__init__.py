"""Connector implementations.

One, deliberately (Task 28): the signed ``webhook`` connector, which is both
the reference implementation of the contract and the thing customers
actually need first. Vendor connectors are added here one at a time, each
behind the same four-function contract, each reviewed as the new egress
surface it is.

Modules in this package are sandboxed by the
``connector-providers-are-sandboxed`` import-linter contract: they may import
the connector contract and the egress value types, and nothing that can reach
the database, settings or another connection's credentials.
"""
