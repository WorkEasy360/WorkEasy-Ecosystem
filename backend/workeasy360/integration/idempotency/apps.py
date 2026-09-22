"""App configuration for integration/idempotency."""

from __future__ import annotations

from django.apps import AppConfig


class IdempotencyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.idempotency"
    label = "idempotency"
    verbose_name = "Integration | Idempotency"
