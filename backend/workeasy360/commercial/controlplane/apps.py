"""App configuration for commercial/controlplane (Phase 7).

The control plane owns no tables. It is an installed app only so Django finds
its management command; its services are ordinary modules.
"""

from __future__ import annotations

from django.apps import AppConfig


class ControlPlaneConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.controlplane"
    label = "controlplane"
    verbose_name = "Commercial | Control plane"
