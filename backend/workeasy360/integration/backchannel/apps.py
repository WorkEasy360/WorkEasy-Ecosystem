"""App configuration for integration/backchannel."""

from __future__ import annotations

from django.apps import AppConfig


class BackchannelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.backchannel"
    label = "integration_backchannel"
    verbose_name = "Integration | Product back-channel logout"

    def ready(self) -> None:
        from workeasy360.integration.backchannel import tasks  # noqa: F401
