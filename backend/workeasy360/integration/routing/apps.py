"""App configuration for integration/routing. No models: this module owns
behaviour (ingestion, fan-out, retention), and its tasks must be registered
at app-ready like every other module's (W360.E007)."""

from __future__ import annotations

from django.apps import AppConfig


class RoutingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.routing"
    label = "routing"
    verbose_name = "Integration | Routing"

    def ready(self) -> None:
        from workeasy360.integration.routing import tasks
        from workeasy360.integration.routing.ingestion import ROUTING_TIMER
        from workeasy360.integration.scheduling.services import register_dispatcher

        register_dispatcher(
            ROUTING_TIMER,
            lambda item: tasks.route_event.apply_async(args=[str(item.target_id)]),
        )
