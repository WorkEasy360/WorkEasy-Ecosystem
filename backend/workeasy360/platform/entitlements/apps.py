"""App configuration for platform/entitlements."""

from __future__ import annotations

from django.apps import AppConfig


class EntitlementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.entitlements"
    label = "entitlements"
    verbose_name = "Platform | Entitlements"

    def ready(self) -> None:
        # Registers this module's concrete EntitlementService/Gate into the
        # contracts-level runtime registry, so a product can reach it
        # through contracts.ports.entitlements alone - see that module's
        # "runtime registry" section for why this indirection exists.
        from workeasy360.contracts.ports.entitlements import register_gate
        from workeasy360.platform.entitlements.services.read import entitlements

        register_gate(entitlements)
