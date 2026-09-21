"""App configuration for platform/files."""

from __future__ import annotations

from django.apps import AppConfig


class FilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.files"
    label = "files"
    verbose_name = "Platform | Files"
