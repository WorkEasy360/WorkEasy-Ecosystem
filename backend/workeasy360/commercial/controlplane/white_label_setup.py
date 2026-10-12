"""The white-label setup wizard's one read: everything a step shows, in one answer.

The wizard configures an *existing* ``PartnerAccount`` - it never creates one.
This module only reads (the writes are the commands that always existed:
``update_brand``, ``upload_logo``, ``update_product_grant``,
``request_domain`` / ``verify_domain`` / ``activate_domain`` and
``enable_white_label``). It answers, for one partner:

- what the partner is today, as the *labels its capabilities earn* (Reseller,
  White-label, Affiliate - derived, never stored);
- its brand and customer-portal settings as stored;
- for every product in the catalog, the four facts the Products step shows:
  available to the partner, visible in its portal, sell allowed, trial available;
- its custom domain, and what sign-in does on it.

**Visibility never grants entitlement.** "Visible in portal" decides whether a
product is *shown* on this partner's portal. Whether a customer may *launch* a
product is the entitlement the customer holds, decided elsewhere and read nowhere
in this module.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.controlplane import partner_console as console
from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners import console_reads as partner_reads
from workeasy360.commercial.partners import domains as partner_domains
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.enums import DomainStatus, ProductStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

PARTNER_VIEW = console.PARTNER_VIEW

#: Most advanced first: the domain a wizard is "about".
_DOMAIN_RANK = {
    DomainStatus.ACTIVE.value: 0,
    DomainStatus.VERIFIED.value: 1,
    DomainStatus.VERIFICATION_PENDING.value: 2,
    DomainStatus.REQUESTED.value: 3,
}

#: The honest statement of how customers sign in, written once, here. The brand
#: is shown on a custom domain; the credential is never typed there (ADR-039).
AUTHENTICATION: dict[str, Any] = {
    "mode": "workeasy360_secure_sign_in",
    "custom_domain_sign_in": False,
    "summary": (
        "Customers sign in on Workeasy360's own secure sign-in page, which shows "
        "this brand. A custom domain shows the brand and its sign-in link hands "
        "over to that page: no password is ever typed on a partner's domain."
    ),
}


def setup(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    authz.require(ctx, PARTNER_VIEW)
    row = console.require_partner(partner_id)
    now = timezone.now()
    held = partner_selectors.active_capabilities(partner_id)
    pair = (capabilities.BRANDING_MANAGE, capabilities.DOMAIN_MANAGE)
    brand_row = partner_selectors.brand_for(partner_id)
    domain = _current_domain(partner_id)

    return {
        "partner": {
            "id": str(row.id),
            "code": row.code,
            "display_name": row.display_name,
            "status": row.status,
            "version": row.version,
        },
        "experience": capabilities.experience_labels(held),
        "capabilities": sorted(held),
        "enabled": all(key in held for key in pair),
        "missing_capabilities": [key for key in pair if key not in held],
        "customers": partner_reads.live_customer_count(partner_id),
        "brand": partner_selectors.brand_dict(brand_row)
        if brand_row is not None
        else None,
        "products": _product_rows(partner_id, held, now),
        "domain": domain,
        "branding_on_custom_domain": _branding_state(domain),
        "authentication": AUTHENTICATION,
        "checks": {
            "brand_saved": brand_row is not None,
            "logo_saved": bool(brand_row and brand_row.logo_file_id),
            "domain_requested": domain is not None,
        },
    }


def _current_domain(partner_id: UUID) -> dict[str, Any] | None:
    rows = [
        r for r in partner_selectors.domains_for(partner_id) if r.status in _DOMAIN_RANK
    ]
    if not rows:
        return None
    best = min(rows, key=lambda r: _DOMAIN_RANK[r.status])
    return partner_selectors.domain_dict(
        best, txt_name=partner_domains.verification_record_name(best.hostname)
    )


def _branding_state(domain: dict[str, Any] | None) -> str:
    """Whether the brand is being served on a custom domain right now."""
    if domain is None:
        return "none"
    return {
        DomainStatus.ACTIVE.value: "active",
        DomainStatus.VERIFIED.value: "verified_not_active",
    }.get(domain["status"], "pending")


def _product_rows(
    partner_id: UUID, held: frozenset[str], now: Any
) -> list[dict[str, Any]]:
    """One row per catalog product, so a product added to the catalog tomorrow
    appears here with no change. Retired and draft products are not offered
    unless the partner already holds a grant for one."""
    grants: dict[str, dict[str, Any]] = {}
    for grant in partner_selectors.granted_products(partner_id):
        entry = grants.setdefault(
            grant["product_code"],
            {
                "grant_id": grant["grant_id"],
                "whole_product": grant["plan_code"] is None,
                "trial_allowed": False,
                "portal_visible": False,
            },
        )
        # Several plan-level grants of one product: the product is allowed /
        # shown when any of them is.
        entry["trial_allowed"] = entry["trial_allowed"] or grant["trial_allowed"]
        entry["portal_visible"] = entry["portal_visible"] or grant["portal_visible"]
        if grant["plan_code"] is None and not entry["whole_product"]:
            entry["grant_id"], entry["whole_product"] = grant["grant_id"], True

    sells = capabilities.SUBSCRIPTION_SELL in held
    rows: list[dict[str, Any]] = []
    for product in catalog.visible_products():
        granted = grants.get(product["code"])
        offered = product["status"] == ProductStatus.AVAILABLE.value
        if not offered and granted is None:
            continue
        trial = product.get("trial")
        platform_trial = bool(trial and trial["enabled"])
        rows.append(
            {
                "product_code": product["code"],
                "product_name": product["name"],
                "status": product["status"],
                "grant_id": granted["grant_id"] if granted else None,
                "available_to_partner": granted is not None,
                "visible_in_portal": bool(granted and granted["portal_visible"]),
                "sell_allowed": granted is not None and sells,
                "platform_trial_enabled": platform_trial,
                "partner_allows_trial": bool(granted and granted["trial_allowed"]),
                "trial_available": bool(
                    granted and granted["trial_allowed"] and platform_trial
                ),
            }
        )
    return rows
