"""One-time account links: email verification and password reset (Phase 9).

Pre-tenant, like the rest of ``platform/identity``. This module mints and
redeems ``AccountToken`` rows and nothing else - what a redeemed link *does*
(activate an account, set a password, create an organization, send a notice)
is the self-service orchestration's job in ``platform/administration``.

The raw token exists in exactly two places: the return value of :func:`issue`
(handed straight to a sealed notification) and the browser that follows the
link. Only its SHA-256 is stored, and a lookup is by that hash.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.errors import AccountLinkInvalid

_TOKEN_BYTES = 32
#: Longest token :func:`redeem` looks up; anything longer was never ours.
MAX_TOKEN_LENGTH = 128


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(
    user_id: Any, purpose: str, *, ttl: timedelta, organization_name: str = ""
) -> str:
    """Mint a link for ``purpose`` and retire the account's earlier unused
    ones of the same purpose - only the latest emailed link works. Returns the
    raw token, which is never stored."""
    from workeasy360.platform.identity.models import AccountToken

    now = timezone.now()
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    with transaction.atomic():
        AccountToken.objects.filter(
            user_id=user_id, purpose=purpose, used_at__isnull=True
        ).update(used_at=now)
        AccountToken.objects.create(
            user_id=user_id,
            purpose=purpose,
            token_hash=_hash(token),
            organization_name=organization_name[:150],
            created_at=now,
            expires_at=now + ttl,
        )
    return token


def open_token(token: Any, purpose: str) -> Any:
    """The unused, unexpired token row for ``token``, **locked**, or
    ``AccountLinkInvalid``. Must run inside the caller's transaction: the
    caller decides whether the link is spent (``consume``) - a verification
    whose password was wrong leaves it usable for a second try."""
    from workeasy360.platform.identity.models import AccountToken

    raw = token if isinstance(token, str) else ""
    if not raw or len(raw) > MAX_TOKEN_LENGTH:
        raise AccountLinkInvalid("This link is not valid.")
    row = (
        AccountToken.objects.select_for_update()
        .select_related("user")
        .filter(token_hash=_hash(raw), purpose=purpose)
        .first()
    )
    if row is None or row.used_at is not None or row.expires_at <= timezone.now():
        raise AccountLinkInvalid("This link is not valid.")
    return row


def consume(row: Any) -> None:
    row.used_at = timezone.now()
    row.save(update_fields=["used_at"])


def spend_all(user_id: Any) -> None:
    """Every outstanding link of the account, of any purpose, is spent - after
    the mailbox has just been proved, a stale link is only a liability."""
    from workeasy360.platform.identity.models import AccountToken

    AccountToken.objects.filter(user_id=user_id, used_at__isnull=True).update(
        used_at=timezone.now()
    )


def requested_organization(user_id: Any) -> str:
    """The company name the account's latest verification link carries, so a
    resend keeps what the signup asked for."""
    from workeasy360.platform.identity.models import AccountToken

    return (
        AccountToken.objects.filter(
            user_id=user_id, purpose=AccountToken.Purpose.VERIFY_EMAIL
        )
        .order_by("-created_at")
        .values_list("organization_name", flat=True)
        .first()
    ) or ""


def prune(*, older_than_days: int = 30) -> int:
    """Delete tokens that expired long ago. The rows hold only a hash, but a
    table that only grows is still a table that only grows."""
    from workeasy360.platform.identity.models import AccountToken

    cutoff = timezone.now() - timedelta(days=max(1, int(older_than_days)))
    deleted, _ = AccountToken.objects.filter(expires_at__lt=cutoff).delete()
    return deleted
