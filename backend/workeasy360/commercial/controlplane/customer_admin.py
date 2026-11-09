"""What an Admin does for a customer beyond reading it (2026-11-09): create one, see its
members, assign a plan, read its whole assignment history.

**Assigning a plan is placing an order for the customer**, so a paid plan goes through
the same price resolution, invoice and payment as a customer's own order, and a free
plan is fulfilled at once. Nothing is granted that nobody paid for by this path. A
**manual exception** is the existing entitlement override: time-limited, with a reason,
behind its own grant.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from workeasy360.commercial.controlplane.customers import customer_context
from workeasy360.commercial.payments import orders
from workeasy360.commercial.subscriptions import admin_reads as subscription_reads
from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import acting_scope, organization_scope

VIEW = "platform.staff_customer.view"
MANAGE = "platform.staff_customer.manage"
SUBSCRIPTION_MANAGE = "platform.staff_subscription.manage"


def create_customer(
    ctx: TenantContext, *, name: Any, owner_email: Any, owner_name: Any, reason: Any
) -> dict[str, Any]:
    """A new organization with a named owner. An address already registered becomes
    the owner straight away; a new one gets an account and a link to set a password.
    No password is ever chosen for someone by staff."""
    from workeasy360.platform.administration.self_service import (
        _clean_email,
        request_password_reset,
    )
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.onboarding import services as onboarding

    authz.require(ctx, MANAGE)
    why = str(reason or "").strip()
    if len(why) < 10:
        raise CatalogValidationError("Say why, in at least 10 characters.", field="reason")
    typed, address = _clean_email(owner_email)
    full_name = " ".join(str(owner_name or "").split())[:200]
    if not full_name:
        raise CatalogValidationError("Name the owner.", field="owner_name")
    invited = False
    with transaction.atomic():
        user = lifecycle.lock_by_email(address)
        if user is None:
            try:
                with transaction.atomic():
                    user = lifecycle.create_account(
                        email=typed,
                        password=None,
                        full_name=full_name,
                        status=UserStatus.ACTIVE,
                        source=User.AcquisitionSource.OPERATOR,
                        verified=False,
                    )
            except (IntegrityError, ValidationError) as exc:
                raise CatalogValidationError(
                    "That address cannot be used.", field="owner_email"
                ) from exc
            invited = True
        elif user.status != UserStatus.ACTIVE.value:
            raise CatalogValidationError("That account cannot own an organization.", field="owner_email")
        result = onboarding.create_organization(user_id=user.pk, name=name)
    with acting_scope(ctx):
        audit.record(
            ctx,
            "customer.created_by_staff",
            resource_type="organizations.organization",
            resource_id=result.organization_id,
            # Kinds only: no name and no address in the audit trail.
            changes={"owner": "invited" if invited else "existing"},
            reason=why,
        )
    if invited:
        request_password_reset(address, portal="app")
    return {
        "organization_id": str(result.organization_id),
        "slug": str(result.slug),
        "owner_invited": invited,
    }


def members(ctx: TenantContext, organization_id: UUID) -> list[dict[str, Any]]:
    """Who belongs to this customer and in what standing. No credential of any kind."""
    from workeasy360.platform.organizations.selectors import member_rows_in_scope

    authz.require(ctx, VIEW)
    with organization_scope(ctx, organization_id):
        return member_rows_in_scope()


def assignment_history(ctx: TenantContext, organization_id: UUID) -> list[dict[str, Any]]:
    authz.require(ctx, VIEW)
    with organization_scope(ctx, organization_id):
        return subscription_reads.assignment_history(organization_id)


def assign_plan(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    plan_version_id: Any,
    billing_interval: Any,
    quantity: Any,
    reason: Any,
    idempotency_key: Any,
) -> dict[str, Any]:
    """Place the order the customer would have placed. Returns whether its invoice now
    waits for payment, which an Admin may record on the customer's billing tab."""
    authz.require(ctx, SUBSCRIPTION_MANAGE)
    why = str(reason or "").strip()
    if len(why) < 10:
        raise CatalogValidationError("Say why, in at least 10 characters.", field="reason")
    key = str(idempotency_key or "").strip()
    if not key:
        raise CatalogValidationError("A command id is required.", field="idempotency_key")
    rebound = customer_context(ctx, organization_id)
    with organization_scope(ctx, organization_id):
        placed = orders.place(
            rebound,
            idempotency_key=key,
            plan_version_id=str(plan_version_id or ""),
            billing_interval=billing_interval,
            quantity=quantity,
        )
        audit.record(
            rebound,
            "customer.plan_assigned",
            resource_type="payments.order",
            resource_id=placed["order_id"],
            changes={"number": placed["number"], "requires_payment": placed.get("requires_payment")},
            reason=why,
        )
    return placed
