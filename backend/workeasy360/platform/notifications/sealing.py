"""Credential-bearing template variables at rest (Phase 8 extension).

An invitation email carries a one-time link, and the link *is* the credential:
whoever holds it can join the organization. ``Invitation`` stores only the
token's hash, precisely so a database dump yields no working invitation - which
would be undone if the notification queued to deliver that link stored it in
plain JSON beside it.

So credential-bearing variables travel separately (``NotificationRequest.
sealed_context``), sealed with Fernet under ``NOTIFICATION_SEAL_KEYS`` (a key
that is not in the database), **bound** to the notification's organization and
id so ciphertext copied onto another row does not open, opened only inside the
delivery attempt, and **wiped** once delivery is final (sent or dead-lettered).
A pending notification in a dump is ciphertext; a delivered one is nothing.

The same shape ``integration/connectors/secrets.py`` uses for webhook signing
secrets, under its own key so rotating one never breaks the other.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SealedContextUnavailable(RuntimeError):
    """The sealed variables could not be opened (wrong key, tampering, or a
    ciphertext that belongs to another notification). Never carries a value."""


def _box() -> MultiFernet:
    from django.conf import settings

    keys = list(getattr(settings, "NOTIFICATION_SEAL_KEYS", []) or [])
    if not keys:
        raise SealedContextUnavailable("No notification seal key is configured.")
    return MultiFernet([Fernet(k) for k in keys])


def seal(values: dict[str, str], *, organization_id: UUID, notification_id: UUID) -> str:
    if not values:
        return ""
    payload = json.dumps(
        {
            "o": str(organization_id),
            "n": str(notification_id),
            "v": {str(k): str(v) for k, v in values.items()},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return _box().encrypt(payload).decode("ascii")


def open_sealed(
    token: str, *, organization_id: Any, notification_id: Any
) -> dict[str, str]:
    if not token:
        return {}
    try:
        payload = json.loads(_box().decrypt(token.encode("ascii")))
    except (InvalidToken, ValueError) as exc:
        raise SealedContextUnavailable("Sealed variables could not be opened.") from exc
    if payload.get("o") != str(organization_id) or payload.get("n") != str(
        notification_id
    ):
        raise SealedContextUnavailable("Sealed variables belong to another notification.")
    values = payload.get("v") or {}
    return {str(k): str(v) for k, v in values.items()}
