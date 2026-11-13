"""What a reseller reads about its customers' subscriptions and its own billing,
and the partner help and sales material (ADR-063, the specification's 6.2, 7,
5.3 and 10.2).

Reads only, each behind the permission the specification's menu needs. Every
customer here is one of the partner's own **live** customers
(``customer_names_for_partner``); another partner's customer, a direct customer
or an ended relationship is simply absent. Payment, subscription, setup and
access states stay separate fields - never one merged "status".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.controlplane import partner_wallet
from workeasy360.commercial.partners import access, promotion
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.payments import partner_fees
from workeasy360.commercial.payments import selectors as payment_selectors
from workeasy360.commercial.provisioning import selectors as provisioning
from workeasy360.commercial.subscriptions import selectors as subscription_selectors
from workeasy360.commercial.wallets import api as wallets
from workeasy360.contracts.enums import CollectionMode, RenewalPolicy, SubscriptionStatus
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import acting_scope, in_scope

CUSTOMER_VIEW: Final = "platform.partner_customer.view"
DASHBOARD_VIEW: Final = "platform.partner_dashboard.view"
PRODUCT_VIEW: Final = "platform.partner_product.view"
REFERRAL_VIEW: Final = "platform.partner_referral.view"

#: A subscription whose access ends within this window, and will not continue
#: by itself, is "expiring".
EXPIRING_WINDOW: Final = timedelta(days=30)
#: The most customers one subscriptions page reads (each in its own scope).
MAX_CUSTOMERS: Final = 300
FILTERS: Final = ("all", "active", "pending_setup", "expiring", "expired")


def _customers(partner_id: UUID) -> dict[str, str]:
    return {
        str(row["organization_id"]): row["name"]
        for row in partner_selectors.customer_names_for_partner(partner_id)
    }


def _order_refs(org_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The order each subscription line came from, by line id."""
    refs: dict[str, dict[str, Any]] = {}
    if not org_ids:
        return refs
    rows = (
        payment_selectors.order_summaries()
        .filter(organization_id__in=org_ids)
        .order_by("created_at")
        .values(
            "order_id",
            "number",
            "status",
            "paid_at",
            "subscription_item_ids",
            "partner_id",
        )[:2000]
    )
    for row in rows:
        for item_id in row["subscription_item_ids"] or []:
            refs[str(item_id)] = {
                "order_id": str(row["order_id"]),
                "number": row["number"],
                "status": row["status"],
                "paid": row["paid_at"] is not None,
            }
    return refs


def _state(sub: Any, *, now: datetime, prepaid: bool, setup_open: bool) -> dict[str, Any]:
    """The four filters' answers for one subscription - separate facts."""
    live_lines = [i for i in sub.items if i.removed_at is None or i.removed_at > now]
    expired = sub.status is SubscriptionStatus.EXPIRED or not live_lines
    continues = (
        sub.renewal_policy is RenewalPolicy.AUTO_RENEW
        and sub.cancel_at is None
        and (sub.collection_mode != CollectionMode.PARTNER_WALLET.value or prepaid)
    )
    ends_at = sub.cancel_at or sub.current_period_end
    expiring = (
        not expired
        and sub.status.is_live
        and not continues
        and ends_at - now <= EXPIRING_WINDOW
    )
    return {
        "expired": expired,
        "expiring": expiring,
        "pending_setup": setup_open and not expired,
        "active": sub.status.is_live and not expired,
        "renews": continues and not expired,
        "ends_at": ends_at.isoformat() if not continues else None,
    }


