"""App configuration for commercial/pricing."""

from __future__ import annotations

from django.apps import AppConfig


class PricingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.pricing"
    label = "pricing"
    verbose_name = "Commercial | Pricing"

    def ready(self) -> None:
        from workeasy360.commercial.pricing import permissions  # noqa: F401
