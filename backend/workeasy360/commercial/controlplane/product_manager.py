"""The Super Admin Product Manager (ADR-051): the one central product catalog.

There is no second product model and no second write path. A product is the
catalog's ``Product`` row; this module composes what other modules already own
around it, exactly as the products console does:

- **create** writes the catalog row (``catalog.create_product``), registers the
  integration that carries its launch URL and sign-in (``products.register_
  integration`` -> ``ecosystem.register_integration``) and sets its trial
  (``catalog.configure_product_trial``) - one transaction, each step through
  its owner's own authorized, audited service;
- **detail** is the console's product plus its images and publish checklist;
- **activity** is the one audit log, narrowed to this product;
- **delete** is only for a product nothing has ever used - anything else is
  archived, so history is never lost.

Publishing a product makes it *visible*; it grants nobody access. Access is an
entitlement, made only by a trial or a purchase.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.catalog.domain import code_for_slug, validate_slug
from workeasy360.commercial.controlplane import products as console
from workeasy360.contracts.enums import ProductListing, ProductSignInMode
from workeasy360.contracts.errors import CatalogValidationError, CommercialRecordNotFound
from workeasy360.contracts.values.cursor import Page
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import selectors as audit_selectors
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
from workeasy360.platform.identity import selectors as identity_selectors

VIEW = "platform.staff_catalog.view"
AUDIT_VIEW = "platform.staff_audit.view"

#: The catalog fields the Add Product wizard sends (beside the integration and
#: the trial). ``code`` is absent: it is derived from the slug, once.
_CREATE_FIELDS = frozenset(
    {
        "name",
        "slug",
        "category",
        "tagline",
        "icon",
        "description",
        "marketing_url",
        *catalog_services.LISTING_DETAIL_FIELDS,
    }
)
_INTEGRATION_FIELDS = frozenset(
    {"launch_url", "sso_enabled", "redirect_uri", "health_url"}
)
_TRIAL_FIELDS = frozenset({"enabled", "duration_days"})


def _refuse_unknown(body: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise CatalogValidationError(
            f"These {where} fields are not accepted: {', '.join(unknown)}.",
            field=unknown[0],
        )


def _integration_spec(raw: Any, *, name: str) -> dict[str, Any] | None:
    """The wizard's Integration step as a registry registration. "SSO enabled"
    is the manifest's own sign-in mode - no second switch is stored."""
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise CatalogValidationError("integration is an object.", field="integration")
    _refuse_unknown(raw, _INTEGRATION_FIELDS, "integration")
    launch_url = str(raw.get("launch_url") or "").strip()
    if not launch_url:
        return None
    sso = raw.get("sso_enabled", True)
    if not isinstance(sso, bool):
        raise CatalogValidationError("sso_enabled is true or false.", field="sso_enabled")
    redirect = str(raw.get("redirect_uri") or "").strip()
    if sso and not redirect:
        raise CatalogValidationError(
            "With single sign-on on, the product's sign-in callback URL is required.",
            field="redirect_uri",
        )
    spec: dict[str, Any] = {
        "display_name": name[:128],
        "launch_url": launch_url,
        "sign_in_mode": (
            ProductSignInMode.SSO_ONLY.value
            if sso
            else ProductSignInMode.LOCAL_ONLY.value
        ),
        "scopes": ["openid", "email", "profile", "organization"],
        "health_url": str(raw.get("health_url") or "").strip(),
    }
    if redirect:
        spec["redirect_uris"] = [redirect]
    return spec


def _trial_terms(raw: Any) -> dict[str, Any] | None:
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise CatalogValidationError("trial is an object.", field="trial")
    _refuse_unknown(raw, _TRIAL_FIELDS, "trial")
    return {
        "enabled": raw.get("enabled", False),
        "duration_days": raw.get("duration_days"),
        "seat_limit": None,
        "payment_method_required": False,
        "eligible_plan_codes": [],
    }


