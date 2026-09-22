"""The commercial-change notification point.

``commercial/provisioning`` reads subscription state, so ``subscriptions``
cannot import it back without a cycle. The dependency is inverted with an
explicit registry - the same shape ``observability.register_probe`` uses
where a lower layer must be driven by a higher one (ADR-015).

**This is not a Django signal.** A signal fires invisibly, in an unspecified
order, and silently does nothing for bulk operations, which is why F-8 bans
them across modules. A registry is greppable (``grep -r
on_commercial_change`` is the complete list of listeners), ordered, and
registered from exactly one place.

**Losing a notification is survivable and that is deliberate.** It is a
latency optimisation, not the correctness mechanism: entitlements are also
recomputed by the scheduled reconciliation pass, which derives the same
answer from the same durable state. A listener that raises is logged and
skipped, because a failure to provision must not roll back a commercial
change that has already been committed and billed for.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Counted so a broken listener is visible before a customer reports it.
PROVISIONING_FAILURES = "commercial_provisioning_failures_total"

CommercialChangeListener = Callable[[UUID], None]

_listeners: list[tuple[str, CommercialChangeListener]] = []


def on_commercial_change(name: str, listener: CommercialChangeListener) -> None:
    """Register a listener. Idempotent on ``name``.

    Idempotent because ``AppConfig.ready`` can run more than once in a test
    process, and a listener registered twice would reconcile twice per
    change - harmless but wasteful, and it would make the query-count
    assertions lie.
    """
    for index, (existing, _) in enumerate(_listeners):
        if existing == name:
            _listeners[index] = (name, listener)
            return
    _listeners.append((name, listener))


def notify_commercial_change(organization_id: UUID) -> None:
    """Tell every listener that this organization's commercial state moved.

    Called **after** the business transaction's work is complete, never
    inside a lock or before the change is durable.
    """
    for name, listener in _listeners:
        try:
            listener(organization_id)
        except Exception as exc:
            metrics.increment(PROVISIONING_FAILURES, listener=name)
            logger.error(
                "commercial.change_listener.failed",
                listener=name,
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )


def reset_listeners() -> None:
    """Test hook only."""
    _listeners.clear()
