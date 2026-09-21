"""TenantTask - tenant context and correlation across the queue boundary.

R-07: contextvars do not propagate into Celery workers. A task that queries
tenant data without re-establishing context either runs unscoped or against
whatever context happened to leak. Background jobs get less review attention
than request paths, so this is exactly where scoping bugs hide.

The rule: a task that needs tenant data and has none **refuses to start and
dead-letters**. It does not guess, and it does not run unscoped.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from celery import Task

from workeasy360.contracts.errors import MissingTenantContext
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.observability.correlation import (
    current_correlation_id,
    set_correlation_id,
)
from workeasy360.platform.tenancy.context import (
    current_tenant_or_none,
    reset_tenant,
    set_tenant,
)

logger = logging.getLogger(__name__)

TENANT_HEADER = "w360_tenant"
CORRELATION_HEADER = "w360_correlation_id"


class TenantTask(Task):
    """Base class for every task in the system.

    Subclasses declare ``requires_tenant = False`` only when they are
    genuinely cross-tenant (nightly reconciliation, catalog sync). Those are
    CODEOWNERS-reviewed and must iterate organizations entering context per
    organization - never run one unscoped query across all tenants.
    """

    #: Default True. Opting out is a deliberate, reviewed act.
    requires_tenant: bool = True

    #: Bounded retries. Never unlimited: an event that cannot be processed
    #: will not become processable by being retried forever, and infinite
    #: retries turn one bad message into a permanent queue clog.
    autoretry_for: tuple[type[BaseException], ...] = ()
    max_retries: int = 5
    retry_backoff: bool = True
    retry_jitter: bool = True

    _tenant_token: Any = None
    _correlation_token: Any = None

    # --- publish side -----------------------------------------------------

    def apply_async(self, args=None, kwargs=None, **options: Any):  # type: ignore[override]
        """Attach tenant context and correlation id to the message."""
        headers = dict(options.get("headers") or {})

        ctx = current_tenant_or_none()
        if ctx is not None:
            headers[TENANT_HEADER] = ctx.to_headers()
        elif self.requires_tenant:
            # Fail at publish time, where the stack trace points at the code
            # that forgot the context - rather than in a worker minutes later
            # with no useful frame.
            raise MissingTenantContext(
                f"{self.name} requires tenant context but was dispatched "
                f"without one. Dispatch from inside a tenant context, or set "
                f"requires_tenant = False if this task is genuinely "
                f"cross-tenant (CODEOWNERS-reviewed)."
            )

        headers[CORRELATION_HEADER] = str(current_correlation_id() or "")
        options["headers"] = headers
        return super().apply_async(args=args, kwargs=kwargs, **options)

    # --- consume side -----------------------------------------------------

    def before_start(self, task_id: str, args: Any, kwargs: Any) -> None:
        raw_headers = getattr(self.request, "headers", None) or {}

        correlation = raw_headers.get(CORRELATION_HEADER) or None
        self._correlation_token = set_correlation_id(correlation)

        raw_tenant = raw_headers.get(TENANT_HEADER)
        if raw_tenant is None:
            if self.requires_tenant:
                logger.error(
                    "task.tenant_context.missing",
                    extra={
                        "event": "task.tenant_context.missing",
                        "task": self.name,
                        "task_id": task_id,
                    },
                )
                raise MissingTenantContext(
                    f"{self.name} dispatched without tenant context; refusing to start."
                )
            self._tenant_token = set_tenant(None)
        else:
            ctx = TenantContext.from_headers(raw_tenant)
            self._tenant_token = set_tenant(ctx)

        logger.info(
            "task.started",
            extra={
                "event": "task.started",
                "task": self.name,
                "task_id": task_id,
                "queue": getattr(self, "queue", None),
            },
        )
        super().before_start(task_id, args, kwargs)

    def after_return(
        self, status: str, retval: Any, task_id: str, args: Any, kwargs: Any, einfo: Any
    ) -> None:
        # Reset in after_return rather than in the task body so context is
        # cleared even when the task raises. A worker process is reused for
        # thousands of tasks; a leaked context is a cross-tenant defect.
        if self._tenant_token is not None:
            reset_tenant(self._tenant_token)
            self._tenant_token = None
        if self._correlation_token is not None:
            set_correlation_id(None)
            self._correlation_token = None

        logger.info(
            "task.finished",
            extra={
                "event": "task.finished",
                "task": self.name,
                "task_id": task_id,
                "status": status,
            },
        )
        super().after_return(status, retval, task_id, args, kwargs, einfo)

    def on_failure(
        self, exc: BaseException, task_id: str, args: Any, kwargs: Any, einfo: Any
    ) -> None:
        logger.error(
            "task.failed",
            extra={
                "event": "task.failed",
                "task": self.name,
                "task_id": task_id,
                "error_type": type(exc).__name__,
                "retries": self.request.retries,
            },
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)

    # --- retry policy -----------------------------------------------------

    def backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with **full jitter**.

        ``sleep = random(0, min(cap, base * 2^n))``. Without jitter, every
        client that failed together retries together, producing a
        synchronised herd that re-kills the recovering dependency
        (09-failure-resilience.md section 4.3).
        """
        from django.conf import settings

        base = float(getattr(settings, "TASK_RETRY_BACKOFF_BASE_S", 2.0))
        cap = float(getattr(settings, "TASK_RETRY_BACKOFF_CAP_S", 3600.0))
        ceiling = min(cap, base * (2**attempt))
        return random.uniform(0, ceiling)  # noqa: S311 - retry jitter, not cryptography


class CrossTenantTask(TenantTask):
    """A task that legitimately spans tenants.

    Subclassing this instead of setting the flag inline makes the opt-out
    greppable and reviewable: ``grep -r CrossTenantTask`` is the full list of
    jobs that may see more than one tenant.

    Implementations must still iterate organizations and enter tenant context
    per organization. Running a single unscoped query across every tenant is
    forbidden even here.
    """

    requires_tenant = False
