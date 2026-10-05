"""The Super Admin products console (Phase 11): read and integration edits.

**One place to see each product whole.** A product is several things that live
in different modules, and staff should not have to visit four screens to answer
"is Easy CRM ready to sell?". This composes what each owner already knows:

- **commercial** - the catalog row, its plans, whether they are public and
  priced (``catalog`` and ``pricing`` selectors, through the plan builder's own
  overview);
- **technical** - the product's integration: address, status, whether single
  sign-on can complete, what the last health check saw (``platform.ecosystem``);
- **channels** - derived from constructs that already exist, *not* new flags:
  direct sale is "listed, available, with a public plan that has a price";
  reseller availability is how many active partners hold a product grant;
  affiliate is whether a commission rule covers the product; the trial is the
  product's trial configuration.

Commercial status and technical status are deliberately separate fields
(Phase 11, brief step 18): an integration being degraded never changes whether
a product is for sale, and neither feeds Workeasy360's own readiness.

Nothing here holds a credential or returns one. Editing an integration goes
through ``ecosystem.update_manifest`` - the same audited, staff-only,
second-factor-gated write the registry API uses - never around it.
"""

from __future__ import annotations

from typing import Any

from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane import catalog as plan_builder
from workeasy360.commercial.partners import console_reads as partner_reads
from workeasy360.contracts.errors import ManifestRejected
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
from workeasy360.platform.ecosystem import services as ecosystem_services

VIEW = "platform.staff_catalog.view"
INTEGRATION_VIEW = "platform.integration.view"

#: The manifest fields the console may change. ``product_code`` is absent (an
#: integration cannot change which product it is) and so is everything about
#: credentials - those are never edited in a screen.
EDITABLE_INTEGRATION_FIELDS = (
    "display_name",
    "launch_url",
    "health_url",
    "redirect_uris",
    "contact_email",
)
#: Optional in the manifest: an empty value removes the key.
_OPTIONAL = frozenset({"health_url", "contact_email"})


def _direct_readiness(
    product: dict[str, Any], plans: list[dict[str, Any]]
) -> dict[str, Any]:
    """Whether a visitor could buy this product directly right now: the same
    test the storefront applies (available, listed, and a public, available
    standard plan with a price)."""
    live = [
        p
        for p in plans
        if p["kind"] == "standard" and p["status"] == "available" and p["is_public"]
    ]
    priced = [p for p in live if any(v.get("prices") for v in p["versions"][:1])]
    listed = product["listing"] == "listed"
    available = product["status"] == "available"
    return {
        "listed": listed,
        "available": available,
        "public_plans": len(live),
        "priced_plans": len(priced),
        "ready": listed and available and bool(priced),
    }


def _integration_json(dto: Any) -> dict[str, Any]:
    manifest = dto.manifest or {}
    return {
        "client_id": dto.client_id,
        "status": dto.status,
        "display_name": manifest.get("display_name", ""),
        "launch_url": manifest.get("launch_url", ""),
        "health_url": manifest.get("health_url", ""),
        "redirect_uris": list(manifest.get("redirect_uris", [])),
        "contact_email": manifest.get("contact_email", ""),
        "suspension_reason": dto.suspension_reason,
        "sso": dto.sso,
        "health": {
            "status": dto.health_status,
            "checked_at": dto.health_checked_at.isoformat()
            if dto.health_checked_at
            else None,
            "detail": dto.health_detail,
        },
    }


