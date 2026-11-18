"""A member's own choice of which alerts the organization console shows them
(User Admin Panel, 2026-11-11).

The console's bell lists alerts worked out from live state (a renewal due, a payment
waiting, a low balance...). Each member may turn a category off for themselves. The
choice is a ``NotificationPreference`` row on the ``in_app`` channel, keyed
``console.alert.<category>``; no row means the category is on, so a new category
needs no backfill. Only the caller's own membership is ever read or written.
"""

from __future__ import annotations

from typing import Any, Final

from django.db import transaction

from workeasy360.contracts.enums import NotificationChannel
from workeasy360.contracts.errors import MembershipValidationError
from workeasy360.contracts.values.tenant import TenantContext

PREFIX: Final = "console.alert."

#: (key, label, what it covers) - in the order the settings page lists them.
CATEGORIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("setup", "Product setup", "A product needs details from you before it can be used."),
    ("renewal", "Renewal reminders", "A subscription or trial renews or ends soon."),
    ("payment", "Payment updates", "An order or invoice is waiting for payment."),
    (
        "low_balance",
        "Low-balance alerts",
        "A wallet or AI credit balance is running low.",
    ),
    ("ticket", "Support ticket updates", "Support replied to a ticket you opened."),
)
KEYS: Final = tuple(key for key, _label, _text in CATEGORIES)


def _rows(ctx: TenantContext) -> Any:
    from workeasy360.platform.notifications.models import NotificationPreference

    return NotificationPreference.objects.filter(
        membership_id=ctx.membership_id,
        channel=NotificationChannel.IN_APP.value,
        template_key__startswith=PREFIX,
    )


def in_app_enabled(ctx: TenantContext) -> dict[str, bool]:
    """Category -> shown, for the caller."""
    chosen = {
        row.template_key[len(PREFIX) :]: row.enabled
        for row in _rows(ctx).only("template_key", "enabled")
    }
    return {key: chosen.get(key, True) for key in KEYS}


def read(ctx: TenantContext) -> dict[str, Any]:
    enabled = in_app_enabled(ctx)
    return {
        "categories": [
            {"key": key, "label": label, "description": text, "in_app": enabled[key]}
            for key, label, text in CATEGORIES
        ]
    }


def update(ctx: TenantContext, categories: Any) -> dict[str, Any]:
    """Set the caller's own choices. Unknown keys and non-boolean values are refused;
    a category left out keeps its current setting."""
    from workeasy360.platform.notifications.models import NotificationPreference

    if not isinstance(categories, list):
        raise MembershipValidationError("Send a list of categories.", field="categories")
    wanted: dict[str, bool] = {}
    for item in categories:
        if not isinstance(item, dict):
            raise MembershipValidationError(
                "Each category is an object.", field="categories"
            )
        key, value = item.get("key"), item.get("in_app")
        if key not in KEYS:
            raise MembershipValidationError("Unknown alert category.", field="categories")
        if not isinstance(value, bool):
            raise MembershipValidationError(
                "in_app must be true or false.", field="in_app"
            )
        wanted[key] = value
    with transaction.atomic():
        for key, value in wanted.items():
            NotificationPreference.objects.update_or_create(
                membership_id=ctx.membership_id,
                template_key=f"{PREFIX}{key}",
                channel=NotificationChannel.IN_APP.value,
                defaults={"enabled": value},
            )
    return read(ctx)
