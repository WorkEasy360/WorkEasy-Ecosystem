"""Claiming and completing idempotent commands.

The shape every caller uses:

    with transaction.atomic():
        claim = idempotency.claim(scope="...", key=..., arguments={...})
        if claim.is_replay:
            return claim.result_ref
        ... do the work ...
        idempotency.complete(claim, result_ref=...)

``claim`` must be called *inside* the caller's transaction. Outside one, a
crash between claiming and doing the work would leave the key burned and the
work never done - which is the exact failure the ledger exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection, transaction

from workeasy360.contracts.errors import IdempotencyConflict
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Counted so a rising replay rate is visible: it usually means a client is
#: retrying because something upstream is timing out.
IDEMPOTENT_REPLAYS = "idempotent_command_replays_total"


@dataclass(frozen=True, slots=True)
class Claim:
    """The outcome of claiming a command key."""

    record_id: UUID
    scope: str
    key: str
    is_replay: bool
    result_ref: str

    @property
    def is_new(self) -> bool:
        return not self.is_replay


def fingerprint(arguments: dict[str, Any]) -> str:
    """Stable hash of a command's arguments.

    ``sort_keys`` and ``default=str`` so that UUIDs, Decimals and datetimes
    hash consistently regardless of how the caller built the dict. Not a
    security control - it detects client bugs and key reuse, not a
    determined attacker, who would simply send a fresh key.
    """
    encoded = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def claim(
    *,
    organization_id: UUID,
    scope: str,
    key: str,
    arguments: dict[str, Any] | None = None,
) -> Claim:
    """Reserve a command key, or report that it was already used.

    Deduplication is done by the unique constraint, not by a read-then-write:
    two concurrent deliveries of the same command genuinely race, and only
    the database can resolve that correctly.
    """
    from workeasy360.integration.idempotency.models import IdempotencyRecord

    if not connection.in_atomic_block:
        raise RuntimeError(
            f"idempotency.claim({scope}) called outside a transaction. The "
            f"claim must roll back with the work it guards, or a failed "
            f"command permanently burns its key."
        )

    digest = fingerprint(arguments or {})

    try:
        # The savepoint is load-bearing. PostgreSQL aborts the whole
        # transaction on a constraint violation, so catching IntegrityError
        # without one leaves the connection unusable and the caller's work
        # unrollbackable - turning a routine duplicate into a failure.
        with transaction.atomic():
            record = IdempotencyRecord(
                organization_id=organization_id,
                scope=scope,
                key=key,
                fingerprint=digest,
            )
            record.save()
        return Claim(
            record_id=record.id,
            scope=scope,
            key=key,
            is_replay=False,
            result_ref="",
        )
    except IntegrityError:
        pass

    existing = IdempotencyRecord.objects.filter(
        organization_id=organization_id, scope=scope, key=key
    ).first()
    if existing is None:  # pragma: no cover - lost row between insert and read
        raise IdempotencyConflict(
            f"Idempotency key {key!r} for {scope} could not be claimed or read."
        )

    if existing.fingerprint != digest:
        logger.warning(
            "idempotency.fingerprint_mismatch",
            scope=scope,
            organization_id=str(organization_id),
        )
        raise IdempotencyConflict(
            f"Idempotency key {key!r} for {scope} was already used with "
            f"different arguments. Reusing a key for a different command is "
            f"refused; issue a new key.",
            scope=scope,
        )

    metrics.increment(IDEMPOTENT_REPLAYS, scope=scope)
    logger.info("idempotency.replay", scope=scope)
    return Claim(
        record_id=existing.id,
        scope=scope,
        key=key,
        is_replay=True,
        result_ref=existing.result_ref,
    )


def complete(claim_: Claim, *, result_ref: str) -> None:
    """Record what the command produced, in the same transaction.

    A replay then returns this reference instead of re-running the work.
    """
    from workeasy360.integration.idempotency.models import IdempotencyRecord

    IdempotencyRecord.objects.filter(id=claim_.record_id).update(result_ref=result_ref)
