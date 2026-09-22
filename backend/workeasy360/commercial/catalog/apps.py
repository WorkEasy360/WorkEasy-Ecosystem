"""App configuration for commercial/catalog."""

from __future__ import annotations

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.catalog"
    label = "catalog"
    verbose_name = "Commercial | Catalog"

    def ready(self) -> None:
        # Populates the permission registry. A permission that is not
        # declared in code cannot be granted, so this import is what makes
        # catalog administration possible at all.
        from workeasy360.commercial.catalog import permissions  # noqa: F401
