"""Staff subscription operations and entitlement overrides (Tasks 11-13).

**Every command is the ordinary commercial command** - ``subscriptions``'
own suspend/resume/cancel/change-plan/change-quantity/activate - called with the
staff actor re-bound to the customer's organization and run inside that
organization's scope. There is no direct table manipulation here, no second
state machine and no ``force`` flag: each operation applies one transition the
domain's own table allows, so staff cannot do anything the domain forbids; they
only decide *when*.

**Overrides are narrow and explainable.** The one override this platform offers
is a time-boxed *entitlement grant* (an input to provisioning, never an edit of
an entitlement row), bounded to 90 days, requiring a reason, and
audited. Staff never write ``Entitlement`` rows: a grant is recorded and
provisioning is asked to reconcile, so the result is rebuildable (Task 13, 57).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from workeasy360.commercial.partners.domain import clean_reason
from workeasy360.commercial.subscriptions import services as subscription_services
from workeasy360.commercial.subscriptions.hooks import notify_commercial_change
from workeasy360.contracts.enums import ChangeTiming
from workeasy360.contracts.errors import CatalogValidationError, CommercialRecordNotFound
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope, organization_scope
from workeasy360.platform.entitlements.services import grants
from workeasy360.platform.observability.metrics import metrics

MANAGE = "platform.staff_subscription.manage"
OPERATIONS = "staff_subscription_operations_total"


def _key(ctx: TenantContext, client_key: Any) -> str:
    text = str(client_key or "").strip()
    if not text or len(text) > 90:
        raise CatalogValidationError(
            "An idempotency key of 1-90 characters is required.", field="idempotency_key"
        )
    return f"staff:{ctx.user_id}:{text}"


def _record(
    ctx: TenantContext,
    operation: str,
    organization_id: UUID,
    resource_id: Any,
    reason: str,
) -> None:
    """The control-plane audit entry; the customer-visible one is written by
    the commercial command itself, in the customer's own log."""
    audit.record(
        ctx,
        f"staff.subscription.{operation}",
        resource_type="subscriptions.subscription",
        resource_id=resource_id,
        changes={"organization_id": str(organization_id)},
        reason=reason,
    )
    metrics.increment(OPERATIONS, operation=operation)


@in_scope
def suspend(
    ctx: TenantContext,
    organization_id: UUID,
    subscription_id: UUID,
    *,
    reason: Any,
    idempotency_key: Any,
    expected_version: int | None = None,
) -> Any:
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with organization_scope(ctx, organization_id) as customer:
        result = subscription_services.suspend_subscription(
            customer,
            subscription_id=subscription_id,
            idempotency_key=_key(ctx, idempotency_key),
            reason_code=why[:64],
            expected_version=expected_version,
        )
    _record(ctx, "suspend", organization_id, subscription_id, why)
    return result


@in_scope
def resume(
    ctx: TenantContext,
    organization_id: UUID,
    subscription_id: UUID,
    *,
    reason: Any,
    idempotency_key: Any,
    expected_version: int | None = None,
) -> Any:
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with organization_scope(ctx, organization_id) as customer:
        result = subscription_services.resume_subscription(
            customer,
            subscription_id=subscription_id,
            idempotency_key=_key(ctx, idempotency_key),
            reason_code=why[:64],
            expected_version=expected_version,
        )
    _record(ctx, "resume", organization_id, subscription_id, why)
    return result


@in_scope
def activate(
    ctx: TenantContext,
    organization_id: UUID,
    subscription_id: UUID,
    *,
    reason: Any,
    idempotency_key: Any,
    expected_version: int | None = None,
) -> Any:
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with organization_scope(ctx, organization_id) as customer:
        result = subscription_services.activate_subscription(
            customer,
            subscription_id=subscription_id,
            idempotency_key=_key(ctx, idempotency_key),
            expected_version=expected_version,
        )
    _record(ctx, "activate", organization_id, subscription_id, why)
    return result


