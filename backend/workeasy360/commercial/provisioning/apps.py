"""App configuration for commercial/provisioning."""

from __future__ import annotations

from django.apps import AppConfig


class ProvisioningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.provisioning"
    label = "provisioning"
    verbose_name = "Commercial | Provisioning"
