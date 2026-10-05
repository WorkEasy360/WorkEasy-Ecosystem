"""The connector registry: key -> implementation.

Code-declared, like permissions and event schemas. A customer chooses among
registered connectors; they never supply one. The pipeline looks a connector
up by the key stored on the connection and never branches on which it is -
there is no ``if connector_key == "..."`` anywhere in the engine (Task 36).
"""

from __future__ import annotations

from typing import Any

from workeasy360.integration.connectors.contract import Connector

_connectors: dict[str, Connector] = {}


def register_connector(connector: Connector) -> Connector:
    if not isinstance(connector, Connector):
        raise TypeError(f"{connector!r} does not implement the Connector contract.")
    existing = _connectors.get(connector.key)
    if existing is not None and existing is not connector:
        raise ValueError(f"Connector {connector.key!r} is already registered.")
    _connectors[connector.key] = connector
    return connector


def get_connector(key: str) -> Connector | None:
    return _connectors.get(key)


def catalog() -> list[dict[str, Any]]:
    """What an administrator may choose from. No configuration values."""
    return [
        {
            "key": c.key,
            "version": c.version,
            "label": c.label,
            "description": c.description,
            "generates_secret": c.generates_secret,
            "capabilities": [
                {"key": s.key, "label": s.label, "description": s.description}
                for s in c.capabilities.values()
            ],
        }
        for c in sorted(_connectors.values(), key=lambda c: c.key)
    ]


def unregister(key: str) -> None:
    """Test hook only."""
    _connectors.pop(key, None)
