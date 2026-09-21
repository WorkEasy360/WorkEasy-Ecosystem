"""App configuration for platform/organizations."""

from __future__ import annotations

from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.organizations"
    label = "organizations"
    verbose_name = "Platform | Organizations"

    def ready(self) -> None:
        # Supplies the TenantResolver that tenancy (P0) declares but cannot
        # implement, because organizations is P1 and upward imports are
        # forbidden (F-5). Runtime registration, no import edge.
        from workeasy360.platform.organizations.resolver import register

        register()