@in_scope
def create(ctx: TenantContext, body: dict[str, Any]) -> dict[str, Any]:
    """Add a product from the wizard: a **draft, hidden** catalog row, its
    integration (when a launch URL is given) and its trial (when set). One
    transaction: a refused step leaves nothing behind. Images are uploaded
    against the new product afterwards; publishing is a separate, checked step.
    """
    if not isinstance(body, dict):
        raise CatalogValidationError("The request body is an object.", field="body")
    product_fields = {k: v for k, v in body.items() if k not in ("integration", "trial")}
    _refuse_unknown(product_fields, _CREATE_FIELDS, "product")
    name = " ".join(str(product_fields.get("name") or "").split())
    if not name or len(name) > 128:
        raise CatalogValidationError(
            "The product name is 1 to 128 characters.", field="name"
        )
    slug = validate_slug(product_fields.get("slug"))
    code = code_for_slug(slug)
    details = {
        k: product_fields[k]
        for k in catalog_services.LISTING_DETAIL_FIELDS
        if k in product_fields
    }
    integration = _integration_spec(body.get("integration"), name=name)
    trial = _trial_terms(body.get("trial"))

    with transaction.atomic():
        product = catalog_services.create_product(
            ctx,
            code=str(code),
            name=name,
            description=str(product_fields.get("description") or ""),
            slug=slug,
            tagline=str(product_fields.get("tagline") or ""),
            marketing_url=str(product_fields.get("marketing_url") or ""),
            listing=ProductListing.HIDDEN,
            category=str(product_fields.get("category") or ""),
            icon=str(product_fields.get("icon") or ""),
            listing_details=details,
        )
        if integration is not None:
            console.register_integration(ctx, product_code=str(code), spec=integration)
        if trial is not None:
            catalog_services.configure_product_trial(ctx, product_id=product.id, **trial)
    return detail(ctx, product.id)


@in_scope
def detail(ctx: TenantContext, product_id: UUID) -> dict[str, Any]:
    """One product whole: the console's view of it, its listing images, its
    publish checklist, and whether it could be bought directly today."""
    authz.require(ctx, VIEW)
    listed = console.console(ctx)
    product = next((p for p in listed["products"] if p["id"] == str(product_id)), None)
    if product is None:
        raise CommercialRecordNotFound("No such product.", product_id=str(product_id))
    readiness = catalog_selectors.readiness_for(product_id) or []
    return {
        "product": {**product, "readiness": readiness},
        "categories": listed["categories"],
        "can_see_integrations": listed["can_see_integrations"],
        "can_see_activity": authz.check(ctx, AUDIT_VIEW).allowed
        and authz.is_platform_staff(ctx, AUDIT_VIEW),
    }


#: What each product action is called on the Activity tab. Anything not listed
#: reads as its own code with the punctuation taken out - never blank.
ACTIVITY_LABELS: dict[str, str] = {
    "commercial.product.created": "Created",
    "commercial.product.updated": "Edited",
    "commercial.product.published": "Published",
    "commercial.product.unpublished": "Unpublished",
    "commercial.product.archived": "Archived",
    "commercial.product.restored": "Restored",
    "commercial.product.status_changed": "Status changed",
    "commercial.product.trial_configured": "Trial changed",
    "commercial.product.media_added": "Image added",
    "commercial.product.media_removed": "Image removed",
    "commercial.product.media_reordered": "Screenshots reordered",
    "commercial.product.media_captioned": "Caption changed",
    "ecosystem.integration.registered": "Integration registered",
    "ecosystem.integration.manifest_updated": "Integration URLs changed",
    "ecosystem.integration.transitioned": "Integration status changed",
    "ecosystem.integration.credential_created": "Credential added",
    "ecosystem.integration.credential_revoked": "Credential revoked",
}
_URL_KEYS = frozenset(
    {"application_url", "documentation_url", "support_url", "marketing_url", "slug"}
)
_AVAILABILITY_KEYS = frozenset(
    {
        "customer_store_enabled",
        "reseller_enabled",
        "affiliate_enabled",
        "white_label_enabled",
        "listing",
    }
)


