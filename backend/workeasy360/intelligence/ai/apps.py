"""App configuration for intelligence/ai."""

from __future__ import annotations

from django.apps import AppConfig


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.intelligence.ai"
    label = "ai"
    verbose_name = "Intelligence | AI gateway"

    def ready(self) -> None:
        from workeasy360.intelligence.ai import permissions  # noqa: F401
        from workeasy360.intelligence.ai.health import provider_health
        from workeasy360.platform.observability.health import register_probe

        # Deep health only: AI is Tier 3 and never gates readiness (Task 53).
        register_probe("deep", "ai_providers", provider_health)
