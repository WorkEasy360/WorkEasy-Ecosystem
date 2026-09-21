"""Event dispatch: outbox row -> broker, and broker -> handlers.

Phase 1 ships the in-process dispatcher plus a broker publish. The extraction
path (ADR-008) is that the relay's *destination* changes and the publishing
code does not: today it publishes to an in-process registry of handlers, and
on extraction it publishes to a real topic that external services consume.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from workeasy360.contracts.errors import CausationDepthExceeded
from workeasy360.contracts.events.envelope import MAX_CAUSATION_DEPTH, DomainEvent
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[DomainEvent], None]

#: schema_ref -> [(consumer_name, handler)]
_handlers: dict[str, list[tuple[str, EventHandler]]] = defaultdict(list)


def subscribe(
    event_type: str, version: int, *, consumer: str
) -> Callable[[EventHandler], EventHandler]:
    """Register a handler for one versioned event type.

    ``consumer`` is the dedupe identity. Two handlers of the same event have
    different consumer names and therefore retry independently.
    """

    def decorator(handler: EventHandler) -> EventHandler:
        _handlers[f"{event_type}.v{version}"].append((consumer, handler))
        return handler

    return decorator


def handlers_for(schema_ref: str) -> list[tuple[str, EventHandler]]:
    return list(_handlers.get(schema_ref, []))


def publish_to_broker(row: Any) -> None:
    """Send one outbox row onward.

    In Phase 1 this dispatches in-process. The broker round trip itself is
    exercised by the R-09 spike rather than put on the relay's critical path
    before a broker decision is made.
    """
    event = DomainEvent.from_envelope(row.envelope)

    if event.causation_depth > MAX_CAUSATION_DEPTH:
        raise CausationDepthExceeded(
            f"{event.event_type} exceeded causation depth "
            f"{MAX_CAUSATION_DEPTH}; refusing to dispatch.",
            event_type=event.event_type,
        )

    dispatch(event)


def dispatch(event: DomainEvent) -> dict[str, str]:
    """Deliver to every subscribed handler, isolating failures.

    One consumer's failure never blocks another's: each (event, consumer) pair
    is a separate unit of work. A raising handler is recorded and the next one
    still runs (09-failure-resilience.md section 3.6).
    """
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.tenancy.context import tenant_context

    results: dict[str, str] = {}
    subscribed = handlers_for(event.schema_ref)
    if not subscribed:
        # Normal and expected: an event with no consumer is simply unconsumed.
        # An org without Books has no Books handler acting - no error, no
        # special case (07-cross-product-integration.md section 5).
        return results

    for consumer, handler in subscribed:
        try:
            # Handlers run inside the event's tenant context so that any
            # tenant query they make is scoped without them arranging it.
            ctx = TenantContext(
                organization_id=event.organization_id,
                membership_id=event.actor.membership_id
                if event.actor and event.actor.membership_id
                else event.organization_id,
                user_id=event.actor.user_id
                if event.actor and event.actor.user_id
                else event.organization_id,
            )
            with tenant_context(ctx):
                handler(event)
            results[consumer] = "applied"
        except Exception as exc:
            results[consumer] = f"failed:{type(exc).__name__}"
            logger.error(
                "event.handler.failed",
                event_type=event.event_type,
                event_id=str(event.event_id),
                consumer=consumer,
                error_type=type(exc).__name__,
            )
    return results


def reset_handlers() -> None:
    """Test hook only."""
    _handlers.clear()
