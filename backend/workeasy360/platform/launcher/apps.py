"""App configuration for platform/launcher."""

from __future__ import annotations

from django.apps import AppConfig


class LauncherConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.launcher"
    label = "launcher"
    verbose_name = "Platform | Launcher"
