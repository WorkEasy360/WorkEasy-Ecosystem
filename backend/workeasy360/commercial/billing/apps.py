"""App configuration for commercial/billing."""

from __future__ import annotations

from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.billing"
    label = "billing"
    verbose_name = "Commercial | Billing"
