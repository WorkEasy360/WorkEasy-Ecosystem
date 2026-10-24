"""Connection credentials at rest: sealed, bound, rotatable (Task 4).

**Why encrypt rather than hash.** Client secrets in ``platform/ecosystem``
are stored as SHA-256 digests because the platform only ever *verifies*
them. A webhook signing secret is the opposite: the platform must *use* it
to compute an HMAC on every delivery, so it has to be recoverable - and the
narrowest recoverable form is authenticated encryption under a key that is
not in the database (12-security.md sections 5 and 6: webhook signing
secrets and connector credentials are both in the designated field-level
encryption set).

**Mechanism.** Fernet (AES-128-CBC + HMAC-SHA256, from ``cryptography``,
already a pinned dependency), under a **separate key** so rotating it never
invalidates another secret. Wrapped in ``MultiFernet`` so the *master* key can
be rotated too: new values are sealed under the first configured key, and
every configured key can open.

**Binding.** Fernet has no associated data, so the sealed plaintext carries
its own context - organization, connection and version - and opening checks
it. A database attacker who copies organization A's ciphertext onto
organization B's row gets an error, not A's secret signing B's deliveries.

This is deliberately *not* a general secrets service (Task 4: "do not build
a general secrets platform"). It seals exactly one kind of value for exactly
one table. Swapping it for KMS envelope encryption later changes this module
and nothing that calls it.
"""

from __future__ import annotations

import json
import secrets as _stdlib_secrets
from typing import Final
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

#: Prefix on generated signing secrets. Makes a leaked value recognisable to
#: secret scanners and to a human reading a config file.
SECRET_PREFIX: Final = "whsec_"  # noqa: S105 - a public prefix, not a credential
SECRET_BYTES: Final = 32  # 256 bits


class SecretUnavailable(RuntimeError):
    """A stored secret could not be opened: wrong key, tampering, or a row
    whose ciphertext does not belong to it. Never carries the value."""


def generate_secret() -> str:
    return SECRET_PREFIX + _stdlib_secrets.token_urlsafe(SECRET_BYTES)


def hint(secret: str) -> str:
    """The last four characters - enough to tell two secrets apart, far too
    few to help anyone recover one."""
    return secret[-4:]


def seal(secret: str, *, organization_id: UUID, connection_id: UUID, version: int) -> str:
    payload = json.dumps(
        {
            "o": str(organization_id),
            "c": str(connection_id),
            "v": int(version),
            "s": secret,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return _box().encrypt(payload).decode("ascii")


def unseal(
    token: str, *, organization_id: UUID, connection_id: UUID, version: int
) -> str:
    try:
        payload = json.loads(_box().decrypt(token.encode("ascii")))
    except (InvalidToken, ValueError, TypeError):
        raise SecretUnavailable("A connection secret could not be decrypted.") from None
    if (
        payload.get("o") != str(organization_id)
        or payload.get("c") != str(connection_id)
        or payload.get("v") != int(version)
    ):
        raise SecretUnavailable("A connection secret is bound to a different row.")
    return str(payload["s"])


def _box() -> MultiFernet:
    from django.conf import settings

    keys = list(settings.INTEGRATION_SECRET_KEYS)
    if not keys:
        raise SecretUnavailable("No integration secret key is configured.")
    return MultiFernet([Fernet(key) for key in keys])
