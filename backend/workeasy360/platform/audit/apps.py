"""App configuration for platform/audit."""

from __future__ import annotations

from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.audit"
    label = "audit"
    verbose_name = "Platform | Audit"
