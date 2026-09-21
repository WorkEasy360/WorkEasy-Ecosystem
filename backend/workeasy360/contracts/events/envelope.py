"""The domain event envelope.

Every cross-module fact travels in this shape. The fields are not negotiable
per-event: correlation, causation and tenancy must be present on all of them
or the chain cannot be reconstructed during an incident.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final
from uuid import UUID

from workeasy360.contracts.enums import ActorKind
from workeasy360.contracts.errors import CausationDepthExceeded

#: Cycle guard. A published -> B handles -> B publishes -> A handles is
#: legal but must terminate; the dispatcher refuses anything deeper and raises
#: a P1 alert (02-dependency-rules.md section 5).
MAX_CAUSATION_DEPTH: Final[int] = 10


@dataclass(frozen=True, slots=True)
class Actor:
    """Who caused an event. ``None`` on the envelope means the system did."""

    kind: ActorKind
    user_id: UUID | None = None
    membership_id: UUID | None = None
    service_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "user_id": str(self.user_id) if self.user_id else None,
            "membership_id": str(self.membership_id) if self.membership_id else None,
            "service_name": self.service_name,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Actor | None:
        if not raw:
            return None
        return cls(
            kind=ActorKind(raw["kind"]),
            user_id=UUID(raw["user_id"]) if raw.get("user_id") else None,
            membership_id=(
                UUID(raw["membership_id"]) if raw.get("membership_id") else None
            ),
            service_name=raw.get("service_name"),
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """A past-tense business fact.

    Events are facts, never commands. ``crm.deal.won``, never
    ``books.create_invoice``: an event that names a command is a disguised RPC
    that tells the consumer what to do, which reintroduces exactly the
    coupling events exist to remove.
    """

    event_type: str
    event_version: int
    organization_id: UUID
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]

    event_id: UUID = field(default_factory=uuid.uuid4)
    occurred_at: datetime = field(default_factory=_utcnow)
    actor: Actor | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    causation_depth: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.organization_id, UUID):
            # There is no such thing as a cross-tenant domain event. Control
            # plane facts use a separate type so the two cannot be confused.
            raise TypeError("DomainEvent.organization_id must be a UUID.")
        if self.event_version < 1:
            raise ValueError("DomainEvent.event_version starts at 1.")
        if self.causation_depth < 0:
            raise ValueError("DomainEvent.causation_depth must be >= 0.")
        if self.causation_depth > MAX_CAUSATION_DEPTH:
            raise CausationDepthExceeded(
                f"{self.event_type} reached causation depth "
                f"{self.causation_depth} (max {MAX_CAUSATION_DEPTH}). "
                f"An event loop is not terminating.",
                event_type=self.event_type,
                causation_depth=self.causation_depth,
            )
        if self.occurred_at.tzinfo is None:
            raise ValueError(
                "DomainEvent.occurred_at must be timezone-aware. Naive "
                "timestamps silently compare wrongly across daylight-saving "
                "boundaries."
            )

    @property
    def schema_ref(self) -> str:
        """Canonical ``<type>.v<n>`` reference used by the registry."""
        return f"{self.event_type}.v{self.event_version}"

    def caused(
        self,
        event_type: str,
        event_version: int,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        actor: Actor | None = None,
    ) -> DomainEvent:
        """Derive a follow-on event, carrying correlation and incrementing
        causation depth so a runaway loop terminates at the guard."""
        return DomainEvent(
            event_type=event_type,
            event_version=event_version,
            organization_id=self.organization_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            actor=actor,
            correlation_id=self.correlation_id,
            causation_id=self.event_id,
            causation_depth=self.causation_depth + 1,
        )

    def to_envelope(self) -> dict[str, Any]:
        """JSON-serialisable form stored in the outbox and sent to the broker.

        The full envelope is stored so that replay needs no reconstruction and
        a dead-lettered event is self-describing.
        """
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "event_version": self.event_version,
            "schema_ref": self.schema_ref,
            "occurred_at": self.occurred_at.isoformat(),
            "organization_id": str(self.organization_id),
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "actor": self.actor.as_dict() if self.actor else None,
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "causation_depth": self.causation_depth,
            "payload": self.payload,
        }

    @classmethod
    def from_envelope(cls, raw: dict[str, Any]) -> DomainEvent:
        return cls(
            event_id=UUID(raw["event_id"]),
            event_type=raw["event_type"],
            event_version=int(raw["event_version"]),
            occurred_at=datetime.fromisoformat(raw["occurred_at"]),
            organization_id=UUID(raw["organization_id"]),
            aggregate_type=raw["aggregate_type"],
            aggregate_id=raw["aggregate_id"],
            actor=Actor.from_dict(raw.get("actor")),
            correlation_id=(
                UUID(raw["correlation_id"]) if raw.get("correlation_id") else None
            ),
            causation_id=UUID(raw["causation_id"]) if raw.get("causation_id") else None,
            causation_depth=int(raw.get("causation_depth", 0)),
            payload=raw["payload"],
        )
