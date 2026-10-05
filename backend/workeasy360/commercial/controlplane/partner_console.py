"""The reseller and white-label consoles: authorized staff reads (Phase 10).

**Read models over data that already exists.** Nothing here writes, and nothing
here is a new source of truth: every figure is a count or a sum over a table an
earlier phase owns (partners, capability grants, links, product grants, the
commission ledger, payouts, domains, the customer summary projection). Where a
figure would need a definition the platform does not have - revenue, growth,
MRR - it is absent, not approximated (see ``dashboard`` for why).

**One decision function for "what kind of partner is this".** A partner has no
type. The labels a screen shows ("Reseller", "White-label", "Affiliate") are
computed from the partner's *effective capability grants* by
``partners.capabilities.experience_labels`` and are returned by the API, so
the console renders them and never re-derives them.

**Constant query counts.** Every list fetches its page, then aggregates for
exactly those partners with a fixed number of grouped queries - never a query per
row. ``tests/phase10`` pins the counts and measures them at 10, 100 and 500
partners.

**Composition, not access.** These functions live in ``controlplane`` (the top of
the commercial layering) because they combine what partners, commissions,
pricing and provisioning each know. They open none of those tables: each owner
answers through its own selectors (``partners.console_reads``,
``commissions.selectors``) and this module authorizes, validates the filters and
assembles the response (architecture rule F-6).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID

from django.utils import timezone

from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners import console_reads as partner_reads
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.cursor import Page, clamp_limit, encode_cursor
from workeasy360.contracts.values.identifiers import ProductCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

PARTNER_VIEW = "platform.staff_partner.view"

DEFAULT_PAGE = 25
MAX_PAGE = 100

SCOPES = ("resellers", "all")

#: Re-exported for the white-label filter validation and the detail tab.
DOMAIN_FILTERS = partner_reads.DOMAIN_FILTERS
AWAITING_VERIFICATION = partner_reads.AWAITING_VERIFICATION


# --- shared building blocks -------------------------------------------------


def _limit(raw: Any) -> int:
    return clamp_limit(raw, default=DEFAULT_PAGE, maximum=MAX_PAGE)


def _clean_status(value: str | None) -> str | None:
    if not value:
        return None
    if value not in {s.value for s in PartnerStatus}:
        raise PartnerValidationError("Unknown status filter.", field="status")
    return value


def _clean_scope(value: str | None) -> str:
    scope = value or "resellers"
    if scope not in SCOPES:
        raise PartnerValidationError("Unknown scope filter.", field="scope")
    return scope


def _clean_capability(value: str | None) -> str | None:
    if not value:
        return None
    return capabilities.require_capability(value).key


def _clean_product(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ProductCode(value))
    except ValueError as exc:
        raise PartnerValidationError("Unknown product filter.", field="product") from exc


def _clean_query(value: str | None) -> str | None:
    text = (value or "").strip()[:80]
    return text or None


# --- overview -----------------------------------------------------------------


def overview(ctx: TenantContext) -> dict[str, Any]:
    """Factual counts, each answering one question a staff member asks first.

    Deliberately absent: MRR, ARR, growth and revenue. A partner's sales are
    priced in several currencies and intervals with no conversion in this
    platform, so any single figure would be invented. Money appears only where
    it is well defined - a per-currency sum of ledger rows or payouts.
    """
    authz.require(ctx, PARTNER_VIEW)
    now = timezone.now()
    counts = partner_reads.overview_counts(
        now,
        reseller_keys=capabilities.experience_keys(capabilities.RESELLER),
        white_label_keys=capabilities.experience_keys(capabilities.WHITE_LABEL),
    )
    pending_commission = commission_selectors.unpaid_by_currency()
    pending_payouts = commission_selectors.open_payouts_by_currency()

    return {
        "as_of": now.isoformat(),
        "cards": [
            {
                "key": "active_partners",
                "label": "Active partners",
                "count": counts["active_partners"],
            },
            {
                "key": "active_resellers",
                "label": "Active resellers",
                "count": counts["active_resellers"],
            },
            {
                "key": "white_label_enabled",
                "label": "White-label enabled",
                "count": counts["white_label_enabled"],
            },
            {
                "key": "partner_customers",
                "label": "Partner customers",
                "count": counts["partner_customers"],
            },
            {
                "key": "active_partner_subscriptions",
                "label": "Active partner subscriptions",
                "count": counts["active_partner_subscriptions"],
            },
            {
                "key": "pending_commissions",
                "label": "Pending commissions",
                "count": sum(a["entries"] for a in pending_commission),
                "amounts": pending_commission,
            },
            {
                "key": "pending_payouts",
                "label": "Pending payouts",
                "count": sum(a["entries"] for a in pending_payouts),
                "amounts": pending_payouts,
            },
            {
                "key": "domains_awaiting_verification",
                "label": "Domains awaiting verification",
                "count": counts["domains_awaiting_verification"],
            },
        ],
    }


# --- the reseller list ------------------------------------------------------------


def resellers_page(
    ctx: TenantContext,
    *,
    query: str | None = None,
    status: str | None = None,
    product: str | None = None,
    capability: str | None = None,
    scope: str | None = None,
    limit: Any = DEFAULT_PAGE,
    cursor: str | None = None,
) -> Page:
    """One page of partners with the aggregates the reseller list shows.

    ``scope=resellers`` (the default) lists partners that wear the Reseller
    label; ``scope=all`` lists every partner, so an account created but not yet
    given capabilities is never invisible. Both are the same query - the label
    is a filter over grants, not a table.
    """
    authz.require(ctx, PARTNER_VIEW)
    page_size = _limit(limit)
    state = _clean_status(status)
    which = _clean_scope(scope)
    needle = _clean_query(query)
    wanted_capability = _clean_capability(capability)
    wanted_product = _clean_product(product)
    now = timezone.now()

    rows, has_more = partner_reads.partner_rows(
        now,
        experience_keys=(
            capabilities.experience_keys(capabilities.RESELLER)
            if which == "resellers"
            else None
        ),
        query=needle,
        status=state,
        product=wanted_product,
        capability=wanted_capability,
        cursor=cursor,
        page_size=page_size,
    )
    facts = _aggregates([r.id for r in rows], now)

    items = []
    for row in rows:
        held = facts["capabilities"].get(row.id, set())
        latest = [
            t
            for t in (
                row.updated_at,
                facts["ledger_at"].get(row.id),
                facts["link_at"].get(row.id),
            )
            if t is not None
        ]
        items.append(
            {
                "id": str(row.id),
                "code": row.code,
                "display_name": row.display_name,
                "status": row.status,
                "payout_currency": row.payout_currency,
                "created_at": row.created_at.isoformat(),
                "experience": capabilities.experience_labels(held),
                "capability_count": len(held),
                "customers": facts["customers"].get(row.id, 0),
                "products": facts["products"].get(row.id, []),
                "active_subscriptions": facts["subscriptions"].get(row.id, 0),
                "commission": facts["commission"].get(row.id, []),
                "payouts": {
                    "open": facts["open_payouts"].get(row.id, 0),
                    "latest": facts["latest_payout"].get(row.id),
                },
                "last_activity_at": max(latest).isoformat(),
            }
        )
    return Page(
        items=items,
        next_cursor=encode_cursor(rows[-1].created_at, rows[-1].id) if has_more else None,
        has_more=has_more,
    )


def _aggregates(partner_ids: list[UUID], now: datetime) -> dict[str, Any]:
    """Every per-partner figure of a list page, in a fixed number of queries.

    Five partner-owned reads (capabilities, live customers, subscriptions,
    product grants, relationship stamps) and four commission-owned ones (ledger
    totals, unpaid ledger totals, open payouts, latest payout) - the same nine
    for one partner or a hundred.
    """
    facts = partner_reads.page_facts(partner_ids, now)
    out: dict[str, Any] = {
        **facts,
        "commission": defaultdict(list),
        "open_payouts": {},
        "latest_payout": {},
        "ledger_at": {},
    }
    if not partner_ids:
        return out
    for partner_id, ledger in commission_selectors.ledger_facts(partner_ids).items():
        out["commission"][partner_id] = ledger["commission"]
        if ledger["last_at"] is not None:
            out["ledger_at"][partner_id] = ledger["last_at"]
    for partner_id, payouts in commission_selectors.payout_facts(partner_ids).items():
        out["open_payouts"][partner_id] = payouts["open"]
        out["latest_payout"][partner_id] = payouts["latest"]
    return out


# --- the catalog the console sells from -------------------------------------------


def console_products(ctx: TenantContext) -> dict[str, Any]:
    """Every product with its live, published plans - what a staff member picks
    from when allowing a partner to sell (a filter, the wizard's product step,
    the pricing review). Two queries. Draft, retired and unpublished plan
    versions are absent, so nothing can be offered that ``purchase_plan``
    would refuse."""
    from workeasy360.commercial.catalog import selectors as catalog

    authz.require(ctx, PARTNER_VIEW)
    products = catalog.visible_products()
    plans_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for plan in catalog.sellable_plans([p["code"] for p in products], at=timezone.now()):
        plans_by_product[plan["product_code"]].append(
            {
                "plan_version_id": str(plan["plan_version_id"]),
                "plan_code": plan["plan_code"],
                "plan_name": plan["plan_name"],
                "included_seats": plan["included_seats"],
                "trial_days": plan["trial_days"],
            }
        )
    return {
        "results": [
            {
                "code": p["code"],
                "name": p["name"],
                "status": p["status"],
                "plans": plans_by_product.get(p["code"], []),
            }
            for p in products
        ]
    }


# --- the white-label list ------------------------------------------------------------


def white_label_page(
    ctx: TenantContext,
    *,
    query: str | None = None,
    status: str | None = None,
    domain_status: str | None = None,
    brand: str | None = None,
    limit: Any = DEFAULT_PAGE,
    cursor: str | None = None,
) -> Page:
    """Partners that wear the White-label label, with their brand and domain.

    This is a **filtered view of ``PartnerAccount``**, not a table: a partner
    appears while it holds an effective ``branding.manage`` or ``domain.manage``
    capability, and stops appearing when both are revoked. Four queries beyond
    the page whatever its size: capabilities, brands, domains and live customers.
    """
    authz.require(ctx, PARTNER_VIEW)
    page_size = _limit(limit)
    state = _clean_status(status)
    needle = _clean_query(query)
    if domain_status and domain_status not in DOMAIN_FILTERS:
        raise PartnerValidationError("Unknown domain filter.", field="domain_status")
    if brand and brand not in ("configured", "none"):
        raise PartnerValidationError("Unknown brand filter.", field="brand")

    found = partner_reads.white_label_rows(
        timezone.now(),
        white_label_keys=capabilities.experience_keys(capabilities.WHITE_LABEL),
        query=needle,
        status=state,
        domain_status=domain_status or None,
        brand=brand or None,
        cursor=cursor,
        page_size=page_size,
    )
    rows = found["rows"]
    items = []
    for row in rows:
        brand_row = found["brands"].get(row.id)
        domain_row = found["domains"].get(row.id)
        stamps = [
            t
            for t in (
                brand_row["updated_at"] if brand_row else None,
                domain_row["updated_at"] if domain_row else None,
            )
            if t is not None
        ]
        items.append(
            {
                "id": str(row.id),
                "code": row.code,
                "display_name": row.display_name,
                "status": row.status,
                "experience": capabilities.experience_labels(
                    found["capabilities"].get(row.id, set())
                ),
                "brand": {
                    "configured": brand_row is not None,
                    "name": brand_row["name"] if brand_row else "",
                    "has_logo": bool(brand_row and brand_row["has_logo"]),
                },
                "domain": (
                    {"hostname": domain_row["hostname"], "status": domain_row["status"]}
                    if domain_row
                    else None
                ),
                "customers": found["customers"].get(row.id, 0),
                "updated_at": (max(stamps) if stamps else row.updated_at).isoformat(),
            }
        )
    return Page(
        items=items,
        next_cursor=(
            encode_cursor(rows[-1].created_at, rows[-1].id) if found["has_more"] else None
        ),
        has_more=found["has_more"],
    )


# --- the one place a price row's figures are computed -----------------------------


def price_figures(
    *,
    list_minor: int | None,
    partner_minor: int | None,
    rate_bp: int | None,
    currency: Any,
) -> dict[str, int | None]:
    """The discount, commission and platform net of one price, from integers.

    Integer arithmetic only (basis points, half-up through
    ``domain.commission_amount``), so a figure is the same wherever it is
    shown - the Add Reseller review, a partner's pricing tab, and the ledger
    entry a sale really produces. This is presentation of a *sale's* economics;
    the sale itself is priced by ``resolve_price`` and accrued by
    ``commissions.accrual``, and neither reads this.
    """
    from workeasy360.commercial.commissions import domain as commission_domain

    discount = discount_bp = commission = net = None
    if list_minor is not None and partner_minor is not None:
        discount = list_minor - partner_minor
        discount_bp = (discount * 10_000) // list_minor if list_minor else 0
    if partner_minor is not None and rate_bp is not None:
        commission = commission_domain.commission_amount(
            basis_minor=partner_minor, currency=currency, rate_bp=rate_bp
        )
        net = partner_minor - commission
    return {
        "discount_minor": discount,
        "discount_bp": discount_bp,
        "commission_bp": rate_bp,
        "commission_minor": commission,
        "platform_net_minor": net,
    }


# --- shared with the detail and the wizard -------------------------------------


def require_partner(partner_id: UUID) -> Any:
    from workeasy360.commercial.partners import selectors

    row = selectors.partner_row(partner_id)
    if row is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    return row
