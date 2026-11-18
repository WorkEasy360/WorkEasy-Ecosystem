"""The organization console's bell (User Admin Panel, 2026-11-11).

What needs a member's attention, worked out from live state on each request - never
stored, never invented: a product waiting for setup details, a renewal or a trial
ending soon, an order or invoice waiting for payment, a wallet under the threshold an
Admin set, a support reply to a ticket the member opened.

**Each category only for those who may see its data.** Renewals need
``commercial.subscription.view``; payments and balances ``platform.billing.view``;
setup and tickets are any member's (the setup page and the tickets are). Then the
member's own in-app choices (``notifications.preferences``) drop what they turned off.
The organization is always the verified tenant context's.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

RENEWAL_DAYS = 14
TRIAL_DAYS = 7
MAX_ITEMS = 30

_KIND_PAGE = {
    "platform": "wallets/platform",
    "ai_credits": "wallets/ai-credits",
    "consumption": "wallets/consumption",
}


def _item(
    key: str,
    category: str,
    tone: str,
    title: str,
    detail: str,
    href: str,
    occurred_at: Any = None,
) -> dict[str, Any]:
    return {
        "id": f"{category}:{key}",
        "category": category,
        "tone": tone,
        "title": title,
        "detail": detail,
        "href": href,
        "occurred_at": occurred_at.isoformat()
        if hasattr(occurred_at, "isoformat")
        else occurred_at,
    }


def _day(moment: Any) -> str:
    return moment.strftime("%d %b %Y")


def _product_names(codes: set[str]) -> dict[str, str]:
    from workeasy360.commercial.catalog.selectors import product_names

    return product_names(sorted(codes))


def _setup(ctx: TenantContext) -> list[dict[str, Any]]:
    from workeasy360.commercial.provisioning.setup import needing_customer

    rows = needing_customer(ctx.organization_id)
    names = _product_names({r["product_code"] for r in rows})
    out = []
    for row in rows:
        name = names.get(row["product_code"], row["product_code"])
        if row["status"] == "correction_required":
            out.append(
                _item(
                    row["id"],
                    "setup",
                    "warning",
                    f"{name}: corrections needed",
                    row["decision_reason"]
                    or "Workeasy360 asked for changes to your setup details.",
                    "apps?filter=setup",
                    row["at"],
                )
            )
        else:
            out.append(
                _item(
                    row["id"],
                    "setup",
                    "info",
                    f"{name}: setup details needed",
                    "Complete the setup so the product can be activated.",
                    "apps?filter=setup",
                    row["at"],
                )
            )
    return out


def _renewals(ctx: TenantContext) -> list[dict[str, Any]]:
    from workeasy360.commercial.subscriptions.selectors import (
        product_trials,
        subscriptions_for_organization,
    )

    now = timezone.now()
    soon = now + timedelta(days=RENEWAL_DAYS)
    out: list[dict[str, Any]] = []
    for sub in subscriptions_for_organization():
        status = sub.status.value
        if status == "expired":
            continue
        ends = sub.cancel_at or (
            sub.current_period_end if status == "cancelled" else None
        )
        if (
            sub.trial_ends_at
            and now <= sub.trial_ends_at <= soon
            and status == "trialing"
        ):
            out.append(
                _item(
                    f"{sub.id}:trial",
                    "renewal",
                    "warning",
                    "Trial ends soon",
                    f"Your trial ends on {_day(sub.trial_ends_at)}.",
                    "settings/billing",
                    sub.trial_ends_at,
                )
            )
        elif ends and now <= ends <= soon:
            out.append(
                _item(
                    f"{sub.id}:ends",
                    "renewal",
                    "warning",
                    "Subscription ends soon",
                    f"It is set to end on {_day(ends)}.",
                    "settings/billing",
                    ends,
                )
            )
        elif not ends and now <= sub.current_period_end <= soon:
            auto = sub.renewal_policy.value == "auto_renew"
            out.append(
                _item(
                    f"{sub.id}:renews",
                    "renewal",
                    "info",
                    "Renewal coming up" if auto else "Subscription period ends soon",
                    f"{'Renews' if auto else 'The period ends'} on {_day(sub.current_period_end)}.",
                    "settings/billing",
                    sub.current_period_end,
                )
            )
    trial_soon = now + timedelta(days=TRIAL_DAYS)
    trials = [
        t
        for t in product_trials()
        if t.status.value == "active" and now <= t.ends_at <= trial_soon
    ]
    names = _product_names({t.product_code for t in trials})
    for t in trials:
        out.append(
            _item(
                f"{t.id}:product-trial",
                "renewal",
                "warning",
                f"{names.get(t.product_code, t.product_code)} trial ends soon",
                f"The free trial ends on {_day(t.ends_at)}.",
                "apps?filter=trial",
                t.ends_at,
            )
        )
    return out


def _payments(ctx: TenantContext) -> list[dict[str, Any]]:
    from workeasy360.commercial.payments.selectors import awaiting_payment

    now = timezone.now()
    due = awaiting_payment(ctx)
    out: list[dict[str, Any]] = [
        _item(
            order["id"],
            "payment",
            "warning",
            f"Order {order['number']} is waiting for payment",
            order["description"] or "Pay it to finish the purchase.",
            "orders",
            order["created_at"],
        )
        for order in due["orders"]
    ]
    out.extend(invoice_item(invoice, now) for invoice in due["invoices"])
    return out


def invoice_item(invoice: dict[str, Any], now: Any) -> dict[str, Any]:
    """An open invoice in the bell. Overdue only once its due DAY has passed: an
    invoice on immediate terms is due the moment it is issued, and is "due today"
    rather than overdue for the rest of that day."""
    due_day, today = invoice["due_at"].date(), now.date()
    if due_day < today:
        tone, state, detail = "danger", "is overdue", "Was due on"
    elif due_day == today:
        tone, state, detail = "warning", "is due today", "Due on"
    else:
        tone, state, detail = "warning", "is due", "Due on"
    return _item(
        invoice["id"],
        "payment",
        tone,
        f"Invoice {invoice['number']} {state}",
        f"{detail} {_day(invoice['due_at'])}.",
        "orders",
        invoice["due_at"],
    )


def _balances(ctx: TenantContext) -> list[dict[str, Any]]:
    from workeasy360.commercial.wallets import customer as wallets

    out = []
    for w in wallets.low_balances(ctx):
        out.append(
            _item(
                f"{w['kind']}:{w['currency']}",
                "low_balance",
                "danger" if w["balance"] <= 0 else "warning",
                f"{w['label']} balance is low",
                "Top it up so the services it pays for keep running."
                if w["kind"] != "ai_credits"
                else "Buy credits so AI features keep running.",
                _KIND_PAGE.get(w["kind"], "wallets"),
                w["updated_at"],
            )
        )
    return out


def _tickets(ctx: TenantContext) -> list[dict[str, Any]]:
    from workeasy360.commercial.partners import tickets

    return [
        _item(
            t["id"],
            "ticket",
            "info",
            f"Ticket #{t['number']} has a reply",
            t["subject"],
            f"help?ticket={t['id']}",
            t["replied_at"],
        )
        for t in tickets.awaiting_customer(ctx.organization_id, ctx.user_id)
    ]


def for_member(ctx: TenantContext) -> dict[str, Any]:
    """The caller's alerts, most urgent first."""
    from workeasy360.platform.notifications import preferences

    wanted = preferences.in_app_enabled(ctx)
    items: list[dict[str, Any]] = []
    if wanted["setup"]:
        items += _setup(ctx)
    if wanted["renewal"] and authz.check(ctx, "commercial.subscription.view"):
        items += _renewals(ctx)
    if authz.check(ctx, "platform.billing.view"):
        if wanted["payment"]:
            items += _payments(ctx)
        if wanted["low_balance"]:
            items += _balances(ctx)
    if wanted["ticket"]:
        items += _tickets(ctx)
    order = {"danger": 0, "warning": 1, "info": 2}
    items.sort(key=lambda i: (order.get(i["tone"], 3), i["occurred_at"] or ""))
    items = items[:MAX_ITEMS]
    return {"items": items, "count": len(items)}
