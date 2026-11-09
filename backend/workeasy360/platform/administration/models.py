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

import uuid

from django.db import models


class PlatformSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    signup_enabled = models.BooleanField(default=True)
    support_email = models.EmailField(max_length=254, blank=True, default="")
    sales_email = models.EmailField(max_length=254, blank=True, default="")
    #: ISO 4217, or empty for the deployment default
    #: (``PUBLIC_CATALOG_DEFAULT_CURRENCY``).
    public_catalog_currency = models.CharField(max_length=3, blank=True, default="")
    #: Whether the public partner application form accepts applications.
    partner_applications_enabled = models.BooleanField(default=True, db_default=True)
    #: The company behind the platform, as invoices and emails name it.
    company_name = models.CharField(max_length=200, blank=True, default="", db_default="")
    company_legal_name = models.CharField(max_length=200, blank=True, default="", db_default="")
    company_address = models.CharField(max_length=500, blank=True, default="", db_default="")
    company_tax_id = models.CharField(max_length=64, blank=True, default="", db_default="")
    #: The tax rate in basis points, or empty for the deployment's default.
    tax_rate_bp = models.PositiveIntegerField(null=True, blank=True)
    #: Where staff are told about new applications and tickets. Empty: nobody.
    notifications_email = models.CharField(max_length=254, blank=True, default="", db_default="")
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


class HelpArticle(models.Model):
    """One FAQ or setup guide. Plain text, never HTML. Archived rather than deleted."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=80, unique=True)
    kind = models.CharField(max_length=16, default="faq")
    audience = models.CharField(max_length=12, default="all")
    status = models.CharField(max_length=12, default="draft")
    title = models.CharField(max_length=200)
    body = models.TextField()
    product_code = models.CharField(max_length=64, blank=True, default="")
    display_order = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "administration_help_article"
        indexes = [
            models.Index(fields=["status", "audience", "display_order"], name="ix_help_reader"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.slug
