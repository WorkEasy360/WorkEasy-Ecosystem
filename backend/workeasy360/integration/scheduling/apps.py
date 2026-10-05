"""App configuration for integration/scheduling."""

from __future__ import annotations

from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.scheduling"
    label = "scheduling"
    verbose_name = "Integration | Scheduling"

    def ready(self) -> None:
        from workeasy360.integration.scheduling import tasks  # noqa: F401
        from workeasy360.platform.observability.health import register_probe

        def due_work_probe() -> str:
            from workeasy360.integration.scheduling.services import overdue_seconds

            lag = overdue_seconds()
            return "on time" if lag == 0 else f"oldest due {lag:.0f}s ago"

        # "deep", never readiness: integration work is Tier 3. A backlog of
        # webhook deliveries is something an operator must see, not a reason
        # to pull a healthy instance out of the load balancer (Task 71).
        register_probe("deep", "integration", due_work_probe)

        # Phase 12: webhook retries, automation, AI jobs, indexing and payment
        # checkouts all run off this timer, so its lag is the integration
        # backlog SLI. DueWork is an RLS-exempt timer table (ADR-024).
        from workeasy360.platform.observability.health import register_gauges

        def due_work_gauges() -> list[tuple[str, dict[str, str], float]]:
            from django.utils import timezone

            from workeasy360.integration.scheduling.models import DueWork
            from workeasy360.integration.scheduling.services import overdue_seconds

            overdue = DueWork.objects.filter(due_at__lte=timezone.now()).count()
            return [
                ("w360_due_work_overdue", {}, float(overdue)),
                ("w360_due_work_oldest_overdue_seconds", {}, float(overdue_seconds())),
            ]

        register_gauges("due_work", due_work_gauges)
