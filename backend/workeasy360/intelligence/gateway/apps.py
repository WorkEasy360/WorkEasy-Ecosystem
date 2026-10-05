"""App configuration for intelligence/gateway (no tables: orchestration only)."""

from __future__ import annotations

from django.apps import AppConfig


class GatewayConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.intelligence.gateway"
    label = "ai_gateway"
    verbose_name = "Intelligence | Gateway"

    def ready(self) -> None:
        from workeasy360.integration.scheduling.services import register_dispatcher
        from workeasy360.intelligence.ai.budget import TIMER_KIND
        from workeasy360.intelligence.gateway import tasks

        # One timer per execution: an async job waiting to run, or a sync
        # reservation's lease. The consumer decides which from the row.
        register_dispatcher(
            TIMER_KIND,
            lambda item: tasks.run_execution.apply_async(args=[str(item.target_id)]),
        )
