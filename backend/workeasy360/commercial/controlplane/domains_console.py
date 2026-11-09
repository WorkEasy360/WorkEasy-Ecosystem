"""Settings > Domains & Apps and shared login clients (2026-11-09).

What the platform already knows, in one place, read-only here: its own portal addresses
(from configuration), each product's addresses and app links, partner domains, partner
brands, how each product may be bought and where. A partner's domain is verified and a
product's links are edited on their own pages.

**DNS and SSL status is a live check, not a stored claim.** There is no Cloudflare account
connected, so this asks the address itself: does it resolve, and does it answer over TLS
with a certificate that validates. It goes through the one guarded outbound client, and
only to addresses already in this list, never one typed in.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.partners import domains_overview
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.health.probe import probe_url
from workeasy360.platform.authz import services as authz
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors

VIEW = "platform.staff_settings.view"
INTEGRATION_VIEW = "platform.staff_integration.view"


def _portals() -> list[dict[str, str]]:
    labels = {
        "admin": "Admin Portal",
        "partner": "Partner Portal",
        "app": "Customer Portal",
        "auth": "Central sign-in",
        "api": "API",
    }
    origins = getattr(settings, "W360_PORTAL_ORIGINS", {}) or {}
    return [
        {"key": key, "label": labels.get(key, key), "origin": origin}
        for key, origin in origins.items()
        if origin
    ]


def _app_row(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": product.get("code"),
        "name": product.get("name"),
        "status": product.get("status"),
        "application_url": product.get("application_url") or "",
        "marketing_url": product.get("marketing_url") or "",
        "documentation_url": product.get("documentation_url") or "",
        "support_url": product.get("support_url") or "",
        "app_links": product.get("app_links") or {},
        "store_link": f"/store/product/{product.get('slug')}" if product.get("slug") else "",
        "purchase_routes": {
            "store": bool(product.get("customer_store_enabled")),
            "reseller": bool(product.get("reseller_enabled")),
            "affiliate": bool(product.get("affiliate_enabled")),
            "white_label": bool(product.get("white_label_enabled")),
        },
    }


def overview(ctx: TenantContext) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    return {
        "portals": _portals(),
        "apps": [_app_row(p) for p in catalog_selectors.visible_products()],
        "partner_domains": domains_overview.partner_domains(),
        "brands": domains_overview.partner_brands(),
        "dns_provider_connected": False,
    }


def _known_hosts() -> set[str]:
    from urllib.parse import urlsplit

    hosts = {urlsplit(p["origin"]).hostname or "" for p in _portals()}
    for product in catalog_selectors.visible_products():
        for field in ("application_url", "marketing_url"):
            value = product.get(field)
            if value:
                hosts.add(urlsplit(value).hostname or "")
    for row in domains_overview.partner_domains():
        hosts.add(row["hostname"])
    return {host for host in hosts if host}


def check_host(ctx: TenantContext, raw: Any) -> dict[str, Any]:
    """Whether a listed address resolves and answers over HTTPS right now."""
    authz.require(ctx, VIEW)
    host = str(raw or "").strip().lower()
    if host not in _known_hosts():
        raise CatalogValidationError("That address is not one of the platform's.", field="host")
    result = probe_url("settings-domains", f"https://{host}/")
    answered = result.detail.startswith("http_")
    return {
        "host": host,
        "ok": result.status.value == "healthy",
        "detail": (
            f"Resolves, and answers over HTTPS with a valid certificate ({result.detail})."
            if answered
            else f"Did not answer over HTTPS ({result.detail})."
        ),
    }


def login_clients(ctx: TenantContext) -> list[dict[str, Any]]:
    """Shared login clients and the addresses each may return to."""
    if not authz.check(ctx, INTEGRATION_VIEW).allowed:
        authz.require(ctx, VIEW)
    return ecosystem_selectors.login_clients()
