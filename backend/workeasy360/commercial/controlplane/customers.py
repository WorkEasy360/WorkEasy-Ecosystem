"""The customer directory, customer detail and organization suspension (Tasks 4-6, 15, 54).

Reads are bounded and constant-query; the detail assembles **commercial and
access facts only** - subscriptions, entitlements, seats, billing state, the
partner relationship, integration status and the customer's own audit trail.
There is no product-domain record to show, and none is reached.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction

from workeasy360.commercial.billing.services import billing_account_for
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.domain import clean_reason
from workeasy360.commercial.provisioning.selectors import summaries_for_organizations
from workeasy360.commercial.subscriptions import selectors as subscription_selectors
from workeasy360.contracts.enums import OrganizationStatus
from workeasy360.contracts.errors import CatalogValidationError, CommercialRecordNotFound
from workeasy360.contracts.events import platform_events
from workeasy360.contracts.values.cursor import Page
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events import services as events
from workeasy360.platform.audit import selectors as audit_selectors
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import (
    context_for_organization,
    in_scope,
    organization_scope,
)
from workeasy360.platform.entitlements.selectors import stored_snapshot
from workeasy360.platform.entitlements.services import grants as entitlement_grants
from workeasy360.platform.entitlements.services import seats
from workeasy360.platform.observability.metrics import metrics
from workeasy360.platform.organizations import selectors as org_selectors
from workeasy360.platform.organizations.services import set_lifecycle_status

VIEW = "platform.staff_customer.view"
MANAGE = "platform.staff_customer.manage"
BILLING_VIEW = "platform.staff_billing.view"
PARTNER_VIEW = "platform.staff_partner.view"

CUSTOMER_OPERATIONS = "staff_customer_operations_total"


def directory(
    ctx: TenantContext,
    *,
    search: str | None = None,
    status: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
    subscription_status: str | None = None,
    access_class: str | None = None,
    product_code: str | None = None,
) -> Page:
    """One page of customers with subscription, products and partner.

    Three queries whatever the page size: the organizations (with owners), the
    summary projection for exactly those, and the partner links for exactly
    those. Seat *capacity* comes from the projection; assignment is shown only
    in the detail, from the authoritative service (Task 15).
    """
    from workeasy360.commercial.provisioning.selectors import (
        organization_ids_matching,
        organization_ids_with_any,
    )

    authz.require(ctx, VIEW)
    # ``""`` means "none": answered by exclusion (see organization_ids_with_any).
    wants = any(v for v in (subscription_status, access_class, product_code))
    page = org_selectors.organizations_directory_page(
        search=search,
        status=status,
        limit=limit,
        cursor=cursor,
        only=(
            organization_ids_matching(
                subscription_status=subscription_status,
                access_class=access_class,
                product_code=product_code,
            )
            if wants
            else None
        ),
        exclude=(
            organization_ids_with_any(
                subscription=subscription_status == "",
                access=access_class == "",
            )
            if "" in (subscription_status, access_class)
            else None
        ),
    )
    ids = [UUID(item["organization_id"]) for item in page.items]
    summaries = summaries_for_organizations(ids)
    links = partner_selectors.links_for_organizations(ids)
    for item in page.items:
        oid = UUID(item["organization_id"])
        summary = summaries.get(oid)
        item["subscription_status"] = summary.subscription_status if summary else ""
        item["products"] = list(summary.product_codes) if summary else []
        item["seat_capacity"] = summary.seat_capacity if summary else None
        item["access_class"] = summary.access_class if summary else ""
        item["summary_as_of"] = summary.computed_at.isoformat() if summary else None
        item["partner"] = links.get(oid)
    return page


def detail(ctx: TenantContext, organization_id: UUID) -> dict[str, Any]:
    """Everything staff may know about one customer, read-only."""
    authz.require(ctx, VIEW)
    lifecycle = org_selectors.organization_lifecycle(organization_id)
    if lifecycle is None:
        raise CommercialRecordNotFound(
            "No such customer.", organization_id=str(organization_id)
        )

    can_billing = authz.check(ctx, BILLING_VIEW).allowed
    can_partner = authz.check(ctx, PARTNER_VIEW).allowed
    with organization_scope(ctx, organization_id) as customer:
        members = org_selectors.membership_counts(customer)
        subscriptions = [
            _subscription_payload(s)
            for s in subscription_selectors.subscriptions_for_organization()
        ]
        snapshot = stored_snapshot(organization_id)
        products = dict(snapshot.products) if snapshot else {}
        licenses = []
        for code in sorted(products):
            assigned = seats.assigned_count(organization_id, pool_key=code)
            capacity = seats.capacity(organization_id, pool_key=code)
            licenses.append(
                {
                    "product_code": code,
                    "seats_total": capacity,
                    "seats_assigned": assigned,
                    "over_limit": len(
                        seats.over_limit_assignments(organization_id, pool_key=code)
                    ),
                }
            )
        billing = _billing_payload(organization_id) if can_billing else None
        grants = entitlement_grants.live_grants(customer)
        trail = audit_selectors.audit_page(limit=20).items

    link = partner_selectors.live_link_for_organization(organization_id)
    partner = (
        {
            "partner_id": str(link.partner_id),
            "code": link.partner.code,
            "display_name": link.partner.display_name,
            "link_status": link.status,
            "started_at": link.started_at.isoformat(),
        }
        if link is not None and can_partner
        else None
    )
    return {
        "organization": lifecycle,
        "members": members,
        "subscriptions": subscriptions,
        "entitlements": {
            code: {"status": info.get("status"), "seats": info.get("seats")}
            for code, info in products.items()
        },
        "licenses": licenses,
        "entitlement_overrides": grants,
        "billing": billing,
        "partner": partner,
        "audit": trail,
    }


def _subscription_payload(dto: Any) -> dict[str, Any]:
    return {
        "id": str(dto.id),
        "status": dto.status.value,
        "currency": dto.currency,
        "billing_interval": dto.billing_interval.value,
        "current_period_end": dto.current_period_end.isoformat(),
        "cancel_at": dto.cancel_at.isoformat() if dto.cancel_at else None,
        "version": dto.version,
        "items": [
            {
                "id": str(i.id),
                "product_code": i.product_code,
                "plan_code": i.plan_code,
                "bundle_code": i.bundle_code,
                "quantity": i.quantity,
                "is_derived": i.is_derived,
                "removed_at": i.removed_at.isoformat() if i.removed_at else None,
            }
            for i in dto.items
        ],
    }


def _billing_payload(organization_id: UUID) -> dict[str, Any] | None:
    """Billing state, labelled for what it is: there is **no payment provider**.

    Currency, collection mode and region only. No contact, no tax reference and
    no card - none is stored, and inventing a "last payment" would present a
    fiction as fact (Task 54).
    """
    try:
        account = billing_account_for(organization_id)
    except CatalogValidationError:
        return None
    return {
        "currency": account.currency,
        "collection_mode": account.collection_mode,
        "billing_region": account.billing_region,
        "provider": "none",
        "note": "Provider-independent, manual state. No payment is collected or recorded by this platform.",
    }


# --- organization lifecycle -------------------------------------------------


@in_scope
def suspend_organization(
    ctx: TenantContext, organization_id: UUID, *, reason: Any
) -> dict[str, Any]:
    """Make a customer read-only. Nothing is deleted or hidden.

    Dangerous (AAL 2). The organization is locked while it changes state;
    commercial writes, partner writes and new launches stop through the
    existing R-10 write block, which reads this same status.
    """
    return _change(ctx, organization_id, OrganizationStatus.SUSPENDED, reason)


@in_scope
def reactivate_organization(
    ctx: TenantContext, organization_id: UUID, *, reason: Any
) -> dict[str, Any]:
    return _change(ctx, organization_id, OrganizationStatus.ACTIVE, reason)


def _change(
    ctx: TenantContext, organization_id: UUID, target: OrganizationStatus, reason: Any
) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    if org_selectors.organization_lifecycle(organization_id) is None:
        raise CommercialRecordNotFound(
            "No such customer.", organization_id=str(organization_id)
        )

    with transaction.atomic():
        previous = set_lifecycle_status(organization_id, target=target, reason=why)
        if previous is not target:
            schema = (
                platform_events.ORGANIZATION_SUSPENDED_V1
                if target is OrganizationStatus.SUSPENDED
                else platform_events.ORGANIZATION_REACTIVATED_V1
            )
            payload: dict[str, Any] = {"organization_id": str(organization_id)}
            if target is OrganizationStatus.SUSPENDED:
                payload["reason"] = "staff_action"
            events.publish(
                events.build_event(
                    ctx,
                    schema.event_type,
                    1,
                    aggregate_type=schema.aggregate_type,
                    aggregate_id=organization_id,
                    payload=payload,
                )
            )
            action = f"organization.{target.value}_by_staff"
            audit.record(
                ctx,
                action,
                resource_type="organizations.organization",
                resource_id=organization_id,
                changes={"from": previous.value, "to": target.value},
                reason=why,
            )
            # And in the customer's own log, where its administrators read it.
            with organization_scope(ctx, organization_id) as customer:
                audit.record(
                    customer,
                    action,
                    resource_type="organizations.organization",
                    resource_id=organization_id,
                    changes={"from": previous.value, "to": target.value},
                    reason=why,
                )
    metrics.increment(CUSTOMER_OPERATIONS, operation=target.value)
    return org_selectors.organization_lifecycle(organization_id) or {}


def customer_context(ctx: TenantContext, organization_id: UUID) -> TenantContext:
    """The staff actor re-bound to a customer (404 if there is none)."""
    return context_for_organization(ctx, organization_id)
