"""Platform settings and the public site's configuration (Phase 9).

``public_site`` is what an anonymous visitor's browser may know: whether it can
sign up, where to write, and the catalog's default currency. It is cached for a
short time because every public page asks for it and it changes a few times a
year; a change reaches visitors within ``CACHE_SECONDS``.

The Super Admin reads and changes the row through ``settings_view`` /
``update_settings``: ``platform.staff_settings.view`` / ``.manage`` (dangerous,
AAL 2, audited with the values before and after, optimistic version).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction

from workeasy360.contracts.errors import SecurityPolicyValidationError
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

VIEW = "platform.staff_settings.view"
MANAGE = "platform.staff_settings.manage"

CACHE_KEY = "w360:site:settings:v1"
CACHE_SECONDS = 30


class SettingsConflict(SecurityPolicyValidationError):
    code = "concurrent_edit"
    http_status = 409


def _row() -> Any:
    from workeasy360.platform.administration.models import PlatformSettings

    return PlatformSettings.objects.filter(id=1).first()


def _values(row: Any) -> dict[str, Any]:
    currency = (row.public_catalog_currency if row else "") or str(
        settings.PUBLIC_CATALOG_DEFAULT_CURRENCY
    )
    return {
        "signup_enabled": bool(row.signup_enabled) if row else True,
        "support_email": row.support_email if row else "",
        "sales_email": row.sales_email if row else "",
        "catalog_currency": currency,
    }


def public_site() -> dict[str, Any]:
    """The public subset. Cached briefly; a cache outage falls back to the
    database rather than failing a public page."""
    try:
        cached = cache.get(CACHE_KEY)
    except Exception:
        cached = None
    if isinstance(cached, dict):
        return cached
    values = _values(_row())
    try:
        cache.set(CACHE_KEY, values, CACHE_SECONDS)
    except Exception as exc:
        logger.warning("site.settings.cache_unavailable", error_type=type(exc).__name__)
    return values


def signup_enabled() -> bool:
    return bool(public_site()["signup_enabled"])


def support_email() -> str:
    return str(public_site()["support_email"] or settings.DEFAULT_FROM_EMAIL)


@in_scope
def settings_view(ctx: TenantContext) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    row = _row()
    return {
        **_values(row),
        "public_catalog_currency": row.public_catalog_currency if row else "",
        "default_currency": str(settings.PUBLIC_CATALOG_DEFAULT_CURRENCY),
        "currencies": [code.value for code in CurrencyCode],
        "version": row.version if row else 0,
    }


def _email(raw: Any, field: str) -> str:
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        validate_email(value)
    except ValidationError as exc:
        raise SecurityPolicyValidationError(
            "Enter a valid email address or leave it empty.", field=field
        ) from exc
    return value[:254]


@in_scope
def update_settings(
    ctx: TenantContext, body: dict[str, Any], *, expected_version: int
) -> dict[str, Any]:
    """Replace the settings. Every field is validated before the row is
    touched; the audit entry records what changed, before and after."""
    from workeasy360.platform.administration.models import PlatformSettings

    authz.require(ctx, MANAGE)
    signup = body.get("signup_enabled")
    if not isinstance(signup, bool):
        raise SecurityPolicyValidationError(
            "signup_enabled must be true or false.", field="signup_enabled"
        )
    currency = str(body.get("public_catalog_currency") or "").strip().upper()
    if currency and currency not in {code.value for code in CurrencyCode}:
        raise SecurityPolicyValidationError(
            "Choose a supported currency or leave it empty.",
            field="public_catalog_currency",
        )
    after = {
        "signup_enabled": signup,
        "support_email": _email(body.get("support_email"), "support_email"),
        "sales_email": _email(body.get("sales_email"), "sales_email"),
        "public_catalog_currency": currency,
    }
    with transaction.atomic():
        row = PlatformSettings.objects.select_for_update().filter(id=1).first()
        current = row.version if row is not None else 0
        if current != expected_version:
            raise SettingsConflict(
                "Someone else changed the platform settings. Reload and try again.",
                field="version",
            )
        if row is None:
            row = PlatformSettings(id=1)
        before = {key: getattr(row, key) for key in after}
        for key, value in after.items():
            setattr(row, key, value)
        row.version = current + 1
        row.updated_by_id = ctx.user_id
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError as exc:
            raise SettingsConflict(
                "Someone else changed the platform settings. Reload and try again.",
                field="version",
            ) from exc
        audit.record(
            ctx,
            "platform.settings_updated",
            resource_type="administration.platform_settings",
            resource_id="1",
            changes={
                "before": {k: v for k, v in before.items() if v != after[k]},
                "after": {k: v for k, v in after.items() if v != before[k]},
            },
        )
    try:
        cache.delete(CACHE_KEY)
    except Exception as exc:
        logger.warning("site.settings.cache_unavailable", error_type=type(exc).__name__)
    return settings_view(ctx)
