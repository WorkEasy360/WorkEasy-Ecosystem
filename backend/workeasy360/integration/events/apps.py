"""App configuration for integration/events."""

from __future__ import annotations

from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.events"
    label = "events"
    verbose_name = "Integration | Events"

    def ready(self) -> None:
        # Populates the event schema registry. Publishing an unregistered
        # type raises, so this import is what makes publishing possible.
        from workeasy360.contracts.events import (  # noqa: F401
            integration_events,
            platform_events,
        )

        # Tasks are registered explicitly here rather than left to Celery's
        # lazy autodiscovery. Autodiscovery only fires when something forces
        # it, which meant the W360.E007 routing check could pass while seeing
        # an empty registry - a guarantee that silently verified nothing.
        from workeasy360.integration.events import tasks  # noqa: F401

        self._register_health_probe()
        self._register_event_publisher()

    @staticmethod
    def _register_event_publisher() -> None:
        """Supplies the ``EventPublisher`` port ``contracts/ports/events.py``
        declares, for lower platform layers that cannot import this module
        directly (F-4) - see that port's docstring."""
        from workeasy360.contracts.ports.events import register_event_publisher
        from workeasy360.integration.events.services import build_event, publish

        class _OutboxEventPublisher:
            def publish(
                self, ctx, event_type, version, *, aggregate_type, aggregate_id, payload
            ):
                publish(
                    build_event(
                        ctx,
                        event_type,
                        version,
                        aggregate_type=aggregate_type,
                        aggregate_id=aggregate_id,
                        payload=payload,
                    )
                )

        register_event_publisher(_OutboxEventPublisher())

    @staticmethod
    def _register_health_probe() -> None:
        """Report outbox lag on the deep health endpoint.

        observability is L1 and must not import L2, so the probe is
        registered rather than imported. Oldest-pending age is the primary
        pipeline SLI: depth alone is misleading, because a large draining
        backlog is healthy while a small stuck one is not.
        """
        from workeasy360.platform.observability.health import register_probe

        def outbox_probe() -> str:
            from workeasy360.integration.events.services import pending_age_seconds

            age = pending_age_seconds()
            return "empty" if age == 0 else f"oldest pending {age:.0f}s"

        register_probe("deep", "outbox", outbox_probe)

        # Phase 12: the same SLI as a scrapeable gauge, plus the backlog and
        # the dead letters nobody has replayed. OutboxEvent and
        # DeadLetterEvent are infrastructure tables (not tenant-scoped), so
        # these counts are real totals, not a tenant's view of zero.
        from workeasy360.platform.observability.health import register_gauges

        def outbox_gauges() -> list[tuple[str, dict[str, str], float]]:
            from workeasy360.contracts.enums import OutboxStatus
            from workeasy360.integration.events.models import DeadLetterEvent, OutboxEvent
            from workeasy360.integration.events.services import pending_age_seconds

            pending = OutboxEvent.objects.filter(
                status__in=[OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]
            ).count()
            dead = DeadLetterEvent.objects.filter(replayed_at__isnull=True).count()
            return [
                ("w360_outbox_pending", {}, float(pending)),
                ("w360_outbox_oldest_pending_seconds", {}, float(pending_age_seconds())),
                ("w360_outbox_dead_letters", {}, float(dead)),
            ]

        register_gauges("outbox", outbox_gauges)
