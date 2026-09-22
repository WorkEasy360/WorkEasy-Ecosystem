"""Tenant-scoped ORM base: isolation Layer 2, and the R-10 persistence block.

Two independent jobs, both structural:

**Isolation.** ``objects`` refuses to build a queryset without tenant context.
It *raises* rather than returning an empty queryset, because silent emptiness
looks like "no data", passes review and ships (ADR-009).

**Suspension (R-10).** Chokepoint 2 of the two required write blocks. Writes
are refused here, at the persistence boundary, so that every path - view,
service, task, event handler, admin action, shell - is covered by the same
rule. Per-view checks guarantee a path will be missed, and a missed path means
a non-paying customer keeps writing.

Chokepoint 1 is the tenancy middleware. Neither is sufficient alone:
middleware misses tasks and shells; this misses nothing but returns an error
later in the cycle than an HTTP boundary should.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from django.db import models

from workeasy360.contracts.errors import (
    CrossTenantAccess,
    MissingTenantContext,
    OrganizationNotWritable,
    TenantContextMismatch,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.observability.metrics import (
    RLS_VIOLATION,
    TENANT_CONTEXT_MISSING,
    WRITE_BLOCKED_SUSPENDED,
    metrics,
)
from workeasy360.platform.tenancy.context import current_tenant_or_none
from workeasy360.platform.tenancy.unscoped import unscoped_access_active

logger = logging.getLogger(__name__)


def _require_context(model_name: str) -> TenantContext:
    ctx = current_tenant_or_none()
    if ctx is None:
        # Correctness alarm: metric tenant_context_missing_total must stay at
        # zero, and any non-zero value pages (11-observability.md section 6).
        metrics.increment(TENANT_CONTEXT_MISSING, model=model_name)
        logger.error(
            "tenant.context.missing",
            extra={"event": "tenant.context.missing", "model": model_name},
        )
        raise MissingTenantContext(
            f"Query or write on {model_name} without tenant context. "
            f"Tenant-scoped work must run inside the tenancy middleware, a "
            f"TenantTask, or an explicit tenant_context() block. If this code "
            f"is legitimately cross-tenant, use the audited `unscoped` "
            f"manager inside unscoped_access(reason=...)."
        )
    return ctx


def _require_writable(
    ctx: TenantContext, model_name: str, operation: str, model: type | None = None
) -> None:
    """R-10 chokepoint 2.

    Raises 402-shaped ``OrganizationNotWritable`` rather than a permission
    error: the remedy is commercial (pay, reactivate), not a role change, and
    the distinction drives a different message and a different support queue.
    """
    if ctx.is_write_allowed:
        return
    if model is not None and getattr(model, "tenancy_write_block_exempt", False):
        # Documented exemption - see TenantScopedModel.tenancy_write_block_exempt.
        return
    metrics.increment(
        WRITE_BLOCKED_SUSPENDED,
        model=model_name,
        organization_status=ctx.organization_status.value,
    )
    logger.warning(
        "tenant.write_blocked",
        extra={
            "event": "tenant.write_blocked",
            "model": model_name,
            "operation": operation,
            "organization_id": str(ctx.organization_id),
            "organization_status": ctx.organization_status.value,
        },
    )
    raise OrganizationNotWritable(
        f"Cannot {operation} {model_name}: organization is "
        f"{ctx.organization_status.value}, which is read-only.",
        organization_id=ctx.organization_id,
        status=ctx.organization_status.value,
    )


class TenantScopedQuerySet(models.QuerySet):
    """A queryset that is always tenant-filtered and suspension-aware.

    The bulk paths are overridden because ``update()``, ``delete()`` and
    ``bulk_create()`` bypass ``Model.save()`` entirely. Leaving them
    unguarded would leave the R-10 block trivially bypassable by anyone
    reaching for a bulk operation, which is exactly what someone does when
    fixing data under time pressure.
    """

    def update(self, **kwargs: Any) -> int:
        ctx = current_tenant_or_none()
        if ctx is not None:
            _require_writable(ctx, self.model.__name__, "bulk update", self.model)
        return super().update(**kwargs)

    def delete(self) -> tuple[int, dict[str, int]]:
        ctx = current_tenant_or_none()
        if ctx is not None:
            _require_writable(ctx, self.model.__name__, "bulk delete", self.model)
        return super().delete()

    def bulk_create(
        self, objs: Iterable[models.Model], *args: Any, **kwargs: Any
    ) -> list:
        objs = list(objs)
        ctx = current_tenant_or_none()
        if ctx is not None:
            _require_writable(ctx, self.model.__name__, "bulk create", self.model)
            for obj in objs:
                # bulk_create skips save(), so the organization stamping and
                # mismatch check that save() performs must happen here too.
                _stamp_organization(obj, ctx)
        return super().bulk_create(objs, *args, **kwargs)

    def bulk_update(
        self,
        objs: Iterable[models.Model],
        fields: Sequence[str],
        *args: Any,
        **kwargs: Any,
    ) -> int:
        objs = list(objs)
        ctx = current_tenant_or_none()
        if ctx is not None:
            _require_writable(ctx, self.model.__name__, "bulk update", self.model)
            for obj in objs:
                _assert_same_tenant(obj, ctx)
        return super().bulk_update(objs, fields, *args, **kwargs)


class TenantScopedManager(models.Manager.from_queryset(TenantScopedQuerySet)):
    """The default manager. Filters by the active organization, or raises.

    Both isolation layers are checked, not one. Layer 2 (this filter) without
    Layer 3 (the RLS variable) is the shape of the worst defect Phase 2 found:
    the query is perfectly well formed, the policy matches nothing, and the
    caller reads zero rows as an answer. Checking only the layer this class
    owns would leave that failure exactly as quiet as it was.
    """

    # Keeps migrations and dumpdata from using this manager, which would make
    # them fail for want of a tenant context they cannot have.
    use_in_migrations = False

    def get_queryset(self) -> TenantScopedQuerySet:
        from workeasy360.platform.tenancy.db import assert_tenant_scope

        ctx = _require_context(self.model.__name__)
        assert_tenant_scope(ctx.organization_id, what=self.model.__name__)
        return super().get_queryset().filter(organization_id=ctx.organization_id)


class UnscopedManager(models.Manager.from_queryset(TenantScopedQuerySet)):
    """Deliberately unfiltered access.

    Exists because migrations, reconciliation jobs and the RLS tests genuinely
    need it. Its use is restricted three ways (03-multi-tenancy.md s4 Layer 2):

    - an AST check in tests/architecture allowlists the modules that may use it
    - every call site must sit inside ``unscoped_access(reason=...)``
    - that context manager writes an audit record and increments a metric

    Note this is *application* scoping only. PostgreSQL RLS still applies, so
    `unscoped` inside a tenant transaction still returns one tenant's rows -
    which is the defence-in-depth property working as intended.
    """

    use_in_migrations = True

    def get_queryset(self) -> TenantScopedQuerySet:
        if not unscoped_access_active():
            raise MissingTenantContext(
                f"{self.model.__name__}.unscoped used outside "
                f"unscoped_access(reason=...). Bypassing tenant scoping is "
                f"permitted, but never silently: the reason is audited and "
                f"the call site is allowlisted."
            )
        return super().get_queryset()


def _stamp_organization(instance: models.Model, ctx: TenantContext) -> None:
    """Set organization_id from context, or verify an explicitly-set one.

    A mismatch raises rather than being silently overwritten. Overwriting
    would turn an attempt to write into another tenant into a successful write
    into this one - a data-corruption bug that looks like it worked.
    """
    current = getattr(instance, "organization_id", None)
    if current is None:
        instance.organization_id = ctx.organization_id
    elif current != ctx.organization_id:
        # A correctness alarm, not a validation error: something tried to
        # write one tenant's row while acting as another. Any non-zero value
        # pages (11-observability.md section 6).
        metrics.increment(RLS_VIOLATION, model=type(instance).__name__, phase="write")
        raise TenantContextMismatch(
            f"{type(instance).__name__}.organization_id={current} does not "
            f"match the active tenant context {ctx.organization_id}. One of "
            f"the two is a bug; guessing which is how cross-tenant writes "
            f"happen.",
            expected=str(ctx.organization_id),
            actual=str(current),
        )


def _assert_same_tenant(instance: models.Model, ctx: TenantContext) -> None:
    current = getattr(instance, "organization_id", None)
    if current is not None and current != ctx.organization_id:
        metrics.increment(RLS_VIOLATION, model=type(instance).__name__, phase="access")
        raise CrossTenantAccess(
            f"{type(instance).__name__} belongs to organization {current}, "
            f"not the active tenant {ctx.organization_id}.",
        )


class TenantScopedModel(models.Model):
    """Base for every model that holds tenant-owned data.

    Subclassing this is what makes a model tenant-scoped; system check
    W360.E001 fails the build for any model with an ``organization`` field
    that does not.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        db_index=True,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    #: The single permitted exemption from the R-10 suspension write block.
    #:
    #: Set True only on append-only, non-business-state models. The one case
    #: in Phase 1 is ``audit.AuditEvent``: audit records what happened,
    #: *including denied attempts*. If suspension blocked audit writes, the
    #: moment a customer most needs a record - the suspension itself, and
    #: every rejected action afterwards - would be the moment nothing was
    #: recorded, and every denial would surface as AuditWriteFailed instead of
    #: the correct 402.
    #:
    #: Safe because an append-only, customer-visible log cannot be used to
    #: mutate business state, which is what suspension exists to stop.
    #: Any new use requires an ADR; a fitness test pins the current list.
    tenancy_write_block_exempt = False

    objects = TenantScopedManager()
    unscoped = UnscopedManager()

    class Meta:
        abstract = True
        # `objects` is the *default* manager, so application code and reverse
        # relations (organization.memberships.all()) are tenant-scoped.
        #
        # `base_manager_name` is deliberately NOT set. Django uses
        # `_base_manager` for its own internals - INSERT, refresh_from_db and
        # forward FK descriptor fetches - and pointing it at the scoped
        # manager makes every one of those raise whenever there is no tenant
        # context, including during legitimate unscoped_access() writes.
        # Leaving it unset lets Django auto-create a plain manager for its own
        # use, which is safe because PostgreSQL RLS still filters those
        # queries: application scoping is Layer 2, not the only layer.
        default_manager_name = "objects"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persistence chokepoint: stamps tenancy and enforces R-10.

        Runs for every ORM write that goes through a model instance, which is
        every write except the bulk paths - and those are guarded on the
        queryset above.
        """
        ctx = current_tenant_or_none()

        if ctx is None:
            # A save with no context is only legitimate when the caller has
            # explicitly opted out and set organization_id themselves; that is
            # how provisioning and reconciliation write across tenants.
            if not unscoped_access_active():
                raise MissingTenantContext(
                    f"Saving {type(self).__name__} without tenant context. "
                    f"Set a context, or use unscoped_access(reason=...) if "
                    f"this write is genuinely cross-tenant."
                )
            if getattr(self, "organization_id", None) is None:
                raise MissingTenantContext(
                    f"Unscoped save of {type(self).__name__} must set "
                    f"organization_id explicitly."
                )
        else:
            _stamp_organization(self, ctx)
            _require_writable(ctx, type(self).__name__, "save", type(self))

        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        ctx = current_tenant_or_none()
        if ctx is not None:
            _assert_same_tenant(self, ctx)
            _require_writable(ctx, type(self).__name__, "delete", type(self))
        elif not unscoped_access_active():
            raise MissingTenantContext(
                f"Deleting {type(self).__name__} without tenant context."
            )
        return super().delete(*args, **kwargs)
