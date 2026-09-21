"""Event type registry and payload validation.

R-13: schemas living in contracts/ does not by itself stop a publisher
emitting a payload that does not match. Validation happens at publish time,
inside the publisher's transaction, so a malformed event never reaches the
outbox and the failure lands on the code that caused it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from workeasy360.contracts.errors import EventSchemaError, UnknownEventType

_EVENT_TYPE_RE: Final = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

#: Past-tense check. Events are facts; a command-shaped name is an RPC in
#: disguise. This list is the common imperative prefixes we reject outright -
#: it is a guard rail, not a linguistic analyser, and review covers the rest.
_COMMAND_VERBS: Final[frozenset[str]] = frozenset(
    {
        "create",
        "update",
        "delete",
        "send",
        "make",
        "do",
        "set",
        "add",
        "remove",
        "process",
        "handle",
        "sync",
        "calculate",
        "generate",
        "issue",
        "apply",
        "run",
        "start",
        "stop",
        "get",
        "fetch",
    }
)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field of an event payload."""

    name: str
    kind: type | tuple[type, ...]
    required: bool = True

    def validate(self, payload: dict[str, Any], schema_ref: str) -> None:
        if self.name not in payload:
            if self.required:
                raise EventSchemaError(
                    f"{schema_ref}: missing required field {self.name!r}.",
                    schema_ref=schema_ref,
                    field=self.name,
                )
            return
        value = payload[self.name]
        if value is None:
            if self.required:
                raise EventSchemaError(
                    f"{schema_ref}: required field {self.name!r} is null.",
                    schema_ref=schema_ref,
                    field=self.name,
                )
            return
        if not isinstance(value, self.kind):
            expected = (
                self.kind.__name__
                if isinstance(self.kind, type)
                else "/".join(k.__name__ for k in self.kind)
            )
            raise EventSchemaError(
                f"{schema_ref}: field {self.name!r} expected {expected}, got "
                f"{type(value).__name__}.",
                schema_ref=schema_ref,
                field=self.name,
            )


@dataclass(frozen=True, slots=True)
class EventSchema:
    """A registered, versioned event contract."""

    event_type: str
    version: int
    aggregate_type: str
    fields: tuple[FieldSpec, ...] = ()
    description: str = ""

    #: Fields that may be added later without a version bump. Everything else
    #: is a breaking change (07-cross-product-integration.md section 3.3).
    allow_extra_fields: bool = False

    @property
    def schema_ref(self) -> str:
        return f"{self.event_type}.v{self.version}"

    def validate(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise EventSchemaError(
                f"{self.schema_ref}: payload must be a dict, got "
                f"{type(payload).__name__}."
            )
        for spec in self.fields:
            spec.validate(payload, self.schema_ref)
        if not self.allow_extra_fields:
            known = {f.name for f in self.fields}
            unexpected = set(payload) - known
            if unexpected:
                raise EventSchemaError(
                    f"{self.schema_ref}: unexpected fields "
                    f"{sorted(unexpected)}. Adding a field requires either "
                    f"declaring it optional or a version bump.",
                    schema_ref=self.schema_ref,
                )


class EventRegistry:
    """Maps ``<type>.v<n>`` to its schema.

    Populated at import time by declaration modules. A publish of an
    unregistered type fails rather than being silently accepted, so a typo in
    an event name is caught by the first test that publishes it (W360.E006).
    """

    def __init__(self) -> None:
        self._schemas: dict[str, EventSchema] = {}

    def register(self, schema: EventSchema) -> EventSchema:
        if not _EVENT_TYPE_RE.match(schema.event_type):
            raise ValueError(
                f"Invalid event type {schema.event_type!r}: expected "
                f"'<product>.<aggregate>.<past_tense_verb>'."
            )
        verb = schema.event_type.rsplit(".", 1)[-1]
        if verb in _COMMAND_VERBS:
            raise ValueError(
                f"Event type {schema.event_type!r} names a command. Events are "
                f"past-tense facts - 'crm.deal.won', not 'books.invoice.create'. "
                f"A command-shaped event dictates consumer behaviour and "
                f"reintroduces the coupling events remove."
            )
        if schema.schema_ref in self._schemas:
            raise ValueError(f"Duplicate event schema {schema.schema_ref!r}.")
        self._schemas[schema.schema_ref] = schema
        return schema

    def get(self, event_type: str, version: int) -> EventSchema:
        ref = f"{event_type}.v{version}"
        try:
            return self._schemas[ref]
        except KeyError:
            raise UnknownEventType(
                f"No schema registered for {ref}. Declare it in "
                f"contracts/events/ before publishing.",
                schema_ref=ref,
            ) from None

    def validate(self, event_type: str, version: int, payload: dict[str, Any]) -> None:
        self.get(event_type, version).validate(payload)

    def all_refs(self) -> list[str]:
        return sorted(self._schemas)

    def versions_of(self, event_type: str) -> list[int]:
        return sorted(
            s.version for s in self._schemas.values() if s.event_type == event_type
        )


#: The process-wide registry.
registry: Final[EventRegistry] = EventRegistry()
