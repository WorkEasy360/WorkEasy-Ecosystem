"""App configuration for commercial/commissions (Phase 7)."""

from __future__ import annotations

from django.apps import AppConfig


class CommissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.commissions"
    label = "commissions"
    verbose_name = "Commercial | Commissions"

    def ready(self) -> None:
        from workeasy360.commercial.commissions import tasks  # noqa: F401
        from workeasy360.contracts.events import partner_events  # noqa: F401

        self._register_change_listener()

    @staticmethod
    def _register_change_listener() -> None:
        """Accrue after a sale commits - as a *listener*, never inside it.

        Registered with the same registry provisioning uses, and for the same
        reason: it is a latency optimisation, and the guarantee is the
        scheduled sweep and ``reconcile``, which derive the same answer from
        the same durable facts. The listener never raises (a commission
        problem must not fail a customer's purchase).
        """
        from workeasy360.commercial.commissions.accrual import (
            accrue_after_commercial_change,
        )
        from workeasy360.commercial.subscriptions.hooks import on_commercial_change

        on_commercial_change("commercial.commissions", accrue_after_commercial_change)
