"""App configuration for commercial/provisioning."""

from __future__ import annotations

from django.apps import AppConfig


class ProvisioningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.provisioning"
    label = "provisioning"
    verbose_name = "Commercial | Provisioning"

    def ready(self) -> None:
        # Tasks are registered explicitly rather than left to Celery's lazy
        # autodiscovery, which only fires when something forces it - and
        # would let the W360.E007 routing check pass while seeing an empty
        # registry.
        from workeasy360.commercial.provisioning import tasks  # noqa: F401

        self._register_change_listener()

    @staticmethod
    def _register_change_listener() -> None:
        """Provision immediately when commercial state moves.

        ``subscriptions`` cannot import ``provisioning`` - provisioning reads
        subscription state, so the import would be a cycle - so the
        dependency is inverted through an explicit registry rather than a
        Django signal (F-8). Registering here, at app-ready, keeps the wiring
        in one greppable place.

        This is a **latency optimisation, not the correctness mechanism**.
        The guarantee is ``reconcile_all``, which derives the same answer
        from the same durable state; a lost notification delays provisioning
        rather than losing it.
        """
        from workeasy360.commercial.provisioning.services import reconcile
        from workeasy360.commercial.subscriptions.hooks import on_commercial_change

        on_commercial_change("commercial.provisioning", reconcile)
