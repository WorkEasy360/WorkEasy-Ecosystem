"""A white-label partner's branded Store (ADR-053, Store Working Flow page 7).

``app.workeasy360.com/store/<partner code>`` shows the partner's brand over the
**central** catalog: only the products the partner may sell (its grants, the
products' white-label channel, its own sale and Explore switches), at the
partner's prices (its book, else list). Signing in, checkout, payment, the
subscription, the entitlement and My Products are the platform's own - the
storefront is presentation, never a second commerce engine:

- **Pricing and eligibility** are decided by ``payments.orders`` with this
  partner as the order's seller (:func:`seller_for`); the browser sends
  identifiers only.
- **Attribution.** When a storefront order is fulfilled the customer becomes
  the partner's customer (:func:`attach_customer`, a live partner link), so the
  partner's margin or commission accrues through the one ledger and the
  customer's console shows the partner's brand. A customer that already
  belongs to another partner cannot buy through this storefront.
- **Branding changes presentation, not authorization**: nothing here reads a
  Host header, and the brand read carries no partner id or capability.

The partner's custom domain keeps serving only its branded landing (D-035):
sign-in stays on the central origin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.partners import capabilities, portal, product_settings
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.pricing import selectors as price_selectors
from workeasy360.contracts.enums import PartnerLinkStatus, PartnerStatus
from workeasy360.contracts.errors import OrderNotAllowed
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.observability.metrics import metrics

#: A storefront is the white-label pair plus the right to sell.
STOREFRONT_CAPABILITIES: Final = frozenset(
    {
        capabilities.BRANDING_MANAGE,
        capabilities.DOMAIN_MANAGE,
        capabilities.SUBSCRIPTION_SELL,
    }
)


def storefront_partner(slug: Any) -> Any | None:
    """The partner whose storefront ``slug`` names, or ``None``: an active
    partner holding the white-label capabilities and the right to sell, with a
    brand set up. One answer for every refusal."""
    code = str(slug or "").strip().lower()
    if not code or len(code) > 48:
        return None
    row = partner_selectors.partner_by_code(code)
    if row is None or row.status != PartnerStatus.ACTIVE.value:
        return None
    if not STOREFRONT_CAPABILITIES <= partner_selectors.active_capabilities(row.id):
        return None
    if partner_selectors.brand_for(row.id) is None:
        return None
    return row


def _offers(partner_id: UUID, *, at: datetime) -> list[dict[str, Any]]:
    """The plan versions this partner may sell now, by product, in the
    partner's display order - its grants, the white-label channel, and its own
    "for sale" and "show in Explore" switches."""
    switches = product_settings.settings_by_product_code(partner_id)
    codes = sorted(
        {g["product_code"] for g in partner_selectors.granted_products(partner_id)}
    )
    shown = [
        c for c in codes if switches.get(c, product_settings.DEFAULTS)["show_in_explore"]
    ]
    return [
        p
        for p in catalog.sellable_plans(shown, at=at)
        if partner_selectors.may_sell(
            partner_id, product_code=p["product_code"], plan_code=p["plan_code"]
        )
    ]


def public_storefront(slug: Any) -> dict[str, Any] | None:
    """The storefront as a visitor sees it: the brand (presentation only) and
    the products with their plans and prices, or ``None`` (a 404)."""
    partner = storefront_partner(slug)
    if partner is None:
        return None
    now = timezone.now()
    brand = partner_selectors.brand_for(partner.id)
    offers = _offers(partner.id, at=now)
    prices: dict[str, dict[tuple[str, str], dict[str, int]]] = {}
    for p in price_selectors.current_prices(
        plan_version_ids=[str(o["plan_version_id"]) for o in offers]
    ):
        if not p["plan_version_id"]:
            continue
        slot = prices.setdefault(p["plan_version_id"], {}).setdefault(
            (p["currency"], p["interval"]), {}
        )
        if p["channel"] == "partner" and p["partner_id"] == str(partner.id):
            slot["partner"] = p["amount_minor"]
        elif p["channel"] == "direct":
            slot.setdefault("list", p["amount_minor"])
    store = {p["code"]: p for p in catalog.store_products()}
    codes = sorted({o["product_code"] for o in offers})
    presentation = catalog.product_presentations(codes)
    media = catalog.media_rows([store[c]["id"] for c in codes if c in store])
    products: dict[str, dict[str, Any]] = {}
    for offer in offers:
        code = offer["product_code"]
        look = presentation.get(code, {})
        product = products.setdefault(
            code,
            {
                "code": code,
                "name": offer["product_name"],
                "slug": look.get("slug", ""),
                "tagline": look.get("tagline", ""),
                "category": look.get("category", ""),
                "icon": look.get("icon", ""),
                "summary": (store.get(code) or {}).get("store_summary")
                or look.get("tagline", ""),
                "logo_url": _logo_url(
                    media.get(str((store.get(code) or {}).get("id")), []), code, store
                ),
                "plans": [],
            },
        )
        product["plans"].append(
            {
                "plan_version_id": str(offer["plan_version_id"]),
                "plan_code": offer["plan_code"],
                "plan_name": offer["plan_name"],
                "trial_days": offer["trial_days"],
                "prices": [
                    {
                        "currency": currency,
                        "interval": interval,
                        "amount_minor": slot.get("partner", slot.get("list")),
                    }
                    for (currency, interval), slot in sorted(
                        prices.get(str(offer["plan_version_id"]), {}).items()
                    )
                    if slot.get("partner", slot.get("list")) is not None
                ],
            }
        )
    return {
        "slug": partner.code,
        "brand": {
            "brand_name": brand.brand_name,
            "has_logo": brand.logo_file_id is not None,
            "accent_color": brand.accent_color,
            "theme": brand.theme,
            "support_email": brand.support_email,
            "support_url": brand.support_url,
            "portal": portal.public_view(brand.portal),
        },
        "products": list(products.values()),
    }


def _logo_url(rows: list[dict[str, Any]], code: str, store: dict[str, Any]) -> str | None:
    """A product's public logo, when the product is on the customer Store."""
    product = store.get(code)
    logo = next((m for m in rows if m["kind"] == "logo"), None)
    if product is None or logo is None:
        return None
    return catalog.media_url(product["slug"], logo["id"])


