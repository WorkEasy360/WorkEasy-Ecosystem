"""Platform settings: the few switches a Super Admin changes without a
deployment (Phase 9).

One row (``id = 1``), the same shape as ``notifications.CommunicationSettings``.
Everything here is **public-facing configuration**, never a secret:

- ``signup_enabled`` - the self-service signup switch, for closing the front
  door during abuse without a deployment;
- ``support_email`` / ``sales_email`` - the addresses the public site, the
  Support page and account emails show;
- ``public_catalog_currency`` - which currency the public catalog prices in by
  default (a visitor can switch among the currencies that have prices).

Read by the public site endpoint, written only by
``platform.administration.site.update_settings`` (``platform.staff_settings.manage``,
dangerous, audited, optimistic version).
"""

from __future__ import annotations

from django.db import models


class PlatformSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    signup_enabled = models.BooleanField(default=True)
    support_email = models.EmailField(max_length=254, blank=True, default="")
    sales_email = models.EmailField(max_length=254, blank=True, default="")
    #: ISO 4217, or empty for the deployment default
    #: (``PUBLIC_CATALOG_DEFAULT_CURRENCY``).
    public_catalog_currency = models.CharField(max_length=3, blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    updated_by_id = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "administration_platform_settings"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1), name="ck_platform_settings_singleton"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return "platform settings"
