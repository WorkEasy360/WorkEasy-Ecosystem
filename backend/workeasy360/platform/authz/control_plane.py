"""Establishing a control-plane principal's context (ADR-027).

Staff and partner users act through a ``TenantContext`` like everyone else, so
that every existing service - which authorizes, audits, publishes and claims
idempotency through it - accepts them unchanged. What is different is how the
context is *built*, and that is confined to this module:

- ``platform_context``: an actor speaking for the control plane itself, bound
  to the **platform organization**. Used for writes that belong to no
  customer (creating a partner, approving a payout).
- ``context_for_organization``: the same actor, re-bound to one customer
  organization whose status is read from the database. Used for acting *on* a
  customer, and never established from a client-supplied organization without
  the caller having first verified the actor's right to that organization.

Neither function decides whether the actor *may* do anything. That remains
``authz.check``, which resolves the permission set from the principal.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from workeasy360.contracts.enums import (
    OrganizationStatus,
    PrincipalKind,
    UserStatus,
)
from workeasy360.contracts.errors import CommercialRecordNotFound
from workeasy360.contracts.values.control_plane import (
    PLATFORM_ORGANIZATION_ID,
    PLATFORM_ORGANIZATION_SLUG,
    PLATFORM_SYSTEM_USER_EMAIL,
)
from workeasy360.contracts.values.tenant import NO_MEMBERSHIP, TenantContext


def ensure_platform_organization() -> None:
    """Make sure the platform organization row exists. Idempotent, race-safe.

    Created lazily rather than only by a migration: the test suite (and any
    ``flush``) truncates every table, and a migration-only row would not come
    back. Lazily creating it costs one indexed existence query per
    control-plane request.
    """
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.selectors import organization_status
    from workeasy360.platform.organizations.services import ensure_organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    if organization_status(PLATFORM_ORGANIZATION_ID) is not None:
        return

    with unscoped_access(
        reason="Bootstrap of the control-plane organization anchor",
        module="platform.authz.control_plane",
    ):
        try:
            with transaction.atomic():
                owner = User.objects.filter(email=PLATFORM_SYSTEM_USER_EMAIL).first()
                if owner is None:
                    owner = lifecycle.create_account(
                        email=PLATFORM_SYSTEM_USER_EMAIL,
                        password=None,
                        status=UserStatus.DISABLED,
                        full_name="Workeasy360 control plane",
                    )
                ensure_organization(
                    organization_id=PLATFORM_ORGANIZATION_ID,
                    slug=PLATFORM_ORGANIZATION_SLUG,
                    name="Workeasy360 control plane",
                    created_by_id=owner.id,
                )
        except IntegrityError:
            # Lost the race for the system account: the anchor now exists.
            if organization_status(PLATFORM_ORGANIZATION_ID) is None:
                raise


def platform_context(
    *,
    user_id: UUID,
    principal: PrincipalKind = PrincipalKind.STAFF,
    partner_id: UUID | None = None,
) -> TenantContext:
    """The context of a control-plane actor speaking for no customer."""
    if principal is PrincipalKind.MEMBER:
        raise ValueError("A control-plane context is STAFF or PARTNER, never MEMBER.")
    return TenantContext(
        organization_id=PLATFORM_ORGANIZATION_ID,
        membership_id=NO_MEMBERSHIP,
        user_id=user_id,
        organization_status=OrganizationStatus.ACTIVE,
        principal=principal,
        partner_id=partner_id,
    )


def context_for_organization(
    actor: TenantContext, organization_id: UUID
) -> TenantContext:
    """Re-bind a control-plane actor to one customer organization.

    A missing organization and the platform organization itself are both a
    404: a caller must not learn which identifiers exist, and the platform
    organization is not a customer.
    """
    from workeasy360.platform.organizations.selectors import organization_status

    if not actor.is_control_plane:
        raise ValueError(
            "Only a control-plane actor is re-bound to another organization."
        )
    status = (
        None
        if organization_id == PLATFORM_ORGANIZATION_ID
        else organization_status(organization_id)
    )
    if status is None:
        raise CommercialRecordNotFound(
            "No such organization.", organization_id=str(organization_id)
        )
    return dataclasses.replace(
        actor, organization_id=organization_id, organization_status=status
    )


@contextmanager
def acting_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    """Both isolation layers for ``ctx``, exactly as the tenancy middleware
    binds them for a request: the ORM context (Layer 2) and the database
    transaction variable (Layer 3). Entering only one measures the wrong
    thing, which is why this is one call."""
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    if ctx.organization_id == PLATFORM_ORGANIZATION_ID:
        # Created lazily and only when a control-plane *write* scope is
        # actually entered, so a read-only request never inserts a row.
        ensure_platform_organization()
    with tenant_context(ctx), tenant_db_scope(ctx):
        yield ctx


@contextmanager
def ensure_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    """Be inside ``ctx``'s scope, entering it only if the caller has not.

    A control-plane service must not depend on its caller remembering to open a
    scope: a service that writes the audit log, claims an idempotency key or
    publishes an event needs both isolation layers bound to its actor's
    organization, and a caller that forgot would otherwise get zero rows or a
    refused write from deep inside. Idempotent: when the exact context is
    already bound (a request that entered it, or a test), this is a no-op and
    adds no savepoint.
    """
    from workeasy360.platform.tenancy.context import current_tenant_or_none
    from workeasy360.platform.tenancy.db import bound_organization

    if current_tenant_or_none() == ctx and bound_organization() == ctx.organization_id:
        yield ctx
        return
    with acting_scope(ctx):
        yield ctx


def in_scope(function: Any) -> Any:
    """Decorator: run a service (whose first argument is its actor's context)
    inside :func:`ensure_scope`."""
    import functools

    @functools.wraps(function)
    def wrapper(ctx: TenantContext, *args: Any, **kwargs: Any) -> Any:
        with ensure_scope(ctx):
            return function(ctx, *args, **kwargs)

    return wrapper


def organization_scope(actor: TenantContext, organization_id: UUID) -> Any:
    """``acting_scope`` for the actor re-bound to a customer organization."""
    return acting_scope(context_for_organization(actor, organization_id))
