"""The Super Admin's user directory with each person's commercial lifecycle
(Phase 9).

**One identity, classified - never moved.** A person is an *Explorer*, on
*Trial* or a *Customer* according to what the organizations they belong to
hold (``provisioning.CustomerSummary.access_class``, derived from the
entitlement computation). Buying a product changes the organization's class,
and so the same account's segment, without touching the account.

``platform/administration.staff_users`` owns the directory and the account
actions; it cannot see commercial state (F-4). This module passes it the two
lazy subqueries a lifecycle segment needs and adds, per page, each row's
lifecycle, organizations, products and subscription status - a constant
number of queries whatever the page size.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.provisioning.selectors import (
    organization_ids_with_access,
    summaries_for_organizations,
)
from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.administration import staff_users
from workeasy360.platform.authz import services as authz
from workeasy360.platform.organizations import selectors as org_selectors

#: When a person's organizations differ, the status shown is the most
#: engaged one.
_STATUS_ORDER = (
    "active",
    "trialing",
    "past_due",
    "grace",
    "suspended",
    "cancelled",
    "expired",
)


def lifecycle(status: str, classes: set[str]) -> str:
    """``unverified`` | ``customer`` | ``trial`` | ``explorer`` - one rule."""
    if status == UserStatus.PENDING.value:
        return "unverified"
    if "customer" in classes:
        return "customer"
    if "trial" in classes:
        return "trial"
    return "explorer"


def _best_status(statuses: list[str]) -> str:
    ranked = [s for s in _STATUS_ORDER if s in statuses]
    return ranked[0] if ranked else ""


def _commercial_view(
    organizations: list[dict[str, Any]], summaries: dict
) -> dict[str, Any]:
    held = [
        summaries[o["organization_id"]]
        for o in organizations
        if o["organization_id"] in summaries
    ]
    return {
        "classes": {s.access_class for s in held if s.access_class},
        "products": sorted({code for s in held for code in s.product_codes}),
        "subscription_status": _best_status([s.subscription_status for s in held]),
    }


def directory(
    ctx: TenantContext,
    *,
    search: str | None = None,
    segment: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    page = staff_users.directory(
        ctx,
        search=search,
        segment=segment,
        limit=limit,
        cursor=cursor,
        customer_orgs=organization_ids_with_access("customer"),
        trial_orgs=organization_ids_with_access("trial"),
    )
    rows = page["results"]
    access = org_selectors.access_rows_for_users([UUID(r["user_id"]) for r in rows])
    summaries = summaries_for_organizations(
        {o["organization_id"] for orgs in access.values() for o in orgs}
    )
    for row in rows:
        organizations = access.get(UUID(row["user_id"]), [])
        view = _commercial_view(organizations, summaries)
        row["lifecycle"] = lifecycle(row["status"], view["classes"])
        row["products"] = view["products"]
        row["subscription_status"] = view["subscription_status"]
        row["organization_names"] = [o["name"] for o in organizations[:3]]
    return page


def detail(ctx: TenantContext, user_id: UUID) -> dict[str, Any]:
    """The person, their organizations with what each holds, and - for a
    viewer who may see payments - the recent orders of those organizations."""
    from workeasy360.commercial.payments.orders import recent_orders_for_organizations

    data = staff_users.detail(ctx, user_id)
    organization_ids = [UUID(m["organization_id"]) for m in data["memberships"]]
    summaries = summaries_for_organizations(organization_ids)
    classes: set[str] = set()
    for membership in data["memberships"]:
        summary = summaries.get(UUID(membership["organization_id"]))
        membership["access_class"] = summary.access_class if summary else ""
        membership["subscription_status"] = summary.subscription_status if summary else ""
        membership["products"] = list(summary.product_codes) if summary else []
        if summary and summary.access_class:
            classes.add(summary.access_class)
    data["lifecycle"] = lifecycle(data["status"], classes)
    data["orders"] = []
    if authz.check(ctx, "platform.staff_payment.view").allowed:
        data["orders"] = recent_orders_for_organizations(organization_ids)
    return data
