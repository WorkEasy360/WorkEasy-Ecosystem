"""App configuration for platform/entitlements."""

from __future__ import annotations

from django.apps import AppConfig


class EntitlementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.entitlements"
    label = "entitlements"
    verbose_name = "Platform | Entitlements"
