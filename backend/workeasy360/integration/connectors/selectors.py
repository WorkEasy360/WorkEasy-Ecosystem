"""Read queries over connections, returning DTOs (F-6).

Nothing outside this module receives a ``Connection`` or ``ConnectionSecret``
instance. What the DTOs never carry: the configuration verbatim (only the
connector's ``describe_config`` form - a webhook URL can itself be a
credential), and any secret material. The one function that returns secret
material, :func:`secret_material`, returns exactly one connection's and is
called only by the delivery pipeline for the connection it is invoking.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from workeasy360.contracts.errors import IntegrationNotFound
from workeasy360.contracts.values.cursor import (
    Page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.connectors import registry, secrets
from workeasy360.integration.connectors.contract import SecretMaterial
from workeasy360.integration.connectors.models import (
    Connection,
    ConnectionSecret,
    ConnectionStatus,
    SecretStatus,
)
from workeasy360.integration.connectors.permissions import CONNECTION_VIEW
from workeasy360.platform.authz import services as authz

#: Filter values a listing accepts. An allowlist, never ``filter(**params)``.
_STATUS_FILTERS = frozenset(s.value for s in ConnectionStatus)


@dataclass(frozen=True, slots=True)
class ConnectionDTO:
    id: UUID
    connector_key: str
    display_name: str
    status: str
    status_reason: str
    revision: int
    destination: dict[str, Any]
    consecutive_failures: int
    circuit_open_until: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    activated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "connector_key": self.connector_key,
            "display_name": self.display_name,
            "status": self.status,
            "status_reason": self.status_reason,
            "revision": self.revision,
            **self.destination,
            "health": {
                "consecutive_failures": self.consecutive_failures,
                "circuit_open_until": _iso(self.circuit_open_until),
                "last_success_at": _iso(self.last_success_at),
                "last_failure_at": _iso(self.last_failure_at),
            },
            "activated_at": _iso(self.activated_at),
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


@dataclass(frozen=True, slots=True)
class RuntimeConnection:
    """What the delivery pipeline needs to invoke a connection."""

    id: UUID
    organization_id: UUID
    connector_key: str
    status: str
    config: dict[str, Any]
    consecutive_failures: int
    circuit_open_until: datetime | None

    @property
    def is_active(self) -> bool:
        return self.status == ConnectionStatus.ACTIVE.value

    @property
    def is_disabled(self) -> bool:
        return self.status == ConnectionStatus.DISABLED.value


def to_dto(connection: Connection) -> ConnectionDTO:
    connector = registry.get_connector(connection.connector_key)
    destination = connector.describe_config(connection.config) if connector else {}
    return ConnectionDTO(
        id=connection.id,
        connector_key=connection.connector_key,
        display_name=connection.display_name,
        status=connection.status,
        status_reason=connection.status_reason,
        revision=connection.revision,
        destination=destination,
        consecutive_failures=connection.consecutive_failures,
        circuit_open_until=connection.circuit_open_until,
        last_success_at=connection.last_success_at,
        last_failure_at=connection.last_failure_at,
        activated_at=connection.activated_at,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


# --- customer reads ---------------------------------------------------------


def connections_page(
    ctx: TenantContext,
    *,
    limit: Any = None,
    cursor: str | None = None,
    status: str | None = None,
) -> Page:
    """Newest first, keyset-paginated (E8), bounded page size."""
    authz.require(ctx, CONNECTION_VIEW)
    size = clamp_limit(limit)
    rows = Connection.objects.all()
    if status in _STATUS_FILTERS:
        rows = rows.filter(status=status)
    position = decode_cursor(cursor)
    if position is not None:
        created_at, last_id = position
        rows = rows.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id)
        )
    batch = list(rows.order_by("-created_at", "-id")[: size + 1])
    has_more = len(batch) > size
    batch = batch[:size]
    return Page(
        items=[to_dto(c).as_dict() for c in batch],
        next_cursor=encode_cursor(batch[-1].created_at, batch[-1].id)
        if has_more
        else None,
        has_more=has_more,
    )


def connection_detail(ctx: TenantContext, connection_id: Any) -> dict[str, Any]:
    """One connection, with its secret *metadata* (never a value)."""
    authz.require(ctx, CONNECTION_VIEW)
    connection = _get(connection_id)
    versions = (
        ConnectionSecret.objects.filter(connection_id=connection.id)
        .exclude(status=SecretStatus.REVOKED.value)
        .order_by("-version")
        .values("version", "hint", "status", "expires_at", "created_at")
    )
    body = to_dto(connection).as_dict()
    body["secrets"] = [
        {
            "version": v["version"],
            "hint": v["hint"],
            "status": v["status"],
            "expires_at": _iso(v["expires_at"]),
            "created_at": _iso(v["created_at"]),
        }
        for v in versions
    ]
    return body


def connection_labels(ids: Iterable[UUID]) -> dict[UUID, str]:
    """Display names for a set of connections, in one query."""
    wanted = list(set(ids))
    if not wanted:
        return {}
    return dict(
        Connection.objects.filter(id__in=wanted).values_list("id", "display_name")
    )


# --- system reads (pipeline, routing, automation) ---------------------------


def runtime_connection(
    connection_id: UUID, *, lock: bool = False
) -> RuntimeConnection | None:
    """The connection as the pipeline needs it. ``lock`` takes the row lock
    that serialises claims against one connection (in-flight cap, breaker)."""
    rows = Connection.objects.filter(id=connection_id)
    if lock:
        rows = rows.select_for_update()
    connection = rows.first()
    if connection is None:
        return None
    return RuntimeConnection(
        id=connection.id,
        organization_id=connection.organization_id,
        connector_key=connection.connector_key,
        status=connection.status,
        config=dict(connection.config),
        consecutive_failures=connection.consecutive_failures,
        circuit_open_until=connection.circuit_open_until,
    )


def active_connection_ids(ids: Iterable[UUID]) -> set[UUID]:
    """The subset of ``ids`` that are active - in one query."""
    wanted = list(set(ids))
    if not wanted:
        return set()
    return set(
        Connection.objects.filter(
            id__in=wanted, status=ConnectionStatus.ACTIVE.value
        ).values_list("id", flat=True)
    )


def ready_connection_ids(now: datetime) -> list[UUID]:
    """The organization's active connections whose circuit is closed - the
    ones a waiting delivery can be handed to right now. One query."""
    return list(
        Connection.objects.filter(status=ConnectionStatus.ACTIVE.value)
        .exclude(circuit_open_until__gt=now)
        .values_list("id", flat=True)
    )


def secret_material(connection_id: UUID) -> SecretMaterial:
    """The usable secrets of **one** connection, current first (Task 30).

    Usable: the active version, plus a previous version whose overlap window
    has not ended. Raises ``secrets.SecretUnavailable`` if any cannot be
    opened - the pipeline records that as a dead attempt rather than
    sending an unsigned or wrongly signed request.
    """
    now = timezone.now()
    rows = (
        ConnectionSecret.objects.filter(connection_id=connection_id)
        .filter(
            Q(status=SecretStatus.ACTIVE.value)
            | Q(status=SecretStatus.PREVIOUS.value, expires_at__gt=now)
        )
        .order_by("-version")
        .values_list("organization_id", "version", "ciphertext", "status")
    )
    ordered = sorted(rows, key=lambda r: (r[3] != SecretStatus.ACTIVE.value, -r[1]))
    return SecretMaterial(
        secrets=tuple(
            secrets.unseal(
                ciphertext,
                organization_id=organization_id,
                connection_id=connection_id,
                version=version,
            )
            for organization_id, version, ciphertext, _status in ordered
        )
    )


def _get(connection_id: Any) -> Connection:
    try:
        key = UUID(str(connection_id))
    except ValueError:
        raise IntegrationNotFound(resource="connection") from None
    connection = Connection.objects.filter(id=key).first()
    if connection is None:
        raise IntegrationNotFound(resource="connection")
    return connection


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