@in_scope
def subscriptions(
    ctx: TenantContext, *, status: Any = "all", q: Any = ""
) -> dict[str, Any]:
    """The subscriptions of this partner's live customers, filtered by Active,
    Pending Setup, Expiring or Expired. Each row: the customer, its products and
    plans, the period, renewal, the subscription status, who pays, the order it
    came from and that order's payment state."""
    authz.require(ctx, CUSTOMER_VIEW)
    chosen = str(status or "all")
    if chosen not in FILTERS:
        raise PartnerValidationError("Unknown filter.", field="status")
    assert ctx.partner_id is not None
    names = _customers(ctx.partner_id)
    needle = str(q or "").strip().lower()
    org_ids = [
        o for o, name in names.items() if not needle or needle in (name or "").lower()
    ]
    truncated = len(org_ids) > MAX_CUSTOMERS
    org_ids = sorted(org_ids, key=lambda o: (names[o] or "").lower())[:MAX_CUSTOMERS]
    refs = _order_refs(org_ids)
    setups = {str(o) for o in provisioning.pending_setup_organizations(org_ids)}
    now = timezone.now()
    counts = dict.fromkeys(FILTERS, 0)
    rows: list[dict[str, Any]] = []
    plan_ids: set[UUID] = set()
    for org in org_ids:
        for sub in subscription_selectors.commercial_state(UUID(org)):
            prepaid = (
                sub.collection_mode == CollectionMode.PARTNER_WALLET.value
                and wallets.entry_id_for_key(
                    partner_wallet.renewal_key(sub.id, sub.current_period_end)
                )
                is not None
            )
            state = _state(sub, now=now, prepaid=prepaid, setup_open=org in setups)
            counts["all"] += 1
            for key in ("active", "pending_setup", "expiring", "expired"):
                counts[key] += int(state[key])
            if chosen != "all" and not state[chosen]:
                continue
            lines = [i for i in sub.items if not i.is_derived]
            plan_ids.update(i.plan_version_id for i in lines if i.plan_version_id)
            rows.append(
                {
                    "subscription_id": str(sub.id),
                    "organization_id": org,
                    "customer": names.get(org),
                    "status": sub.status.value,
                    "currency": sub.currency,
                    "billing_interval": sub.billing_interval.value,
                    "current_period_start": sub.current_period_start.isoformat(),
                    "current_period_end": sub.current_period_end.isoformat(),
                    "renewal_date": sub.current_period_end.isoformat()
                    if state["renews"]
                    else None,
                    "cancel_at": sub.cancel_at.isoformat() if sub.cancel_at else None,
                    "version": sub.version,
                    "paid_by": "partner_wallet"
                    if sub.collection_mode == CollectionMode.PARTNER_WALLET.value
                    else "customer",
                    "renewal_prepaid": prepaid,
                    "state": state,
                    "lines": [
                        {
                            "subscription_item_id": str(i.id),
                            "product_code": i.product_code,
                            "plan_code": i.plan_code,
                            "plan_version_id": str(i.plan_version_id)
                            if i.plan_version_id
                            else None,
                            "quantity": i.quantity,
                            "added_at": i.added_at.isoformat(),
                            "removed_at": i.removed_at.isoformat()
                            if i.removed_at
                            else None,
                            "order": refs.get(str(i.id)),
                        }
                        for i in lines
                    ],
                }
            )
    plans = catalog.plan_versions(list(plan_ids))
    for row in rows:
        for line in row["lines"]:
            dto = (
                plans.get(UUID(line["plan_version_id"]))
                if line["plan_version_id"]
                else None
            )
            line["plan_name"] = dto.plan_name if dto else line["plan_code"]
            line["product_name"] = dto.product_name if dto else line["product_code"]
    rows.sort(key=lambda r: r["current_period_end"])
    return {"items": rows, "counts": counts, "filter": chosen, "truncated": truncated}


@in_scope
def customer_products(ctx: TenantContext, organization_id: UUID) -> dict[str, Any]:
    """Products & Setup for one assigned customer: its plans and their validity,
    how many members it has, each product's setup state (never the answers) and
    the product's website and published app links - links only, which sign
    nobody in as anybody."""
    authz.require(ctx, CUSTOMER_VIEW)
    customer = access.customer_context(ctx, organization_id, write=False)
    with acting_scope(customer):
        state = subscription_selectors.commercial_state(organization_id)
        setups = provisioning.setup_states(organization_id)
        members = _member_count(customer)
    now = timezone.now()
    lines = [
        (sub, item)
        for sub in state
        if sub.status is not SubscriptionStatus.EXPIRED
        for item in sub.items
        if not item.is_derived and (item.removed_at is None or item.removed_at > now)
    ]
    codes = {item.product_code for _sub, item in lines} | {
        s["product_code"] for s in setups
    }
    products = catalog.products_by_codes(codes)
    plans = catalog.plan_versions(
        [i.plan_version_id for _s, i in lines if i.plan_version_id]
    )
    setup_by = {s["product_code"]: s for s in setups}
    return {
        "organization_id": str(organization_id),
        "members": members,
        "products": [
            {
                "product_code": code,
                "product_name": (products.get(code) or {}).get("name", code),
                "website_url": (products.get(code) or {}).get("marketing_url") or None,
                "application_url": (products.get(code) or {}).get("application_url")
                or None,
                "app_links": (products.get(code) or {}).get("app_links") or {},
                "plans": [
                    {
                        "subscription_id": str(sub.id),
                        "subscription_item_id": str(item.id),
                        "plan_name": plans[item.plan_version_id].plan_name
                        if item.plan_version_id in plans
                        else item.plan_code,
                        "quantity": item.quantity,
                        "valid_from": item.added_at.isoformat(),
                        "valid_until": (
                            item.removed_at or sub.cancel_at or sub.current_period_end
                        ).isoformat(),
                        "subscription_status": sub.status.value,
                    }
                    for sub, item in lines
                    if item.product_code == code
                ],
                "setup": setup_by.get(code),
            }
            for code in sorted(codes)
        ],
    }


