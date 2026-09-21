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
        from workeasy360.contracts.events import platform_events  # noqa: F401

        # Tasks are registered explicitly here rather than left to Celery's
        # lazy autodiscovery. Autodiscovery only fires when something forces
        # it, which meant the W360.E007 routing check could pass while seeing
        # an empty registry - a guarantee that silently verified nothing.
        from workeasy360.integration.events import tasks  # noqa: F401

        self._register_health_probe()

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