@in_scope
def cancel(
    ctx: TenantContext,
    organization_id: UUID,
    subscription_id: UUID,
    *,
    reason: Any,
    idempotency_key: Any,
    immediate: bool = False,
    expected_version: int | None = None,
) -> Any:
    """Cancel at period end (default) or, deliberately, now.

    An immediate cancellation is the bounded override for fraud or a mistaken
    signup: it needs the same dangerous permission as everything
    here, a recorded reason, and it is visible in the customer's own log.
    """
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with organization_scope(ctx, organization_id) as customer:
        result = subscription_services.cancel_subscription(
            customer,
            subscription_id=subscription_id,
            idempotency_key=_key(ctx, idempotency_key),
            immediate=bool(immediate),
            reason_code=why[:64],
            expected_version=expected_version,
        )
    _record(
        ctx,
        "cancel_now" if immediate else "cancel",
        organization_id,
        subscription_id,
        why,
    )
    return result


@in_scope
def change_plan(
    ctx: TenantContext,
    organization_id: UUID,
    item_id: UUID,
    *,
    to_plan_version_id: UUID,
    reason: Any,
    idempotency_key: Any,
    timing: ChangeTiming | None = None,
    expected_version: int | None = None,
) -> Any:
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with organization_scope(ctx, organization_id) as customer:
        result = subscription_services.change_plan(
            customer,
            subscription_item_id=item_id,
            to_plan_version_id=to_plan_version_id,
            idempotency_key=_key(ctx, idempotency_key),
            timing=timing,
            expected_version=expected_version,
        )
    _record(ctx, "change_plan", organization_id, item_id, why)
    return result


@in_scope
def change_quantity(
    ctx: TenantContext,
    organization_id: UUID,
    item_id: UUID,
    *,
    quantity: int,
    reason: Any,
    idempotency_key: Any,
    expected_version: int | None = None,
) -> Any:
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with organization_scope(ctx, organization_id) as customer:
        result = subscription_services.change_quantity(
            customer,
            subscription_item_id=item_id,
            quantity=quantity,
            idempotency_key=_key(ctx, idempotency_key),
            expected_version=expected_version,
        )
    _record(ctx, "change_quantity", organization_id, item_id, why)
    return result


# --- entitlement overrides --------------------------------------------------


@in_scope
def grant_entitlement_override(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    product_code: str = "",
    feature_key: str = "",
    value: Any = None,
    reason: Any,
    expires_at: datetime,
) -> Any:
    """A time-boxed support grant, then a reconcile - never a direct edit."""
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with organization_scope(ctx, organization_id) as customer:
        grant = grants.issue_grant(
            customer,
            product_code=product_code,
            feature_key=feature_key,
            value=value,
            reason=why,
            expires_at=expires_at,
        )
        # Provisioning is asked to recompute through the same change registry
        # every commercial command uses; the hourly reconcile is the guarantee.
        notify_commercial_change(organization_id)
    audit.record(
        ctx,
        "staff.entitlement.override_granted",
        resource_type="entitlements.grant",
        resource_id=grant.id,
        changes={
            "organization_id": str(organization_id),
            "product_code": product_code,
            "feature_key": feature_key,
            "expires_at": expires_at.isoformat(),
        },
        reason=why,
    )
    metrics.increment(OPERATIONS, operation="override_grant")
    return grant


@in_scope
def revoke_entitlement_override(
    ctx: TenantContext, organization_id: UUID, grant_id: UUID, *, reason: Any
) -> None:
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with organization_scope(ctx, organization_id) as customer:
        revoked = grants.revoke_grant(customer, grant_id)
        if revoked is None:
            raise CommercialRecordNotFound("No such override.", grant_id=str(grant_id))
        # Provisioning is asked to recompute through the same change registry
        # every commercial command uses; the hourly reconcile is the guarantee.
        notify_commercial_change(organization_id)
    audit.record(
        ctx,
        "staff.entitlement.override_revoked",
        resource_type="entitlements.grant",
        resource_id=grant_id,
        changes={"organization_id": str(organization_id)},
        reason=why,
    )
    metrics.increment(OPERATIONS, operation="override_revoke")
