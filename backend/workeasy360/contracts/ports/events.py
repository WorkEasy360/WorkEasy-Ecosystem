"""How a lower platform layer publishes a domain event it does not own the
table for.

``platform/organizations`` is L1; the transactional outbox
(``integration/events``) is L2. Dependencies point downward only (F-4), so
``platform/organizations`` cannot import ``integration.events`` to write the
``OutboxEvent`` row itself - the same forbidden-edge shape ``TenantResolver``
(``platform/tenancy/resolution.py``) and ``SigningKeyProvider``
(``contracts/ports/signing.py``) already solve.

The fix is the same one: a narrow protocol declared where every layer can see
it, implemented and registered by the owning module at app-ready. No import
edge is created, so the linter stays satisfied and the layering is real.

Publishing an event through this port still happens **inside the caller's own
transaction** (D3): registration only removes the import, not the atomicity -
``integration.events.services.publish`` still runs synchronously, in-process,
as the registered callable.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from workeasy360.contracts.values.tenant import TenantContext


@runtime_checkable
class EventPublisher(Protocol):
    """Publishes one domain event to the outbox, in the caller's transaction."""

    def publish(
        self,
        ctx: TenantContext,
        event_type: str,
        version: int,
        *,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Build and write the event. Must be called inside an open transaction.

        Mirrors ``integration.events.services.build_event`` + ``publish``'s
        combined signature exactly, so the registered implementation is a
        one-line adapter. ``payload`` is validated against the registered
        schema by the implementation; a caller here need not import the
        schema registry itself.
        """
        ...


_publisher: EventPublisher | None = None


def register_event_publisher(publisher: EventPublisher) -> None:
    """Install the publisher. Called once, from ``integration.events``'s
    ``AppConfig.ready()``."""
    global _publisher
    _publisher = publisher


def get_event_publisher() -> EventPublisher | None:
    """The registered publisher, or ``None``.

    Unlike :func:`workeasy360.contracts.ports.signing.get_signing_key_provider`,
    this does not raise when unregistered: a domain event is an
    integration-layer *facility*, not a security-critical dependency, and a
    platform service publishing "for other consumers, if any exist" must not
    fail its own transaction merely because ``integration.events`` was not
    installed (e.g. a minimal deployment, or a unit test that only loads
    ``platform``). Callers that publish a `required` event should check for
    `None` and decide their own failure policy explicitly.
    """
    return _publisher


def reset_event_publisher() -> None:
    """Test hook only."""
    global _publisher
    _publisher = None
