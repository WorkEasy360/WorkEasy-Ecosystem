"""The action registry (Tasks 35-37).

An action is registered, not switched on: the engine looks up
``ActionSpec`` by the ``type`` stored in a rule and calls its two functions.
There is no ``if action_type == ...`` anywhere in the engine, so adding an
action is registering one - here, or from any module that may import this
one - with no engine change.

Every action declares a name and version, validates its configuration when
the rule is saved (so a broken rule cannot be activated), and executes only
through approved interfaces - today, creating a durable delivery or a
platform notification. **No action performs an external side effect
itself**: the effect is a record the delivery pipeline or the notification
service carries out, with its own retries and at-least-once semantics.
That is what lets an execution retry without duplicating anything
(Task 37): each action's effect is keyed on ``(execution_id, action_key)``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events.selectors import RoutedEventDTO

DONE = "done"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConfigField:
    """Describes one configuration field, for the rule editor. Documentation
    only - validation is ``ActionSpec.validate``."""

    name: str
    kind: str
    label: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ActionInvocation:
    """Everything an action may know when it runs."""

    organization_id: UUID
    execution_id: UUID
    rule_id: UUID
    rule_name: str
    action_key: str
    event: RoutedEventDTO
    config: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ActionResult:
    status: str
    #: A reference to the effect (``delivery:<id>``), never its content.
    reference: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ActionSpec:
    key: str
    version: int
    label: str
    description: str
    #: ``(ctx, raw_config) -> normalised config``; raises
    #: ``IntegrationValidationError``. Runs at save and at activation.
    validate: Callable[[TenantContext, Mapping[str, Any]], dict[str, Any]]
    #: Performs the action for one execution. Must be idempotent on
    #: ``(execution_id, action_key)``. Returns ``FAILED`` for a permanent
    #: failure; raises for a transient one (the execution is retried).
    execute: Callable[[ActionInvocation], ActionResult]
    config_fields: tuple[ConfigField, ...] = field(default_factory=tuple)


_actions: dict[str, ActionSpec] = {}


def register_action(spec: ActionSpec) -> ActionSpec:
    existing = _actions.get(spec.key)
    if existing is not None and existing is not spec:
        raise ValueError(f"Action {spec.key!r} is already registered.")
    _actions[spec.key] = spec
    return spec


def get_action(key: str) -> ActionSpec | None:
    return _actions.get(key)


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "type": spec.key,
            "version": spec.version,
            "label": spec.label,
            "description": spec.description,
            "config_fields": [
                {"name": f.name, "kind": f.kind, "label": f.label, "required": f.required}
                for f in spec.config_fields
            ],
        }
        for spec in sorted(_actions.values(), key=lambda s: s.key)
    ]


def unregister(key: str) -> None:
    """Test hook only."""
    _actions.pop(key, None)
