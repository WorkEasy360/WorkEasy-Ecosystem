"""App configuration for integration/automation."""

from __future__ import annotations

from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.integration.automation"
    label = "automation"
    verbose_name = "Integration | Automation"

    def ready(self) -> None:
        from workeasy360.integration.automation import permissions, tasks  # noqa: F401
        from workeasy360.integration.automation.builtin_actions import (
            register_builtin_actions,
            register_notification_template,
        )
        from workeasy360.integration.automation.services import (
            EXECUTION_TIMER,
            SCHEDULE_TIMER,
        )
        from workeasy360.integration.scheduling.services import register_dispatcher

        register_builtin_actions()
        register_notification_template()
        register_dispatcher(
            EXECUTION_TIMER,
            lambda item: tasks.run_execution.apply_async(args=[str(item.target_id)]),
        )
        register_dispatcher(
            SCHEDULE_TIMER,
            lambda item: tasks.fire_schedule.apply_async(args=[str(item.target_id)]),
        )