def _member_count(customer: TenantContext) -> dict[str, int] | None:
    from workeasy360.platform.organizations import selectors as organizations

    try:
        return organizations.membership_counts(customer)
    except Exception:  # pragma: no cover - a summary only; the page still renders
        return None


# --- billing records -----------------------------------------------------------------


@in_scope
def billing_records(ctx: TenantContext) -> dict[str, Any]:
    """The partner's own billing: what it paid Workeasy360 (upgrade fees and
    Platform Wallet top-ups, with their payment status) and what its wallet paid
    for customers' plans, each naming the order or subscription and customer.
    Customer orders and their invoices are the orders list beside this."""
    authz.require(ctx, DASHBOARD_VIEW)
    assert ctx.partner_id is not None
    permissions = authz.effective_permissions(ctx)
    payments = partner_fees.for_partner(ctx.partner_id, limit=50)
    charges: list[dict[str, Any]] = []
    if partner_wallet.VIEW in permissions:
        page = wallets.partner_entries(
            ctx.partner_id, entry_type=wallets.SUBSCRIPTION, limit=50
        )
        partner_wallet.describe_sources(ctx.partner_id, page["items"])
        charges = page["items"]
    return {
        "partner_payments": [
            {
                "id": p["id"],
                "purpose": p["purpose"],
                "label": "Platform Wallet top-up"
                if p["purpose"] == partner_fees.PARTNER_WALLET_TOP_UP
                else "Partner upgrade fee",
                "amount_minor": p["amount_minor"],
                "currency": p["currency"],
                "status": p["status"],
                "created_at": p["created_at"],
                "completed_at": p["completed_at"],
                "refund_status": "none",
            }
            for p in payments
        ],
        "wallet_charges": charges,
        "orders_visible": CUSTOMER_VIEW in permissions,
    }


# --- help and sales material ------------------------------------------------------------


@in_scope
def help_articles(ctx: TenantContext, *, kind: Any = None) -> dict[str, Any]:
    """Published FAQs, product guides and setup instructions for partners (and
    for everyone). Open to every partner, onboarding included."""
    from workeasy360.platform.administration import help_content

    authz.require(ctx, DASHBOARD_VIEW)
    return {"items": help_content.published(audience="partner", kind=kind)}


@in_scope
def sales_resources(ctx: TenantContext) -> dict[str, Any]:
    """Admin-approved material for the products this partner may promote or
    sell: each product's published listing images and video, its approved
    description, and the published sales guides for partners. Only products
    in the partner's grants and only published material."""
    from workeasy360.platform.administration import help_content

    authz.require(ctx, DASHBOARD_VIEW)
    permissions = authz.effective_permissions(ctx)
    if not ({PRODUCT_VIEW, REFERRAL_VIEW} & permissions):
        raise PartnerRecordNotFound("No sales resources for this partner.")
    assert ctx.partner_id is not None
    codes = set(promotion.promotable_codes(ctx.partner_id)) | {
        g["product_code"] for g in partner_selectors.granted_products(ctx.partner_id)
    }
    products = [p for p in catalog.store_products() if p["code"] in codes]
    ids = [p["id"] for p in products]
    images = catalog.media_rows(ids)
    videos = catalog.product_videos(ids)
    guides = help_content.published(audience="partner", kind="sales_guide")
    return {
        "products": [
            {
                "product_code": p["code"],
                "name": p["store_title"] or p["name"],
                "summary": p["store_summary"] or p["tagline"],
                "description": p["description"],
                "documentation_url": p["documentation_url"] or None,
                "images": [
                    {**m, "url": catalog.media_url(p["slug"], m["id"])}
                    for m in images.get(str(p["id"]), [])
                ],
                "video": videos.get(str(p["id"])),
                "guides": [g for g in guides if g["product_code"] in ("", p["code"])],
            }
            for p in products
        ],
        "guides": [g for g in guides if not g["product_code"]],
    }
