"""App configuration for platform/identity."""

from __future__ import annotations

from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.identity"
    label = "identity"
    verbose_name = "Platform | Identity"
