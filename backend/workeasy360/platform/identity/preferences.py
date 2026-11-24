"""A person's own notification choices on WorkEasy360 Accounts (ADR-075).

Personal, across every organization: an organization's console alerts stay
per membership in User Admin (``notifications.preferences``). Security and
recovery notices are **required**: they are always sent and cannot be turned
off, whatever else is chosen (Accounts V3 section 10.1).

A row exists only for a choice that differs from nothing at all: no row means
the default below, so a new choice needs no backfill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from django.db import transaction
from django.utils import timezone


@dataclass(frozen=True, slots=True)
class Choice:
    key: str
    label: str
    description: str
    default: bool
    required: bool = False


SECURITY_NOTICES: Final = "security_notices"
NEW_SIGN_IN_ALERTS: Final = "new_sign_in_alerts"
PRODUCT_NEWS: Final = "product_news"

CHOICES: Final[tuple[Choice, ...]] = (
    Choice(
        SECURITY_NOTICES,
        "Security and recovery notices",
        "Password, sign-in email, two-step verification and account changes, "
        "and the links you ask for. Always sent.",
        default=True,
        required=True,
    ),
    Choice(
        NEW_SIGN_IN_ALERTS,
        "New sign-in alerts",
        "An email when your account is signed in to from a new device.",
        default=True,
    ),
    Choice(
        PRODUCT_NEWS,
        "Product news and tips",
        "Occasional emails about new WorkEasy360 features. Never needed for "
        "your account to work.",
        default=False,
    ),
)
_BY_KEY: Final = {c.key: c for c in CHOICES}


def list_choices(user_id: Any) -> list[dict[str, Any]]:
    from workeasy360.platform.identity.models import PersonalPreference

    stored = dict(
        PersonalPreference.objects.filter(user_id=user_id).values_list("key", "enabled")
    )
    return [
        {
            "key": c.key,
            "label": c.label,
            "description": c.description,
            "enabled": True if c.required else bool(stored.get(c.key, c.default)),
            "required": c.required,
        }
        for c in CHOICES
    ]


def enabled(user_id: Any, key: str) -> bool:
    from workeasy360.platform.identity.models import PersonalPreference

    choice = _BY_KEY[key]
    if choice.required:
        return True
    stored = (
        PersonalPreference.objects.filter(user_id=user_id, key=key)
        .values_list("enabled", flat=True)
        .first()
    )
    return choice.default if stored is None else bool(stored)


def update_choices(
    user_id: Any, changes: Any, *, ip_prefix: str = "", device_label: str = ""
) -> list[dict[str, Any]]:
    """Apply ``{key: bool}``. Every key is checked before anything is written:
    an unknown key, a non-boolean, or a required notice refuses the whole
    request."""
    from workeasy360.contracts.errors import PreferenceInvalid
    from workeasy360.platform.identity.account import record_account_event
    from workeasy360.platform.identity.models import AccountEvent, PersonalPreference

    if not isinstance(changes, dict) or not changes:
        raise PreferenceInvalid("Send at least one choice.", field="body")
    for key, value in changes.items():
        choice = _BY_KEY.get(key)
        if choice is None or not isinstance(value, bool):
            raise PreferenceInvalid("That choice does not exist.", field=str(key)[:64])
        if choice.required and value is not True:
            raise PreferenceInvalid(
                "Security and recovery notices are always sent.", field=key
            )
    before = {row["key"]: row["enabled"] for row in list_choices(user_id)}
    changed = [k for k, v in changes.items() if before.get(k) != v]
    if changed:
        with transaction.atomic():
            for key in changed:
                if _BY_KEY[key].required:
                    continue
                PersonalPreference.objects.update_or_create(
                    user_id=user_id,
                    key=key,
                    defaults={"enabled": changes[key], "updated_at": timezone.now()},
                )
            record_account_event(
                user_id,
                AccountEvent.Kind.NOTIFICATIONS_CHANGED,
                reason=",".join(sorted(changed))[:64],
                ip_prefix=ip_prefix,
                device_label=device_label,
            )
    return list_choices(user_id)
