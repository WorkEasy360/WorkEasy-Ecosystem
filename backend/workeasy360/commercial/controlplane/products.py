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
from workeasy360.contracts.values.manifest import MANIFEST_VERSION, from_stored_manifest
from workeasy360.contracts.values.sign_in import ProductDoors
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
from workeasy360.platform.ecosystem import services as ecosystem_services

VIEW = "platform.staff_catalog.view"
INTEGRATION_VIEW = "platform.integration.view"

#: The manifest fields the console may change. ``product_code`` is absent (an
#: integration cannot change which product it is) and so is everything about
#: credentials - a credential is added or revoked, never edited, and a secret
#: is shown once at creation and never again.
#:
#: ADR-044 adds the Authentication section's fields: how the product lets
#: people in, its own sign-in page, where it may be returned after a
#: Workeasy360 sign-out, and the scopes it may ask for.
EDITABLE_INTEGRATION_FIELDS = (
    "display_name",
    "launch_url",
    "health_url",
    "redirect_uris",
    "contact_email",
    "sign_in_mode",
    "login_url",
    "post_logout_redirect_uris",
    "scopes",
)
#: Optional in the manifest: an empty value removes the key.
_OPTIONAL = frozenset({"health_url", "contact_email", "login_url"})

#: The fields a new integration may be registered with from the console.
REGISTRATION_FIELDS = (*EDITABLE_INTEGRATION_FIELDS, "token_endpoint_auth_method")


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
    parsed = from_stored_manifest(manifest)
    doors = ProductDoors(
        mode=parsed.sign_in_mode,
        sso_ready=dto.status == "active" and dto.sso == "connected",
        has_product_login=bool(parsed.product_login_url),
    )
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
        # ADR-044: the Authentication section. Never a secret: a credential is
        # listed by its public reference only (see ``credentials``).
        "manifest_version": parsed.manifest_version,
        "sign_in_mode": parsed.sign_in_mode.value,
        "login_url": parsed.login_url,
        "post_logout_redirect_uris": list(parsed.post_logout_redirect_uris),
        "scopes": list(parsed.scopes),
        "token_endpoint_auth_method": parsed.token_endpoint_auth_method,
        "sso_state": doors.sso_state,
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
    # Every console edit writes the current manifest version: each version
    # is a superset of the one before (fields are only ever added), and the
    # whole manifest is re-validated under today's rules by update_manifest.
    manifest["manifest_version"] = MANIFEST_VERSION
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


@in_scope
def register_integration(
    ctx: TenantContext, *, product_code: str, spec: dict[str, Any]
) -> dict[str, Any]:
    """Register the integration of a catalog product that has none - the
    console's "Add product -> Integration" step (ADR-044). Starts in
    ``draft`` with no credential, through ``ecosystem.register_integration``:
    staff-only, second factor, audited, the manifest validated exactly as
    the registry API validates it. Nothing here names a product."""
    unknown = sorted(set(spec) - set(REGISTRATION_FIELDS))
    if unknown:
        raise ManifestRejected(
            f"These integration fields cannot be set here: {', '.join(unknown)}.",
            field=unknown[0],
        )
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "product_code": str(product_code),
        "scopes": ["openid"],
        "capabilities": [],
        "token_endpoint_auth_method": "private_key_jwt",
    }
    for key, value in spec.items():
        if key in _OPTIONAL and value in ("", None):
            continue
        manifest[key] = value
    ecosystem_services.register_integration(ctx, manifest=manifest)
    refreshed = ecosystem_selectors.integration_for_product(
        ctx, product_code=product_code
    )
    assert refreshed is not None
    return _integration_json(refreshed)


@in_scope
def credentials(ctx: TenantContext, *, product_code: str) -> dict[str, Any]:
    """A product's credentials by public reference: kind, status, expiry,
    last use. A secret is never stored, so it can never be listed."""
    current = ecosystem_selectors.integration_for_product(ctx, product_code=product_code)
    if current is None:
        raise ManifestRejected(
            "That product has no registered integration.", field="product_code"
        )
    return {
        "credentials": [
            c.as_dict()
            for c in ecosystem_selectors.credentials(ctx, client_id=current.client_id)
        ]
    }


def public_key_algorithm(public_key_pem: str) -> str:
    """RS256 for an RSA key, ES256 for a P-256 key - the two the platform
    verifies. Derived from the key, so staff cannot pick a mismatch."""
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        key = load_pem_public_key(public_key_pem.strip().encode())
    except (ValueError, TypeError):
        raise ManifestRejected(
            "That is not a PEM public key.", field="public_key_pem"
        ) from None
    if isinstance(key, rsa.RSAPublicKey):
        return "RS256"
    if isinstance(key, ec.EllipticCurvePublicKey) and key.curve.name == "secp256r1":
        return "ES256"
    raise ManifestRejected(
        "The public key must be RSA or EC P-256.", field="public_key_pem"
    )


@in_scope
def add_credential(
    ctx: TenantContext,
    *,
    product_code: str,
    kind: str,
    public_key_pem: str = "",
    label: str = "",
) -> dict[str, Any]:
    """Add a credential: the product's public key (``public_key``, the
    recommended ``private_key_jwt``), or a generated client secret
    (``client_secret``) returned **in this response only** - nothing stores
    it and no read path can produce it again. Overlap is allowed (rotation)."""
    current = ecosystem_selectors.integration_for_product(ctx, product_code=product_code)
    if current is None:
        raise ManifestRejected(
            "That product has no registered integration.", field="product_code"
        )
    algorithm = public_key_algorithm(public_key_pem) if kind == "public_key" else ""
    issued = ecosystem_services.create_credential(
        ctx,
        client_id=current.client_id,
        kind=kind,
        public_key_pem=public_key_pem,
        algorithm=algorithm,
        label=label[:100],
    )
    body: dict[str, Any] = {
        "credential_id": issued.credential_id,
        "kind": issued.kind,
        "expires_at": issued.expires_at.isoformat(),
    }
    if issued.secret:
        body["client_secret"] = issued.secret
        body["notice"] = (
            "This secret is shown once and cannot be shown again. Store it in "
            "the product's secret store now; if it is lost, add a new "
            "credential and revoke this one."
        )
    return body


@in_scope
def revoke_credential(
    ctx: TenantContext, *, product_code: str, credential_id: str, reason: str
) -> dict[str, Any]:
    """Revoke a credential, effective on the product's next token exchange."""
    current = ecosystem_selectors.integration_for_product(ctx, product_code=product_code)
    if current is None:
        raise ManifestRejected(
            "That product has no registered integration.", field="product_code"
        )
    credential = ecosystem_services.revoke_credential(
        ctx,
        client_id=current.client_id,
        credential_id=credential_id,
        reason=reason,
    )
    return {"credential_id": credential.credential_id, "status": credential.status}
