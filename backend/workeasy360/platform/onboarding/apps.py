"""App configuration for platform/onboarding (Phase 5, ADR-023)."""

from __future__ import annotations

from django.apps import AppConfig


class OnboardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.onboarding"
    label = "onboarding"
    verbose_name = "Platform | Onboarding"
