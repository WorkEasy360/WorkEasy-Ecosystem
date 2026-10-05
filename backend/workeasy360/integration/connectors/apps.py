"""App configuration for integration/connectors."""

from __future__ import annotations

from django.apps import AppConfig


class ConnectorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.connectors"
    label = "connectors"
    verbose_name = "Integration | Connectors"

    def ready(self) -> None:
        from workeasy360.integration.connectors import permissions  # noqa: F401
        from workeasy360.integration.connectors.providers.webhook import (
            WEBHOOK_CONNECTOR,
        )
        from workeasy360.integration.connectors.registry import register_connector

        register_connector(WEBHOOK_CONNECTOR)
