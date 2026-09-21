"""Tenant context propagation.

A ``ContextVar`` rather than thread-local storage because the application runs
under ASGI, where one thread interleaves many requests. Thread-locals are a
correctness bug under async, not a style preference
(03-multi-tenancy.md section 4, Layer 1).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

from workeasy360.contracts.enums import OrganizationStatus
from workeasy360.contracts.errors import MissingTenantContext
from workeasy360.contracts.values.tenant import TenantContext

_tenant: ContextVar[TenantContext | None] = ContextVar("w360_tenant", default=None)


def current_tenant_or_none() -> TenantContext | None:
    """The active context, or None. For code that legitimately runs unscoped."""
    return _tenant.get()


def current_tenant() -> TenantContext:
    """The active context, raising if absent.

    Raising rather than returning None is the whole point: silent absence is
    the dangerous failure, because it looks like "no data" and ships.
    """
    ctx = _tenant.get()
    if ctx is None:
        raise MissingTenantContext(
            "No tenant context is active. Tenant-scoped work must run inside "
            "set_tenant()/tenant_context(), which the tenancy middleware "
            "establishes for requests and TenantTask establishes for tasks."
        )
    return ctx


def set_tenant(ctx: TenantContext | None) -> Token:
    """Bind a context. Returns a token for :func:`reset_tenant`.

    Prefer the :func:`tenant_context` context manager; this exists for
    framework integration points such as Celery's ``before_start``, where
    there is no enclosing block to attach to.
    """
    return _tenant.set(ctx)


def reset_tenant(token: Token) -> None:
    _tenant.reset(token)


@contextmanager
def tenant_context(ctx: TenantContext | None) -> Iterator[TenantContext | None]:
    """Bind a context for the duration of the block, then restore.

    Always restores the previous value rather than clearing, so nesting works
    and an inner block cannot silently widen an outer one.
    """
    token = _tenant.set(ctx)
    try:
        yield ctx
    finally:
        _tenant.reset(token)


@contextmanager
def no_tenant() -> Iterator[None]:
    """Explicitly run with no tenant context.

    Used by tests and by genuinely cross-tenant jobs to prove that a code path
    fails closed rather than inheriting whatever context happened to be set.
    """
    with tenant_context(None):
        yield


def build_context(
    *,
    organization_id: UUID,
    membership_id: UUID,
    user_id: UUID,
    organization_status: OrganizationStatus = OrganizationStatus.ACTIVE,
    aal: int = 1,
    is_break_glass: bool = False,
    reason: str | None = None,
) -> TenantContext:
    """Construct a context. Keyword-only: three same-typed UUIDs adjacent in a
    positional signature is a swap waiting to happen, and swapping
    ``user_id`` with ``organization_id`` would be a cross-tenant defect."""
    return TenantContext(
        organization_id=organization_id,
        membership_id=membership_id,
        user_id=user_id,
        organization_status=organization_status,
        aal=aal,
        is_break_glass=is_break_glass,
        reason=reason,
    )
