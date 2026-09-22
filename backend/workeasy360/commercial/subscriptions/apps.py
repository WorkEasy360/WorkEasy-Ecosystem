"""App configuration for commercial/subscriptions."""

from __future__ import annotations

from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.subscriptions"
    label = "subscriptions"
    verbose_name = "Commercial | Subscriptions"

    def ready(self) -> None:
        # Populates the event schema registry. Publishing an unregistered
        # type raises, so this import is what makes the commercial events
        # publishable at all.
        from workeasy360.commercial.subscriptions import (
            permissions,  # noqa: F401
            tasks,  # noqa: F401
        )
        from workeasy360.contracts.events import commercial_events  # noqa: F401
