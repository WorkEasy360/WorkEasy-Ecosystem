"""The Admin console's search across records (2026-11-09).

**Each group only for someone holding the grant of its screen.** A person without the
payments grant gets no orders or invoices back, not an empty group that says they exist.
Names, codes and numbers only: nothing sensitive is searched or returned, and a user's
email goes only to someone who may already read the user directory. A result is a label
and a link; the page behind the link authorizes again.
"""

from __future__ import annotations

from typing import Any

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.organizations import selectors as org_selectors

MIN_QUERY = 2
MAX_QUERY = 80
PER_GROUP = 5


def _row(kind: str, row_id: Any, label: str, hint: str, href: str) -> dict[str, str]:
    return {"kind": kind, "id": str(row_id), "label": label, "hint": hint, "href": href}


def _customers(term: str) -> list[dict[str, str]]:
    page = org_selectors.organizations_directory_page(search=term, limit=PER_GROUP)
    return [
        _row(
            "customer",
            row["organization_id"],
            row["display_name"],
            row["slug"],
            f"/admin/customers/{row['organization_id']}",
        )
        for row in page.items
    ]


def _users(ctx: TenantContext, term: str) -> list[dict[str, str]]:
    from workeasy360.platform.administration import staff_users

    page = staff_users.directory(ctx, search=term, limit=PER_GROUP)["results"]
    return [
        _row(
            "user",
            row["user_id"],
            row.get("name") or row["email"],
            row["email"],
            f"/admin/users/{row['user_id']}",
        )
        for row in page
    ]


def _partners(term: str) -> list[dict[str, str]]:
    page = partner_selectors.partners_page(query=term, limit=PER_GROUP)
    return [
        _row(
            "partner",
            row["id"],
            row["display_name"],
            row["contact_email"] or row["code"],
            f"/admin/partners/{row['id']}",
        )
        for row in page.items
    ]


def _products(term: str) -> list[dict[str, str]]:
    return [
        _row("product", row["id"], row["name"], row["code"], f"/admin/products/{row['id']}")
        for row in catalog_selectors.search_products(term, limit=PER_GROUP)
    ]


def _subscriptions(term: str) -> list[dict[str, str]]:
    """Found through the customer's name, because that is what people remember."""
    from uuid import UUID

    from workeasy360.commercial.subscriptions.admin_reads import for_organizations

    customers = org_selectors.organizations_directory_page(search=term, limit=PER_GROUP).items
    names = {UUID(row["organization_id"]): row["display_name"] for row in customers}
    return [
        _row(
            "subscription",
            row["subscription_id"],
            names.get(row["organization_id"], "Subscription"),
            f"{row['status']} · {row['billing_interval']}",
            f"/admin/customers/{row['organization_id']}",
        )
        for row in for_organizations(names, limit=PER_GROUP)
    ]


def _orders(term: str) -> list[dict[str, str]]:
    from workeasy360.commercial.payments.admin_reads import search_orders

    return [
        _row("order", row["id"], row["number"], row["description"], f"/admin/orders?q={row['number']}")
        for row in search_orders(term, limit=PER_GROUP)
    ]


def _invoices(term: str) -> list[dict[str, str]]:
    from workeasy360.commercial.payments.admin_reads import search_invoices

    return [
        _row("invoice", row["id"], row["number"], row["status"], f"/admin/invoices?q={row['number']}")
        for row in search_invoices(term, limit=PER_GROUP)
    ]


def search(ctx: TenantContext, raw: Any) -> dict[str, Any]:
    term = " ".join(str(raw or "").split())[:MAX_QUERY]
    if len(term) < MIN_QUERY:
        return {"query": term, "results": []}
    allowed = lambda permission: authz.check(ctx, permission).allowed  # noqa: E731
    results: list[dict[str, str]] = []
    if allowed("platform.staff_customer.view"):
        results += _customers(term)
        results += _subscriptions(term)
    if allowed("platform.staff_user.view"):
        results += _users(ctx, term)
    if allowed("platform.staff_partner.view"):
        results += _partners(term)
    if allowed("platform.staff_catalog.view"):
        results += _products(term)
    if allowed("platform.staff_payment.view"):
        results += _orders(term)
        results += _invoices(term)
    return {"query": term, "results": results}
