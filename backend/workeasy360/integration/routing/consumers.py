"""Routed-event consumers registered by modules above ``integration``.

Webhooks and automation are routing's own consumers, called directly by
``fanout``. A module *above* ``integration`` - Phase 8's analytics, which
turns product events into metric facts - cannot be imported from here
without inverting the layering, so it registers a callable at app-ready and
``fanout`` calls every registered consumer for each event it routes: the same
registry pattern the due-work dispatcher uses.

A consumer runs **inside the routing transaction and the event's
organization scope**, so what it records commits or rolls back with the
routing of the event - one event, routed once, consumed once (a duplicate
event never reaches here; routing the same event twice is a no-op). A
consumer must therefore be cheap and database-only: it records durable work
for itself (a row and a due-work timer) and does the rest later, in its own
task, where its failures are its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Consumer = Callable[[Any], None]
_consumers: dict[str, Consumer] = {}


def register_consumer(name: str, consumer: Consumer) -> None:
    _consumers[name] = consumer


def notify(event: Any) -> None:
    for name in sorted(_consumers):
        _consumers[name](event)