def _summary(action: str, changes: dict[str, Any]) -> str:
    """One plain line about what changed. Names fields, never values that
    could carry anything sensitive (and the audit redactor ran on write)."""
    if action == "commercial.product.updated":
        fields = [str(f) for f in changes.get("fields") or []]
        kinds = []
        if _URL_KEYS & set(fields):
            kinds.append("URLs")
        if _AVAILABILITY_KEYS & set(fields):
            kinds.append("availability")
        rest = [f for f in fields if f not in _URL_KEYS | _AVAILABILITY_KEYS]
        return ", ".join([*kinds, *(f.replace("_", " ") for f in rest)])
    if action == "commercial.product.trial_configured":
        after = changes.get("after") or {}
        if isinstance(after, dict):
            state = "on" if after.get("enabled") else "off"
            return f"Trial {state}, {after.get('duration_days')} days"
    if "after" in changes and isinstance(changes["after"], dict):
        after = changes["after"]
        return ", ".join(
            f"{k}: {v}" for k, v in sorted(after.items()) if k in ("status", "listing")
        )
    if action.startswith("commercial.product.media_"):
        return str(changes.get("kind") or "").replace("_", " ")
    if action == "ecosystem.integration.transitioned":
        return f"{changes.get('from_status')} -> {changes.get('to_status')}"
    return ""


@in_scope
def activity(ctx: TenantContext, product_id: UUID, *, cursor: str | None = None) -> Page:
    """What was done to this product and its integration, newest first, from
    the one audit log: entries about the product row, or naming its code."""
    authz.require(ctx, AUDIT_VIEW)
    facts = catalog_selectors.product_facts_by_id(product_id)
    if facts is None:
        raise CommercialRecordNotFound("No such product.", product_id=str(product_id))
    page = audit_selectors.audit_page(
        resource_ids=[str(product_id)],
        changes_match=("product_code", facts["code"]),
        limit=25,
        cursor=cursor,
    )
    actors = identity_selectors.emails_for_users(
        UUID(i["actor_user_id"]) for i in page.items if i["actor_user_id"]
    )
    items = []
    for item in page.items:
        action = str(item["action"])
        changes = item["changes"] if isinstance(item["changes"], dict) else {}
        items.append(
            {
                "id": item["id"],
                "occurred_at": item["occurred_at"],
                "action": action,
                "label": ACTIVITY_LABELS.get(action)
                or action.split(".")[-1].replace("_", " ").capitalize(),
                "summary": _summary(action, changes),
                "actor": actors.get(UUID(item["actor_user_id"]), "")
                if item["actor_user_id"]
                else "System",
            }
        )
    return Page(items=items, next_cursor=page.next_cursor, has_more=page.has_more)


@in_scope
def delete(ctx: TenantContext, product_id: UUID) -> dict[str, Any]:
    """Delete a product only if nothing has ever used it. The catalog refuses
    one that was active or has plans or features; here are the references other
    modules hold: a registered integration, and any control-plane record
    (override, partner grant, commission rule, credential...) naming its code."""
    authz.require(ctx, VIEW)
    facts = catalog_selectors.product_facts_by_id(product_id)
    if facts is None:
        raise CommercialRecordNotFound("No such product.", product_id=str(product_id))
    code = facts["code"]
    outside: list[str] = []
    if ecosystem_selectors.launch_target(code) is not None:
        outside.append("it has a registered integration")
    named = audit_selectors.audit_page(changes_match=("product_code", code), limit=100)
    if any(not str(i["action"]).startswith("commercial.product.") for i in named.items):
        outside.append("other records name it")
    deleted = catalog_services.delete_product(
        ctx, product_id=product_id, outside_references=outside
    )
    return {"deleted": True, "code": deleted}


#: The largest listing image served (the largest any rule accepts).
MEDIA_MAX_BYTES = 2 * 1024 * 1024


@in_scope
def media_content(
    ctx: TenantContext, product_id: UUID, media_id: UUID
) -> tuple[bytes, str] | None:
    """The bytes of one listing image, for the staff preview - only through the
    product that owns it, and only once the files platform has scanned it
    clean (``servable_content``'s guards). ``None`` is "no such image"."""
    from workeasy360.platform.files import selectors as file_selectors

    authz.require(ctx, VIEW)
    file_id = catalog_selectors.media_file_id(product_id, media_id)
    if file_id is None:
        return None
    return file_selectors.servable_content(file_id, max_bytes=MEDIA_MAX_BYTES)
