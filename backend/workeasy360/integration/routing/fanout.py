"""Fan-out: one accepted event -> deliveries + executions (Task 12).

A fixed number of queries whatever the organization's subscription and
rule counts (Tasks 73, 74): the event's filters are reduced to at most two
candidate strings (``contracts.events.namespaces.candidate_filters``), and
each matching step is one indexed ``IN`` query, one bulk insert and one
read-back. Routing the same event twice (a duplicate dispatch, a retry after
a crash) is a no-op: the event row is locked and must still be ``pending``,
and both inserts are idempotent by unique constraint.

Runs inside the event's organization scope and one transaction, so an event
is either fully routed - deliveries, executions and their timers all
committed - or not routed at all and retried by its timer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from workeasy360.contracts.events.namespaces import candidate_filters
from workeasy360.integration.automation import selectors as automation_selectors
from workeasy360.integration.automation import services as automation
from workeasy360.integration.connectors import selectors as connections
from workeasy360.integration.events import selectors as events
from workeasy360.integration.events.services import mark_routed
from workeasy360.integration.routing import consumers
from workeasy360.integration.routing.ingestion import ROUTING_TIMER
from workeasy360.integration.scheduling import services as scheduling
from workeasy360.integration.webhooks import selectors as webhook_selectors
from workeasy360.integration.webhooks import services as webhooks
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Routed:
    status: str
    deliveries: list[UUID] = field(default_factory=list)
    executions: list[UUID] = field(default_factory=list)


def route_event(event_pk: Any) -> Routed:
    try:
        key = UUID(str(event_pk))
    except ValueError:
        return Routed("missing")

    event = events.routed_event(key, lock=True)
    if event is None:
        scheduling.complete(ROUTING_TIMER, key)
        return Routed("missing")
    if not event.awaiting_routing:
        scheduling.complete(ROUTING_TIMER, key)
        return Routed("already_routed")

    filters = candidate_filters(event.event_type, event.event_version)
    subscribed = webhook_selectors.subscribed_connection_ids(filters)
    # Only *active* connections receive fan-out; a draft or suspended one's
    # subscriptions are inert until it is activated (it is not backfilled).
    targets = connections.active_connection_ids(subscribed)
    deliveries = webhooks.create_event_deliveries(event, targets)

    rules = automation_selectors.matching_rules(filters)
    executions = automation.create_executions(event, rules)

    # Consumers above integration/ (Phase 8 analytics), in this transaction.
    consumers.notify(event)

    mark_routed(event.id, deliveries=len(targets), rules=len(rules))
    scheduling.complete(ROUTING_TIMER, key)

    metrics.observe("integration_routing_fanout", float(len(targets) + len(rules)))
    logger.info(
        "integration.event.routed",
        event_id=str(event.event_id),
        event_type=event.event_type,
        correlation_id=str(event.correlation_id),
        connections=len(targets),
        rules=len(rules),
        chain_depth=event.chain_depth,
    )
    return Routed("routed", deliveries=deliveries, executions=executions)
