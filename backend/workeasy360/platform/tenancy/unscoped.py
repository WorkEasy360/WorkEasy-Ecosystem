"""The audited escape hatch from tenant scoping.

Bypassing tenant scoping is sometimes legitimate - migrations, reconciliation,
the RLS tests themselves. It is never legitimate *silently*. Every use must
state a reason, and every use is recorded and counted so that anomalous
volumes alert (03-multi-tenancy.md section 4, Layer 5).

Kept in its own module so that ``models.py`` can import it without a cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_unscoped: ContextVar[UnscopedAccess | None] = ContextVar("w360_unscoped", default=None)


@dataclass(frozen=True, slots=True)
class UnscopedAccess:
    """A live unscoped-access grant."""

    reason: str
    module: str


def unscoped_access_active() -> bool:
    return _unscoped.get() is not None


def current_unscoped_access() -> UnscopedAccess | None:
    return _unscoped.get()


@contextmanager
def unscoped_access(*, reason: str, module: str = "") -> Iterator[UnscopedAccess]:
    """Permit unscoped queries and cross-tenant writes inside this block.

    The reason is mandatory and is recorded. A short or absent reason is
    refused rather than accepted with a shrug: "fix data" tells a future
    reader nothing, and this record is what an auditor reads.
    """
    if not reason or len(reason.strip()) < 10:
        raise ValueError(
            "unscoped_access(reason=...) requires a meaningful reason of at "
            "least 10 characters. It is recorded and reviewed."
        )

    grant = UnscopedAccess(reason=reason.strip(), module=module)
    # Counted by metric unscoped_query_total{module} (11-observability.md s6).
    # NOTE: the field is "scope_module", not "module": stdlib logging refuses
    # to let `extra` overwrite reserved LogRecord attributes, and `module` is
    # one of them. Using it raises KeyError at the log call - which would turn
    # an audit record into a crash.
    logger.info(
        "tenancy.unscoped_access.opened",
        extra={
            "event": "tenancy.unscoped_access.opened",
            "reason": grant.reason,
            "scope_module": grant.module,
        },
    )
    token = _unscoped.set(grant)
    try:
        yield grant
    finally:
        _unscoped.reset(token)
        logger.info(
            "tenancy.unscoped_access.closed",
            extra={
                "event": "tenancy.unscoped_access.closed",
                "scope_module": grant.module,
            },
        )
