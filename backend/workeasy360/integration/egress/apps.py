"""App configuration for integration/egress.

Egress has no models. It is an app only so that, at start-up, it can hand the
files platform the adapters that need a socket (the ``clamd`` malware scanner):
the files platform sits below integration and may not import them itself.
"""

from __future__ import annotations

from django.apps import AppConfig


class EgressConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.egress"
    label = "integration_egress"
    verbose_name = "Integration | Outbound boundary"

    def ready(self) -> None:
        from workeasy360.integration.egress.clamd import from_settings
        from workeasy360.platform.files.scanning import register_scanner

        register_scanner("clamd", from_settings)
