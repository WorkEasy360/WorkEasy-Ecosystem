"""Read access to routable events, returning DTOs (F-6).

Also home to the **delivery body contract** (Task 91): the one function that
turns a stored event into what a receiver sees. Versioned by its ``schema``
field; its shape is the platform's promise to every receiver and is changed
additively or not at all. It exposes the event's public metadata and the
producer's payload - never an internal column, a database id or the
platform's own bookkeeping (``fingerprint``, ``routing_status``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

#: The delivery body schema. Receivers switch on it.
WEBHOOK_BODY_SCHEMA: Final = "workeasy360.webhook.v1"


@dataclass(frozen=True, slots=True)
class RoutedEventDTO:
    id: UUID
    organization_id: UUID
    event_id: UUID
    source: str
    event_type: str
    event_version: int
    occurred_at: datetime
    correlation_id: UUID
    causation_id: UUID | None
    chain_depth: int
    payload: dict[str, Any]
    routing_status: str
    created_at: datetime

    @property
    def ref(self) -> str:
        return f"{self.event_type}.v{self.event_version}"

    @property
    def awaiting_routing(self) -> bool:
        from workeasy360.integration.events.models import RoutingStatus

        return self.routing_status == RoutingStatus.PENDING.value


def to_routed_dto(row: Any) -> RoutedEventDTO:
    return RoutedEventDTO(
        id=row.id,
        organization_id=row.organization_id,
        event_id=row.event_id,
        source=row.source,
        event_type=row.event_type,
        event_version=row.event_version,
        occurred_at=row.occurred_at,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        chain_depth=row.chain_depth,
        payload=row.payload,
        routing_status=row.routing_status,
        created_at=row.created_at,
    )


def routed_event(event_pk: UUID, *, lock: bool = False) -> RoutedEventDTO | None:
    from workeasy360.integration.events.models import RoutedEvent

    rows = RoutedEvent.objects.filter(id=event_pk)
    if lock:
        rows = rows.select_for_update()
    row = rows.first()
    return to_routed_dto(row) if row is not None else None


def events_older_than(cutoff: datetime, *, limit: int) -> list[UUID]:
    """Oldest-first ids of events created before ``cutoff`` - the retention
    candidates (``integration/routing/retention.py``)."""
    from workeasy360.integration.events.models import RoutedEvent

    return list(
        RoutedEvent.objects.filter(created_at__lt=cutoff)
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )


def event_summaries(pks: Iterable[UUID]) -> dict[UUID, dict[str, Any]]:
    """Public metadata (never payload) for listings - one query."""
    from workeasy360.integration.events.models import RoutedEvent

    wanted = list(set(pks))
    if not wanted:
        return {}
    return {
        row["id"]: {
            "event_id": str(row["event_id"]),
            "event_type": row["event_type"],
            "event_version": row["event_version"],
            "source": row["source"],
        }
        for row in RoutedEvent.objects.filter(id__in=wanted).values(
            "id", "event_id", "event_type", "event_version", "source"
        )
    }


def webhook_body(
    event: RoutedEventDTO,
    *,
    delivery_id: UUID,
    automation: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The ``workeasy360.webhook.v1`` delivery body.

    ``delivery_id`` changes on a manual replay; ``event.id`` never does. A
    receiver deduplicates on ``event.id``, which is exactly the key that
    stays stable across every retry and replay of one fact.
    """
    body: dict[str, Any] = {
        "schema": WEBHOOK_BODY_SCHEMA,
        "delivery_id": str(delivery_id),
        "event": {
            "id": str(event.event_id),
            "type": event.event_type,
            "version": event.event_version,
            "source": event.source,
            "organization_id": str(event.organization_id),
            "occurred_at": event.occurred_at.isoformat(),
            "correlation_id": str(event.correlation_id),
            "causation_id": str(event.causation_id) if event.causation_id else None,
            "data": event.payload,
        },
    }
    if automation:
        body["automation"] = dict(automation)
    return body


def outbox_backlog() -> dict[str, int]:
    """Outbox rows by status - the integration backlog, as counts.

    ``OutboxEvent`` is RLS-exempt infrastructure, so this reads without a
    tenant scope; it returns counts only, never an envelope.
    """
    from django.db.models import Count

    from workeasy360.integration.events.models import OutboxEvent

    return {
        row["status"]: row["n"]
        for row in OutboxEvent.objects.values("status").annotate(n=Count("id"))
    }
