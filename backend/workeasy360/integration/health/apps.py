"""App configuration for integration/health."""

from __future__ import annotations

from django.apps import AppConfig


class HealthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.health"
    label = "integration_health"
    verbose_name = "Integration | Product health"

    def ready(self) -> None:
        from workeasy360.integration.health import tasks  # noqa: F401
