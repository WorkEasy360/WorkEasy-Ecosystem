"""Reads over the customer summary projection (see ``models.CustomerSummary``).

Control-plane reads: no tenant scope is needed or used, and nothing here
reaches a tenant table. Callers pass organization ids they have **already
authorised** (the staff directory, or a partner's own relationships) - this
module answers "what does the projection say about these", never "which
organizations may this caller see".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CustomerSummaryDTO:
    organization_id: UUID
    subscription_status: str
    product_codes: tuple[str, ...]
    seat_capacity: int | None
    computed_at: datetime
    access_class: str = ""


def summaries_for_organizations(
    organization_ids: Iterable[UUID],
) -> dict[UUID, CustomerSummaryDTO]:
    """The projection rows for these organizations - one query."""
    from workeasy360.commercial.provisioning.models import CustomerSummary

    ids = list(organization_ids)
    if not ids:
        return {}
    return {
        row.organization_id: CustomerSummaryDTO(
            organization_id=row.organization_id,
            subscription_status=row.subscription_status,
            product_codes=tuple(row.product_codes or ()),
            seat_capacity=row.seat_capacity,
            computed_at=row.computed_at,
            access_class=row.access_class,
        )
        for row in CustomerSummary.objects.filter(organization_id__in=ids)
    }


def status_counts() -> dict[str, int]:
    """Customers by subscription status, for the dashboard. One query."""
    from django.db.models import Count

    from workeasy360.commercial.provisioning.models import CustomerSummary

    return {
        row["subscription_status"] or "none": row["n"]
        for row in CustomerSummary.objects.values("subscription_status").annotate(
            n=Count("organization_id")
        )
    }


def organization_ids_with_access(access_class: str) -> Any:
    """The organizations of one commercial class (``customer`` or ``trial``),
    as a **lazy** subquery of ids - composed into a larger statement by the
    caller (the staff user segments), never evaluated into a list here, so a
    segment over any number of customers stays one query (Phase 9)."""
    from workeasy360.commercial.provisioning.models import CustomerSummary

    return CustomerSummary.objects.filter(access_class=access_class).values(
        "organization_id"
    )


def organization_ids_matching(
    *,
    subscription_status: str | None = None,
    access_class: str | None = None,
    product_code: str | None = None,
) -> Any:
    """A lazy subquery of the organizations whose summary matches every given
    filter - for the staff directory's Subscriptions and Entitlements views
    (Phase 9). ``product_code`` is "holds this product now"."""
    from workeasy360.commercial.provisioning.models import CustomerSummary

    rows = CustomerSummary.objects.all()
    if subscription_status:
        rows = rows.filter(subscription_status=subscription_status)
    if access_class:
        rows = rows.filter(access_class=access_class)
    if product_code:
        rows = rows.filter(product_codes__contains=[product_code])
    return rows.values("organization_id")


def organization_ids_with_any(*, subscription: bool = False, access: bool = False) -> Any:
    """Organizations that *have* an open subscription or a commercial access
    class - excluded to answer "none" (an organization never reconciled has no
    summary row at all, so "none" cannot be a positive match)."""
    from workeasy360.commercial.provisioning.models import CustomerSummary

    rows = CustomerSummary.objects.all()
    if subscription:
        rows = rows.exclude(subscription_status="")
    if access:
        rows = rows.exclude(access_class="")
    return rows.values("organization_id")


# --- the partner panel's customer counts (ADR-045) -----------------------------------

ACTIVE_STATES = ("active", "trialing")


def active_subscription_count(organization_ids: Any) -> int:
    """How many of these organizations (a queryset of ids, or a list) hold an
    active or trialling subscription. One query."""
    from workeasy360.commercial.provisioning.models import CustomerSummary

    return CustomerSummary.objects.filter(
        subscription_status__in=ACTIVE_STATES, organization_id__in=organization_ids
    ).count()


def product_holders(organization_ids: Any, product_code: str) -> tuple[int, int]:
    """``(organizations that hold the product, those with an active subscription)``
    among these organizations. Two queries."""
    from workeasy360.commercial.provisioning.models import CustomerSummary

    held = CustomerSummary.objects.filter(
        organization_id__in=organization_ids, product_codes__contains=[product_code]
    )
    return held.count(), held.filter(subscription_status__in=ACTIVE_STATES).count()
