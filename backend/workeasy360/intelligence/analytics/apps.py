"""App configuration for intelligence/analytics."""

from __future__ import annotations

from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.intelligence.analytics"
    label = "analytics"
    verbose_name = "Intelligence | Analytics"

    def ready(self) -> None:
        from workeasy360.integration.routing.consumers import register_consumer
        from workeasy360.integration.scheduling.services import register_dispatcher
        from workeasy360.intelligence.analytics import permissions, tasks  # noqa: F401
        from workeasy360.intelligence.analytics.consumer import (
            PROJECT_TIMER,
            on_routed_event,
        )

        register_consumer("analytics", on_routed_event)
        register_dispatcher(
            PROJECT_TIMER,
            lambda item: tasks.project.apply_async(args=[str(item.target_id)]),
        )