@in_scope
def console(ctx: TenantContext) -> dict[str, Any]:
    """Every product, whole. Needs the catalog view grant; the integration
    section is included only for staff who also hold the integration view."""
    authz.require(ctx, VIEW)
    built = plan_builder.overview(ctx)
    plans_by_product: dict[str, list[dict[str, Any]]] = {}
    for plan in built["plans"]:
        plans_by_product.setdefault(plan["product"]["code"], []).append(plan)

    grants = partner_reads.product_grant_counts()
    rules = commission_selectors.rules_list(live_only=True)
    default_rules = sum(1 for r in rules if not r["product_code"] and not r["partner_id"])

    may_see_integrations = authz.check(
        ctx, INTEGRATION_VIEW
    ).allowed and authz.is_platform_staff(ctx, INTEGRATION_VIEW)
    integrations = (
        {i.product_code: i for i in ecosystem_selectors.integrations(ctx, limit=100)}
        if may_see_integrations
        else {}
    )

    products = []
    for product in built["products"]:
        code = product["code"]
        plans = plans_by_product.get(code, [])
        link = integrations.get(code)
        products.append(
            {
                **{
                    k: product[k]
                    for k in (
                        "id",
                        "code",
                        "name",
                        "slug",
                        "tagline",
                        "description",
                        "category",
                        "icon",
                        "status",
                        "listing",
                        "availability",
                        "licensing_mode",
                        "sort_order",
                        "marketing_url",
                    )
                },
                "id": str(product["id"]),
                "plans": [
                    {
                        "code": p["code"],
                        "name": p["name"],
                        "status": p["status"],
                        "is_public": p["is_public"],
                        "kind": p["kind"],
                        "billing_type": p["billing_type"],
                        "priced": any(v.get("prices") for v in p["versions"][:1]),
                    }
                    for p in plans
                ],
                "channels": {
                    "direct": _direct_readiness(product, plans),
                    "reseller": {"partners": grants.get(code, 0)},
                    "affiliate": {
                        "product_rules": sum(
                            1 for r in rules if r["product_code"] == code
                        ),
                        "programme_default": default_rules > 0,
                    },
                    "trial": product["trial"],
                },
                "integration": _integration_json(link) if link is not None else None,
            }
        )
    return {"products": products, "can_see_integrations": may_see_integrations}


@in_scope
def update_integration(
    ctx: TenantContext, *, product_code: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """Change a product's launch address and related public settings.

    Merges ``changes`` into the *stored* manifest and hands the whole to
    ``ecosystem.update_manifest``, which re-validates it (https only, no
    internal or metadata address, exact redirect URIs), requires the
    second factor and audits the old and new redirect URIs. Unknown fields are
    refused rather than ignored, so a typo cannot look like success.
    """
    unknown = sorted(set(changes) - set(EDITABLE_INTEGRATION_FIELDS))
    if unknown:
        raise ManifestRejected(
            f"These integration fields cannot be changed here: {', '.join(unknown)}.",
            field=unknown[0],
        )
    current = ecosystem_selectors.integration_for_product(ctx, product_code=product_code)
    if current is None:
        raise ManifestRejected(
            "That product has no registered integration.", field="product_code"
        )
    manifest = dict(current.manifest)
    for key, value in changes.items():
        if key in _OPTIONAL and value in ("", None):
            manifest.pop(key, None)
        else:
            manifest[key] = value
    ecosystem_services.update_manifest(
        ctx, client_id=current.client_id, manifest=manifest
    )
    refreshed = ecosystem_selectors.integration_for_product(
        ctx, product_code=product_code
    )
    assert refreshed is not None
    return _integration_json(refreshed)


@in_scope
def set_integration_status(
    ctx: TenantContext, *, product_code: str, status: str, reason: str
) -> dict[str, Any]:
    """Activate, suspend or retire a product's integration - the technical
    switch, separate from whether the product is for sale. The service refuses
    an illegal move, requires a reason to suspend and the second factor."""
    current = ecosystem_selectors.integration_for_product(ctx, product_code=product_code)
    if current is None:
        raise ManifestRejected(
            "That product has no registered integration.", field="product_code"
        )
    ecosystem_services.transition(
        ctx, client_id=current.client_id, to_status=status, reason=reason
    )
    refreshed = ecosystem_selectors.integration_for_product(
        ctx, product_code=product_code
    )
    assert refreshed is not None
    return _integration_json(refreshed)
