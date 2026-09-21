"""App configuration for platform/observability."""

from __future__ import annotations

from django.apps import AppConfig


class ObservabilityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.observability"
    label = "observability"
    verbose_name = "Platform | Observability"

    def ready(self) -> None:
        from workeasy360.platform.observability.logging import configure_logging

        configure_logging()
