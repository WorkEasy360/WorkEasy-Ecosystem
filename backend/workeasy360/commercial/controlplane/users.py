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

from datetime import datetime
from typing import Any
from uuid import UUID

from workeasy360.commercial.provisioning.selectors import (
    organization_ids_matching,
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
    product_code: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """``product_code`` keeps the people in an organization that holds that product
    now (the summary projection, as the customer directory's product filter);
    ``start``/``end`` the accounts created - signed up or invited - in
    ``[start, end)``. Both are applied by the database, under the cursor."""
    page = staff_users.directory(
        ctx,
        search=search,
        segment=segment,
        limit=limit,
        cursor=cursor,
        customer_orgs=organization_ids_with_access("customer"),
        trial_orgs=organization_ids_with_access("trial"),
        product_orgs=(
            organization_ids_matching(product_code=product_code) if product_code else None
        ),
        created_from=start,
        created_before=end,
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


def detail(
    ctx: TenantContext, user_id: UUID, *, record_view: bool = False
) -> dict[str, Any]:
    """The person, their organizations with what each holds, and - for a
    viewer who may see payments - the recent orders of those organizations.

    ADR-072, while WorkEasy360 Accounts hosts identity: also the person's seats
    per organization, their product connections (Connected Logins), their
    sign-in history and their sign-ins grouped per device - and, when
    ``record_view`` (the HTTP view), one control-plane audit entry per staff
    member per person per window."""
    from workeasy360.commercial.payments.orders import recent_orders_for_organizations
    from workeasy360.platform.identity import portals as portal_links

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
    if portal_links.accounts_identity():
        data.update(_accounts_view(ctx, user_id, data["memberships"]))
        if record_view:
            staff_users.record_view(
                ctx,
                user_id,
                sections=[
                    "organizations",
                    "products",
                    "connections",
                    "sign_in_history",
                    "sessions",
                ],
            )
    return data


def _accounts_view(
    ctx: TenantContext, user_id: UUID, memberships: list[dict[str, Any]]
) -> dict[str, Any]:
    """ADR-072: the Accounts Control Center's facts about one person, for
    ``platform.staff_user.view`` (already required by ``staff_users.detail``).
    No password, token, session key or full address: the session rows carry
    public ids and network prefixes only, exactly as the person sees them."""
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.contracts.errors import CommercialRecordNotFound
    from workeasy360.platform.authz.control_plane import organization_scope
    from workeasy360.platform.ecosystem import connections
    from workeasy360.platform.entitlements.services.seats import pools_held_by_member
    from workeasy360.platform.identity import account

    member_ids = {
        str(o["organization_id"]): o["membership_id"]
        for o in org_selectors.organizations_for_user(
            user_id, limit=org_selectors.MAX_PAGE_SIZE
        ).items
    }
    for membership in memberships:
        held: list[str] = []
        membership_id = member_ids.get(membership["organization_id"])
        if membership_id:
            try:
                with organization_scope(ctx, UUID(membership["organization_id"])):
                    held = sorted(
                        pools_held_by_member(
                            UUID(membership["organization_id"]),
                            membership_id=UUID(membership_id),
                        )
                    )
            except CommercialRecordNotFound:
                # The organization is gone (or is the platform's own): no seats.
                held = []
        membership["membership_id"] = membership_id
        membership["seats"] = held

    names = {m["organization_id"]: m["name"] for m in memberships}
    known = connections.connections_for_user(
        user_id, live_sign_in_ids=account.live_sign_in_ids(user_id)
    )
    history = account.account_event_page(user_id, kinds=account.SIGN_IN_KINDS, limit=50)
    codes = sorted(
        set(known)
        | {
            r["reason"]
            for r in history["results"]
            if r["kind"] == "product_sign_in" and r["reason"]
        }
    )
    shown = catalog.product_presentations(codes) if codes else {}

    def name_of(code: str, fallback: str = "") -> str:
        return shown.get(code, {}).get("name") or fallback or code

    for row in history["results"]:
        row["product_name"] = (
            name_of(row["reason"]) if row["kind"] == "product_sign_in" else None
        )
    return {
        "central_user_id": str(user_id),
        "connections": [
            {
                "product_code": code,
                "name": name_of(code, entry.display_name),
                "domain": {"host": entry.launch_host, "verified": entry.domain_verified},
                "takes_backchannel_logout": entry.takes_backchannel_logout,
                "platform_sign_in": entry.platform_sign_in,
                "platform_sign_in_since": _iso(entry.platform_sign_in_since),
                "platform_sign_in_ended_at": _iso(entry.platform_sign_in_ended_at),
                "last_sign_out": entry.last_sign_out,
                "organizations": [
                    {
                        "organization_id": str(o.organization_id),
                        "name": names.get(str(o.organization_id), ""),
                        "first_sign_in_at": _iso(o.first_sign_in_at),
                        "last_sign_in_at": _iso(o.last_sign_in_at),
                        "sign_in_count": o.sign_in_count,
                        "last_activity_at": _iso(o.last_activity_at),
                    }
                    for o in entry.organizations
                ],
            }
            for code, entry in sorted(known.items())
            if entry.organizations
        ],
        "sign_in_history": history["results"],
        "sign_ins": account.list_sign_ins(user_id, current_public_id=None),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
