"""Tenancy middleware: isolation Layer 1, and R-10 chokepoint 1.

Resolution order (03-multi-tenancy.md section 4, Layer 1):

1. Authenticate the principal.
2. Determine the target organization from the **URL path**. Never from a
   header or body - a client-supplied organization is a cross-tenant read
   primitive, and TB-1 says client headers are never truthful.
3. Look up an ``active`` Membership for (user, organization). No membership
   means 403, with a uniform response that does not disclose existence.
4. Check the organization's status against the request method.
5. Bind TenantContext, open the transaction, set the RLS variable.

Every failure path denies. There is no branch where an unresolved tenant
results in a wider query.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from workeasy360.contracts.enums import OrganizationStatus
from workeasy360.contracts.errors import Workeasy360Error
from workeasy360.platform.tenancy.context import reset_tenant, set_tenant
from workeasy360.platform.tenancy.db import tenant_db_scope
from workeasy360.platform.tenancy.resolution import (
    TenantResolution,
    resolve_tenant_from_path,
)

logger = logging.getLogger(__name__)

#: Methods that may mutate state. The R-10 block applies to exactly these.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Paths that never carry tenant context. Kept deliberately short: an
#: over-broad exemption list is how an endpoint ends up unscoped by accident.
EXEMPT_PREFIXES = ("/healthz/", "/static/", "/admin/login")


class TenancyMiddleware:
    """Establishes tenant context, or denies the request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if self._is_exempt(request.path):
            return self.get_response(request)

        try:
            resolution = resolve_tenant_from_path(request)
        except Workeasy360Error as exc:
            return self._deny(request, exc)

        if resolution is None:
            # Not a tenant-scoped URL. The request proceeds with no context,
            # and any tenant query it attempts will raise rather than run
            # unscoped - which is the fail-closed property, not a gap.
            return self.get_response(request)

        denial = self._check_access(request, resolution)
        if denial is not None:
            return denial

        return self._run_in_tenant_scope(request, resolution)

    # --- steps ------------------------------------------------------------

    @staticmethod
    def _is_exempt(path: str) -> bool:
        return path.startswith(EXEMPT_PREFIXES)

    def _check_access(
        self, request: HttpRequest, resolution: TenantResolution
    ) -> HttpResponse | None:
        """Steps 3 and 4: membership, then organization status."""
        if not resolution.membership_status.grants_access:
            return self._deny_uniform(request, resolution)

        status = resolution.organization_status

        if not status.allows_read:
            # locked / deleted: not even reads. Uniform 404-shaped denial so a
            # locked organization is not distinguishable from a missing one.
            return self._deny_uniform(request, resolution)

        # Phase 8 extension: the organization's own access policy - network
        # allow-list, session age, "signed out of this
        # organization", password policy. Evaluated only once membership and
        # status have admitted the request, so it can deny and never widen.
        denial = self._check_access_policy(request, resolution)
        if denial is not None:
            return denial

        # R-10 chokepoint 1. Blocking at the HTTP boundary means the caller
        # gets a fast, well-shaped 402 instead of a 500 from deep inside a
        # service - but it is not the only block, because middleware never
        # sees a Celery task or a management command.
        if request.method in UNSAFE_METHODS and not status.allows_write:
            logger.warning(
                "tenancy.write_blocked.http",
                extra={
                    "event": "tenancy.write_blocked.http",
                    "organization_id": str(resolution.organization_id),
                    "organization_status": status.value,
                    "http_method": request.method,
                },
            )
            return JsonResponse(
                {
                    "code": "organization_not_writable",
                    "detail": _write_block_message(status),
                    "organization_status": status.value,
                },
                status=402,
            )

        return None

    @staticmethod
    def _check_access_policy(
        request: HttpRequest, resolution: TenantResolution
    ) -> HttpResponse | None:
        from workeasy360.contracts.values import access_policy
        from workeasy360.platform.observability.metrics import metrics
        from workeasy360.platform.tenancy.resolution import session_facts

        facts = session_facts(request, resolution.organization_id)
        reason = access_policy.evaluate(resolution.access_policy, facts, now=time.time())
        if reason is None:
            return None
        logger.info(
            "tenancy.access_policy.denied",
            extra={
                "event": "tenancy.access_policy.denied",
                "organization_id": str(resolution.organization_id),
                "reason": reason,
            },
        )
        metrics.increment("tenancy_access_policy_denials_total", reason=reason)
        return JsonResponse(
            {"code": reason, "detail": access_policy.DENIAL_MESSAGES[reason]},
            status=access_policy.DENIAL_STATUS[reason],
        )

    def _run_in_tenant_scope(
        self, request: HttpRequest, resolution: TenantResolution
    ) -> HttpResponse:
        """Step 5: bind context, open the transaction, set the RLS variable.

        The whole request runs inside this transaction. That is a deliberate
        consequence of using SET LOCAL: the variable only survives while the
        transaction does (ADR-009, Consequences).
        """
        ctx = resolution.to_context()
        request.tenant_context = ctx  # type: ignore[attr-defined]
        token = set_tenant(ctx)
        try:
            with tenant_db_scope(ctx):
                response = self.get_response(request)

                # A 5xx must not commit partial work. Every view exception
                # reaches here already converted to a response - Django's
                # handler and DomainErrorMiddleware.process_exception both
                # run inside this block - so this is the only rollback
                # trigger. A 4xx deliberately still commits: the launcher's
                # DENIED seat audit relies on it. The consequence, for
                # writers: validate before the first write, or keep it in an
                # inner atomic() the domain error propagates out of (D-016).
                if response.status_code >= 500:
                    from django.db import transaction

                    transaction.set_rollback(True)
                return response
        finally:
            reset_tenant(token)

    # --- denials ----------------------------------------------------------

    def _deny_uniform(
        self, request: HttpRequest, resolution: TenantResolution
    ) -> HttpResponse:
        """A uniform denial that does not disclose whether the organization
        exists, whether the user is a member, or why.

        Returning different shapes for "no such org" and "not a member" turns
        the endpoint into an organization-enumeration oracle.
        """
        logger.info(
            "tenancy.access.denied",
            extra={
                "event": "tenancy.access.denied",
                "organization_slug": resolution.organization_slug,
                "membership_status": resolution.membership_status.value,
                "organization_status": resolution.organization_status.value,
            },
        )
        return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)

    def _deny(self, request: HttpRequest, exc: Workeasy360Error) -> HttpResponse:
        logger.info(
            "tenancy.resolution.failed",
            extra={"event": "tenancy.resolution.failed", "error_code": exc.code},
        )
        return JsonResponse(exc.as_dict(), status=exc.http_status)


def _write_block_message(status: OrganizationStatus) -> str:
    """A message that names the remedy, because the remedy differs."""
    if status is OrganizationStatus.SUSPENDED:
        return (
            "This organization is suspended and is read-only. Your data "
            "remains available to view and export. Settle the outstanding "
            "balance to restore write access."
        )
    if status is OrganizationStatus.PENDING_DELETION:
        return (
            "This organization is scheduled for deletion and is read-only. "
            "Cancel the deletion request to restore write access."
        )
    if status is OrganizationStatus.PROVISIONING:
        return "This organization is still being set up. Try again shortly."
    return "This organization cannot accept changes at present."
