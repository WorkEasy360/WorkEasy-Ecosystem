"""Integration event schemas - facts the integration platform itself emits.

Workeasy360-owned (``integration.*``, see ``namespaces.PLATFORM_OWNED_ROOTS``).
Two, because two are needed and no more:

- ``integration.webhook.pinged`` is what a connection's "send test event"
  delivers. It lets an administrator prove, end to end, that the endpoint is
  reachable and that their receiver verifies the signature - before any real
  business event depends on it.
- ``integration.schedule.fired`` is the trigger event of a scheduled
  automation. Giving a schedule a real event (rather than a special case in
  the executor) means scheduled and event-driven rules run through one code
  path, one execution record and one set of action semantics.

Neither carries personal data. ``missed_occurrences`` makes the documented
missed-schedule policy (coalesce to the latest occurrence) observable to the
receiver rather than silent.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.events.registry import EventSchema, FieldSpec, registry

WEBHOOK_PINGED_V1: Final = registry.register(
    EventSchema(
        event_type="integration.webhook.pinged",
        version=1,
        aggregate_type="integration.connection",
        description="An administrator sent a test event to a connection.",
        fields=(
            FieldSpec("connection_id", str),
            FieldSpec("message", str),
        ),
    )
)

SCHEDULE_FIRED_V1: Final = registry.register(
    EventSchema(
        event_type="integration.schedule.fired",
        version=1,
        aggregate_type="integration.automation_rule",
        description="A scheduled automation reached its run time.",
        fields=(
            FieldSpec("rule_id", str),
            FieldSpec("scheduled_for", str),
            FieldSpec("missed_occurrences", int),
        ),
    )
)

ALL_INTEGRATION_SCHEMAS: Final = (WEBHOOK_PINGED_V1, SCHEDULE_FIRED_V1)
