"""App configuration for integration/webhooks."""

from __future__ import annotations

from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.webhooks"
    label = "webhooks"
    verbose_name = "Integration | Webhooks"

    def ready(self) -> None:
        from workeasy360.integration.scheduling.services import register_dispatcher
        from workeasy360.integration.webhooks import permissions, tasks  # noqa: F401
        from workeasy360.integration.webhooks.services import TIMER_KIND

        register_dispatcher(
            TIMER_KIND, lambda item: tasks.deliver.apply_async(args=[str(item.target_id)])
        )
