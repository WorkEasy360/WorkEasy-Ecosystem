"""App configuration for platform/notifications."""

from __future__ import annotations

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.notifications"
    label = "notifications"
    verbose_name = "Platform | Notifications"

    def ready(self) -> None:
        from workeasy360.platform.notifications.channels import register_default_channels

        register_default_channels()

        # Explicit task registration - see the note in integration/events.
        from workeasy360.platform.notifications import tasks  # noqa: F401
