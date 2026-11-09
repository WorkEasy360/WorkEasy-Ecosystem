"""The Admin console's reads across customers, from the subscription projections.

Each function either authorizes (``platform.staff_customer.view``) or is called by a
screen that has. History of one customer stays on its own tenant rows.

**Separate states.** A subscription's status is one fact. "Pending Setup" is not one of
them: payment, subscription, setup and member access are separate, so that is a filter
over the setup records (``customers/setup``), never a value here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import Count, Q
from django.utils import timezone

from workeasy360.commercial.subscriptions.models import (
    Subscription,
    SubscriptionItem,
    SubscriptionItemSummary,
    SubscriptionSummary,
)
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.cursor import decode_cursor, encode_cursor
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.organizations.selectors import organization_names

VIEW = "platform.staff_customer.view"

#: Running and billable.
RUNNING = ("active", "past_due", "grace")
KNOWN_STATUSES = {
    "trialing",
    "active",
    "past_due",
    "grace",
    "suspended",
    "cancelled",
    "expired",
}
MAX_PAGE = 100


def running_at(end: Any | None) -> int:
    """Subscriptions running at ``end``, or now."""
    if end is None:
        return SubscriptionSummary.objects.filter(status__in=RUNNING).count()
    return SubscriptionSummary.objects.filter(
        Q(ended_at__isnull=True) | Q(ended_at__gte=end), activated_at__lt=end
    ).count()


def running_by_product() -> dict[str, int]:
    """Customers holding each product on a running subscription now."""
    now = timezone.now()
    running = SubscriptionSummary.objects.filter(status__in=RUNNING).values(
        "subscription_id"
    )
    rows = (
        SubscriptionItemSummary.objects.filter(subscription_id__in=running)
        .filter(Q(removed_at__isnull=True) | Q(removed_at__gt=now))
        .exclude(product_code="")
        .values("product_code")
        .annotate(subscriptions=Count("organization_id", distinct=True))
    )
    return {row["product_code"]: int(row["subscriptions"]) for row in rows}


def for_organizations(organization_ids: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    rows = SubscriptionSummary.objects.filter(
        organization_id__in=list(organization_ids)
    ).order_by("-created_at")[:limit]
    return [
        {
            "subscription_id": str(row.subscription_id),
            "organization_id": row.organization_id,
            "status": row.status,
            "billing_interval": row.billing_interval,
        }
        for row in rows
    ]


def status_counts() -> dict[str, int]:
    return {
        row["status"]: row["n"]
        for row in SubscriptionSummary.objects.values("status").annotate(
            n=Count("subscription_id")
        )
    }


def _lines(subscription_ids: list[Any]) -> dict[Any, list[dict[str, Any]]]:
    now = timezone.now()
    out: dict[Any, list[dict[str, Any]]] = {}
    for row in SubscriptionItemSummary.objects.filter(
        subscription_id__in=subscription_ids
    ).filter(Q(removed_at__isnull=True) | Q(removed_at__gt=now)):
        out.setdefault(row.subscription_id, []).append(
            {
                "product_code": row.product_code,
                "plan_code": row.plan_code,
                "bundle_code": row.bundle_code,
                "quantity": row.quantity,
                "total_amount_minor": row.total_amount_minor,
                "currency": row.currency,
                "interval": row.interval,
            }
        )
    return out


def subscriptions(
    ctx: TenantContext,
    *,
    status: Any = None,
    product: Any = None,
    renewing_within_days: Any = None,
    search: Any = None,
    limit: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    """Subscriptions across customers, newest first, with their current lines."""
    authz.require(ctx, VIEW)
    rows = SubscriptionSummary.objects.all()
    if status:
        if status not in KNOWN_STATUSES:
            raise CatalogValidationError("Unknown status.", field="status")
        rows = rows.filter(status=status)
    if product:
        code = str(product).strip()[:64]
        rows = rows.filter(
            subscription_id__in=SubscriptionItemSummary.objects.filter(
                product_code=code, removed_at__isnull=True
            ).values("subscription_id")
        )
    if renewing_within_days:
        try:
            days = int(renewing_within_days)
        except (TypeError, ValueError) as exc:
            raise CatalogValidationError("Unknown range.", field="renewing") from exc
        if not 1 <= days <= 366:
            raise CatalogValidationError("Unknown range.", field="renewing")
        from datetime import timedelta

        now = timezone.now()
        rows = rows.filter(
            status__in=RUNNING,
            cancel_at__isnull=True,
            current_period_end__gte=now,
            current_period_end__lt=now + timedelta(days=days),
        )
    if search:
        from workeasy360.platform.organizations.selectors import (
            organizations_directory_page,
        )

        matches = organizations_directory_page(search=str(search), limit=100).items
        rows = rows.filter(
            organization_id__in=[UUID(m["organization_id"]) for m in matches]
        )
    try:
        size = max(1, min(int(limit or 25), MAX_PAGE))
    except (TypeError, ValueError):
        size = 25
    after = decode_cursor(cursor)
    if after is not None:
        rows = rows.filter(
            Q(created_at__lt=after[0])
            | Q(created_at=after[0], subscription_id__lt=after[1])
        )
    page = list(rows.order_by("-created_at", "-subscription_id")[: size + 1])
    more = len(page) > size
    page = page[:size]
    names = organization_names({r.organization_id for r in page})
    lines = _lines([r.subscription_id for r in page])
    return {
        "results": [
            {
                "subscription_id": str(r.subscription_id),
                "organization_id": str(r.organization_id),
                "organization_name": names.get(r.organization_id, ""),
                "status": r.status,
                "currency": r.currency,
                "billing_interval": r.billing_interval,
                "renewal_policy": r.renewal_policy,
                "current_period_end": r.current_period_end.isoformat(),
                "trial_ends_at": r.trial_ends_at.isoformat() if r.trial_ends_at else None,
                "activated_at": r.activated_at.isoformat() if r.activated_at else None,
                "cancel_at": r.cancel_at.isoformat() if r.cancel_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "lines": lines.get(r.subscription_id, []),
            }
            for r in page
        ],
        "next_cursor": encode_cursor(page[-1].created_at, page[-1].subscription_id)
        if more and page
        else None,
        "has_more": more,
        "counts": status_counts(),
    }


def assignment_history(organization_id: UUID) -> list[dict[str, Any]]:
    """Every line this customer has held, newest first: what was assigned, when, and
    when it ended. For a caller already inside this customer's scope. The lines are
    never deleted (``SubscriptionItem``), so this is the whole history."""
    rows = SubscriptionItem.objects.filter(organization_id=organization_id).order_by(
        "-added_at"
    )[:200]
    statuses = dict(
        Subscription.objects.filter(organization_id=organization_id).values_list(
            "id", "status"
        )
    )
    return [
        {
            "item_id": str(row.id),
            "subscription_id": str(row.subscription_id),
            "subscription_status": statuses.get(row.subscription_id, ""),
            "product_code": row.product_code,
            "plan_code": row.plan_code,
            "bundle_code": row.bundle_code,
            "quantity": row.quantity,
            "price_reason": row.price_reason,
            "total_amount_minor": row.total_amount_minor,
            "currency": row.currency,
            "interval": row.interval,
            "added_at": row.added_at.isoformat(),
            "removed_at": row.removed_at.isoformat() if row.removed_at else None,
        }
        for row in rows
    ]


def holders_of_products(products: Any) -> list[tuple[Any, str]]:
    """``(organization, product)`` for each customer holding one of these products on
    a subscription that has not ended. For the setup queue, whose caller authorized."""
    now = timezone.now()
    live = SubscriptionSummary.objects.exclude(
        status__in=("expired", "cancelled")
    ).values("subscription_id")
    rows = (
        SubscriptionItemSummary.objects.filter(
            subscription_id__in=live, product_code__in=products
        )
        .filter(Q(removed_at__isnull=True) | Q(removed_at__gt=now))
        .values_list("organization_id", "product_code")
        .distinct()
    )
    return list(rows)


def report_renewals(
    *, start: Any, end: Any, product: Any, search: Any, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Running subscriptions whose period ends in the window. Callers authorize."""
    rows = SubscriptionSummary.objects.filter(
        status__in=RUNNING, current_period_end__gte=start, current_period_end__lt=end
    )
    if product:
        rows = rows.filter(
            subscription_id__in=SubscriptionItemSummary.objects.filter(
                product_code=product, removed_at__isnull=True
            ).values("subscription_id")
        )
    if search:
        from workeasy360.platform.organizations.selectors import (
            organizations_directory_page,
        )

        matches = organizations_directory_page(search=search, limit=100).items
        rows = rows.filter(
            organization_id__in=[UUID(m["organization_id"]) for m in matches]
        )
    total = rows.count()
    page = list(rows.order_by("current_period_end")[offset : offset + limit])
    names = organization_names({r.organization_id for r in page})
    lines = _lines([r.subscription_id for r in page])
    return (
        [
            {
                "subscription_id": str(r.subscription_id),
                "organization_id": str(r.organization_id),
                "organization_name": names.get(r.organization_id, ""),
                "products": ", ".join(
                    sorted(
                        {
                            i["product_code"] or i["bundle_code"]
                            for i in lines.get(r.subscription_id, [])
                        }
                    )
                ),
                "status": r.status,
                "billing_interval": r.billing_interval,
                "renewal_policy": r.renewal_policy,
                "current_period_end": r.current_period_end.isoformat(),
            }
            for r in page
        ],
        total,
    )


def running_plan_codes(organization_id: UUID) -> list[str]:
    """The plans this customer holds on a running subscription now."""
    now = timezone.now()
    running = SubscriptionSummary.objects.filter(
        organization_id=organization_id, status__in=RUNNING
    ).values("subscription_id")
    return list(
        SubscriptionItemSummary.objects.filter(subscription_id__in=running)
        .filter(Q(removed_at__isnull=True) | Q(removed_at__gt=now))
        .exclude(plan_code="")
        .values_list("plan_code", flat=True)
    )