def logo_file_id(slug: Any) -> Any | None:
    partner = storefront_partner(slug)
    if partner is None:
        return None
    brand = partner_selectors.brand_for(partner.id)
    return brand.logo_file_id if brand is not None else None


def seller_for(ctx: TenantContext, slug: Any) -> Any:
    """This storefront's partner as the seller of the customer's order, or a
    refusal: no such storefront, or the customer already belongs to another
    partner (a customer is served by one partner, ADR-029)."""
    from workeasy360.commercial.payments.orders import Seller

    partner = storefront_partner(slug)
    if partner is None:
        raise OrderNotAllowed(
            "This store is not available.", reason="storefront_unavailable"
        )
    link = partner_selectors.live_link_for_organization(ctx.organization_id)
    if link is not None and link.partner_id != partner.id:
        raise OrderNotAllowed(
            "Your organization buys through another partner.",
            reason="storefront_other_partner",
        )
    partner_id = partner.id
    return Seller(
        channel="storefront",
        partner_id=partner_id,
        may_sell=lambda product_code, plan_code: partner_selectors.may_sell(
            partner_id, product_code=product_code, plan_code=plan_code
        ),
    )


def attach_customer(
    partner_id: UUID, organization_id: UUID, user_id: UUID | None
) -> None:
    """Make the customer this partner's customer as its storefront order is
    fulfilled - inside fulfilment's transaction, before the line exists, so
    the sale is attributed to the partner from its first moment. Idempotent;
    refuses (failing the order, which is then refunded) when the customer
    meanwhile became another partner's or the storefront closed."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    if user_id is None:
        raise OrderNotAllowed("The order has no buyer.", reason="storefront_unavailable")
    with transaction.atomic():
        live = (
            PartnerOrganizationLink.objects.select_for_update()
            .filter(organization_id=organization_id)
            .exclude(status=PartnerLinkStatus.ENDED.value)
            .first()
        )
        if live is not None:
            if live.partner_id != partner_id:
                raise OrderNotAllowed(
                    "This organization buys through another partner.",
                    reason="storefront_other_partner",
                )
            return
        partner = partner_selectors.partner_row(partner_id)
        if (
            partner is None
            or partner.status != PartnerStatus.ACTIVE.value
            or not STOREFRONT_CAPABILITIES
            <= partner_selectors.active_capabilities(partner_id)
        ):
            raise OrderNotAllowed(
                "This store is not available.", reason="storefront_unavailable"
            )
        link = PartnerOrganizationLink.objects.create(
            partner_id=partner_id,
            organization_id=organization_id,
            started_at=timezone.now(),
            created_by_id=user_id,
        )
        audit.record_system(
            organization_id,
            "partner.customer_linked_by_storefront",
            resource_type="partners.link",
            resource_id=link.id,
            changes={"partner_code": partner.code},
            reason="The customer bought through the partner's branded Store.",
        )
    metrics.increment("partner_storefront_customers_total")
