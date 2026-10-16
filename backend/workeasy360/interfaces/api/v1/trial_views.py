"""Product trials over HTTP (Phase 11): ``/api/v1/orgs/<slug>/trials``.

Session + tenancy middleware authenticate; ``commercial.subscriptions.trials``
authorizes and decides. The request carries **only** a product and a plan
code: the trial's length, seats and end come from the product's configuration
(never from the body), so there is nothing here for a client to forge.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.subscriptions import selectors as subscription_selectors
from workeasy360.commercial.subscriptions import trials
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values import json_input
from workeasy360.interfaces.api.v1.control_plane_http import limited

_FIELDS = ("product_code", "plan_code")


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json_input.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CatalogValidationError("Malformed JSON body.", field="body") from exc
    if not isinstance(payload, dict):
        raise CatalogValidationError("The body must be a JSON object.", field="body")
    return payload


def trial_json(trial: subscription_selectors.ProductTrialDTO) -> dict[str, Any]:
    """A trial as the organization sees it: what it is, until when, and whether
    it still grants - never a plan-version id or another tenant's anything."""
    return {
        "id": str(trial.id),
        "product_code": trial.product_code,
        "plan_code": trial.plan_code,
        "status": trial.status.value,
        "seats": trial.seats,
        "duration_days": trial.duration_days,
        "started_at": trial.started_at.isoformat(),
        "ends_at": trial.ends_at.isoformat(),
        "ended_at": trial.ended_at.isoformat() if trial.ended_at else None,
    }


@require_http_methods(["GET", "POST"])
def trials_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """GET: the organization's trials, newest first. POST: start one
    (``Idempotency-Key`` required)."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        return JsonResponse({"results": [trial_json(t) for t in trials.list_trials(ctx)]})
    blocked = limited(request, "order_place", user=getattr(request, "user", None))
    if blocked is not None:
        return blocked
    body = _body(request)
    _refuse_if_partner_turned_trial_off(ctx, body.get("product_code"))
    started = trials.start_product_trial(
        ctx,
        idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
        **{key: body.get(key) for key in _FIELDS},
    )
    return JsonResponse(trial_json(started), status=201)


def _refuse_if_partner_turned_trial_off(ctx: Any, product_code: Any) -> None:
    """A partner may decide its customers do not get a product's platform
    trial (``PartnerProductGrant.trial_allowed``). It only ever narrows what the
    platform offers: it adds no trial, and a direct customer or a product the
    partner does not sell is never affected. The caller must already be allowed
    to start a trial, so this refusal reveals nothing to someone who is not."""
    from workeasy360.commercial.partners.selectors import trial_blocked_for_organization
    from workeasy360.contracts.errors import TrialNotAllowed
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, trials.MANAGE)
    code = str(product_code or "")
    if code and trial_blocked_for_organization(ctx.organization_id, code):
        raise TrialNotAllowed(reason="not_offered", product_code=code)


@require_http_methods(["POST"])
def trial_cancel_view(
    request: HttpRequest, org_slug: str, trial_id: UUID
) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    return JsonResponse(trial_json(trials.cancel_product_trial(ctx, trial_id=trial_id)))


@require_http_methods(["GET"])
def explore_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """The products this organization can discover, priced in its own currency,
    each with what it may do about it (buy, start a trial, nothing)."""
    from workeasy360.commercial.partners import selectors as partner_selectors
    from workeasy360.commercial.subscriptions.marketplace import explore

    ctx = request.tenant_context  # type: ignore[attr-defined]
    listing = explore(ctx)
    # What this customer's *partner* chose to show and to trial. Presentation
    # and a narrowing of the offer only: nothing here reads or changes an
    # entitlement, and a product the customer already holds is never hidden.
    choices = partner_selectors.product_choices_for_organization(ctx.organization_id)
    if choices:  # a direct customer pays for nothing here: one query, then done
        hidden = partner_selectors.hidden_product_codes_for_organization(
            ctx.organization_id
        )
        shown = []
        for product in listing["products"]:
            if product["code"] in hidden and product.get("holding") is None:
                continue
            entry = choices.get(product["code"])
            if entry is not None and not entry["trial_allowed"]:
                product["trial"] = {
                    **product["trial"],
                    "available": False,
                    "plan_codes": [],
                }
                if product["trial_state"] == "available":
                    product["trial_state"] = "unavailable"
                for plan in product["plans"]:
                    plan["trial_available"] = False
            shown.append(product)
        listing["products"] = shown
    _apply_portal_presentation(ctx, listing)
    return JsonResponse(listing)


def _apply_portal_presentation(ctx: Any, listing: dict[str, Any]) -> None:
    """The white-label partner's own order and visibility for Explore (ADR-045).

    Presentation only. A product the customer already holds is never hidden, and
    a product the partner turned off for new sales is not offered to discover.
    Nothing is added, no price is read and no entitlement is touched."""
    from workeasy360.commercial.partners import selectors as partner_selectors

    presentation = partner_selectors.portal_presentation_for_organization(
        ctx.organization_id
    )
    if not presentation:
        return
    shown = []
    for product in listing["products"]:
        entry = presentation.get(product["code"])
        if (
            entry is not None
            and product.get("holding") is None
            and (not entry["show_in_explore"] or not entry["enabled_for_sale"])
        ):
            continue
        shown.append(product)
    listing["products"] = sorted(
        shown,
        key=lambda p: (presentation.get(p["code"], {}).get("display_order", 0) or 10**6,),
    )


@require_http_methods(["GET"])
def brand_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """This organization's own white-label brand, if it has one (Phase 11,
    Step 30) - resolved through its live partner link, never a Host header.
    Any member may read it: presentation only, carrying no partner id, no
    capability and no authority (the same contract the public, host-resolved
    endpoint keeps)."""
    from workeasy360.commercial.partners.selectors import brand_for_organization

    ctx = request.tenant_context  # type: ignore[attr-defined]
    brand = brand_for_organization(ctx.organization_id)
    if brand is None:
        return JsonResponse({"brand": None})
    payload = dict(brand)
    has_logo = payload.pop("logo_file_id", None) is not None
    payload["logo_url"] = f"/api/v1/orgs/{org_slug}/brand/logo" if has_logo else None
    response = JsonResponse({"brand": payload})
    response["Cache-Control"] = "private, no-store"
    return response


@require_http_methods(["GET"])
def brand_logo_view(request: HttpRequest, org_slug: str) -> HttpResponse:
    """The logo of this organization's own brand, as an image, or 404 - the
    same resolution and the same hardening as the public, host-based logo
    endpoint (no file id in the request, only the caller's own organization)."""
    from workeasy360.commercial.partners import branding
    from workeasy360.commercial.partners.selectors import brand_for_organization

    ctx = request.tenant_context  # type: ignore[attr-defined]
    brand = brand_for_organization(ctx.organization_id)
    logo = branding.public_logo(brand.get("logo_file_id") if brand else None)
    if logo is None:
        return HttpResponse(status=404)
    data, content_type = logo
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Content-Disposition"] = "inline"
    return response
