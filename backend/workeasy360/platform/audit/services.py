"""Recording audit entries.

One function, ``record()``, called from application services. It is
deliberately not a signal handler: a signal fires invisibly, in an unspecified
order, and does not run for bulk operations (F-8). An explicit call at the
point of the change is greppable and testable.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Final

from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.enums import ActorKind, AuditOutcome
from workeasy360.contracts.errors import AuditWriteFailed
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.observability.correlation import current_correlation_id
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: Keys whose values are never stored in ``changes``. The audit log is
#: customer-readable and retained for seven years, so a secret written here is
#: a secret disclosed for seven years.
_REDACT_KEYS: Final = re.compile(
    r"(password|secret|token|api_?key|authorization|credential|private_?key"
    r"|session|csrf|signature|otp|mfa|card_?number|cvv|iban|account_?number)",
    re.IGNORECASE,
)

REDACTED: Final = "[redacted]"


def record(
    ctx: TenantContext,
    action: str,
    *,
    resource_type: str,
    resource_id: str | Any = "",
    changes: dict[str, Any] | None = None,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    reason: str = "",
    actor_kind: ActorKind | None = None,
    ip: str | None = None,
) -> Any:
    """Append one audit entry, in the caller's transaction.

    Raises ``AuditWriteFailed`` on failure, which fails the enclosing
    operation. That is intentional and is the single exception to "telemetry
    never breaks the request" (12-security.md section 18).
    """
    from workeasy360.platform.audit.models import AuditEvent

    if actor_kind is None:
        actor_kind = ActorKind.STAFF_BREAK_GLASS if ctx.is_break_glass else ActorKind.USER

    if ctx.is_break_glass and not (reason or ctx.reason):
        raise AuditWriteFailed(
            "A break-glass action must carry a reason before it can be audited."
        )

    try:
        with transaction.atomic():
            prev_hash = _latest_hash(ctx)
            event = AuditEvent(
                organization_id=ctx.organization_id,
                occurred_at=timezone.now(),
                actor_kind=actor_kind.value,
                actor_user_id=ctx.user_id,
                actor_membership_id=ctx.membership_id,
                on_behalf_of=None,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id or ""),
                changes=redact(changes) if changes else None,
                outcome=outcome.value,
                reason=reason or (ctx.reason or ""),
                ip_hash=hash_ip(ip) if ip else "",
                correlation_id=current_correlation_id(),
                prev_hash=prev_hash,
            )
            event.save()
            return event
    except Exception as exc:
        logger.critical(
            "audit.write.failed",
            action=action,
            error_type=type(exc).__name__,
        )
        raise AuditWriteFailed(
            f"Could not record audit entry for {action}: {type(exc).__name__}. "
            f"The operation is failed rather than completed unaudited."
        ) from exc


def record_system(
    organization_id: Any,
    action: str,
    *,
    resource_type: str,
    resource_id: str | Any = "",
    changes: dict[str, Any] | None = None,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    reason: str = "",
) -> Any:
    """Append an audit entry for an action **no user performed**.

    Time-driven commercial work - a trial reaching its end, a scheduled
    cancellation taking effect, a billing period rolling - changes a
    customer's access with no request, no session and no membership behind
    it. Those changes are exactly the ones a customer will later ask about,
    so they must be audited; but :func:`record` needs a ``TenantContext``,
    and a synthetic one carrying a fabricated user id would put a person's
    name against something they did not do.

    ``ActorKind.SYSTEM`` with null actor ids is the honest representation.

    The unscoped manager is required because these operations run outside any
    request tenant context: the caller has entered the organization's
    *database* scope (Layer 3) but has no ORM context (Layer 2). RLS still
    applies, so an entry can only be written for the organization whose scope
    is open.
    """
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    if not reason:
        raise AuditWriteFailed(
            "A system action must carry a reason. An unexplained automated "
            "change to a customer's access is the thing this record exists "
            "to make explainable."
        )

    try:
        with (
            unscoped_access(
                reason="System commercial operations audit without a request context",
                module="platform.audit.services",
            ),
            transaction.atomic(),
        ):
            prev_hash = (
                AuditEvent.unscoped.filter(organization_id=organization_id)
                .order_by("-occurred_at", "-id")
                .values_list("entry_hash", flat=True)
                .first()
                or ""
            )
            event = AuditEvent(
                organization_id=organization_id,
                occurred_at=timezone.now(),
                actor_kind=ActorKind.SYSTEM.value,
                actor_user_id=None,
                actor_membership_id=None,
                on_behalf_of=None,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id or ""),
                changes=redact(changes) if changes else None,
                outcome=outcome.value,
                reason=reason,
                ip_hash="",
                correlation_id=current_correlation_id(),
                prev_hash=prev_hash,
            )
            event.save()
            return event
    except Exception as exc:
        logger.critical(
            "audit.write.failed",
            action=action,
            error_type=type(exc).__name__,
        )
        raise AuditWriteFailed(
            f"Could not record system audit entry for {action}: "
            f"{type(exc).__name__}. The operation is failed rather than "
            f"completed unaudited."
        ) from exc


def _latest_hash(ctx: TenantContext) -> str:
    """The previous entry's hash for this organization.

    Locked ordering is not attempted: two concurrent writes may read the same
    ``prev_hash`` and produce a fork in the chain. Serialising every audit
    write per organization would make audit a contention point on the hot
    path, which is a worse trade. The verification job reports forks, and a
    fork is detectable tampering-wise because rewriting history still breaks
    the descendants of the rewritten entry.
    """
    from workeasy360.platform.audit.models import AuditEvent

    return (
        AuditEvent.objects.filter(organization_id=ctx.organization_id)
        .order_by("-occurred_at", "-id")
        .values_list("entry_hash", flat=True)
        .first()
        or ""
    )


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip sensitive values from a changes dict, recursively."""

    def scrub(key: str, value: Any, depth: int = 0) -> Any:
        if depth > 6:
            return value
        if _REDACT_KEYS.search(key):
            return REDACTED
        if isinstance(value, dict):
            return {k: scrub(str(k), v, depth + 1) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [scrub(key, v, depth + 1) for v in value]
        return value

    return {k: scrub(str(k), v) for k, v in payload.items()}


def hash_ip(ip: str) -> str:
    """Hash an IP with a server-side pepper.

    A bare SHA-256 of an IPv4 address is trivially reversible - there are only
    four billion of them - so the pepper is what makes this a one-way
    transformation rather than a lookup table.
    """
    from django.conf import settings

    pepper = getattr(settings, "SECRET_KEY", "")
    return hashlib.sha256(f"{pepper}:{ip}".encode()).hexdigest()


def verify_chain(organization_id: Any, limit: int = 1000) -> list[str]:
    """Re-derive each entry's hash and report mismatches.

    Run as a scheduled job. A mismatch means an entry was modified after it
    was written - which the grants and trigger should make impossible, so a
    finding here is a serious incident, not a data-quality note.
    """
    from workeasy360.platform.audit.models import AuditEvent

    problems: list[str] = []
    entries = list(
        AuditEvent.objects.filter(organization_id=organization_id).order_by(
            "occurred_at", "id"
        )[:limit]
    )

    for entry in entries:
        expected = entry.compute_hash()
        if expected != entry.entry_hash:
            problems.append(
                f"AuditEvent {entry.id}: stored hash {entry.entry_hash[:12]}... "
                f"does not match recomputed {expected[:12]}.... The entry has "
                f"been modified since it was written."
            )
    return problems
