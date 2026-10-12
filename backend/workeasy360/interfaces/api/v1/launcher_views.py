"""HTTP adapters for the app launcher: the directory, the launch redirect,
and seat administration (Tasks 6, 7, 13, 16).

Thin, as every view in this repository is (F-7): parse, call one
application service, shape the response. Every decision lives in
``platform/launcher``.

Every route here sits under the tenant path shape (``/orgs/<slug>/...``),
which is what gives it ``request.tenant_context`` - the caller's
already-verified organization and membership - established by
``TenancyMiddleware`` before this module is ever reached (12-security.md,
TB-1/TB-2). Nothing here reads an organization id from a header, a body or a
query parameter.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.platform.identity import portals as portal_links
from workeasy360.platform.launcher import selectors, services
from workeasy360.platform.launcher.access import AppAccessDecision
from workeasy360.platform.observability.metrics import metrics


@require_http_methods(["GET"])
def app_directory_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """Every marketable product, with this member's access state against
    each one (Task 6). Data-driven: the set of products comes from the
    catalog, not from anything hardcoded here (Task 21/22)."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    decisions = selectors.app_directory(ctx)
    extras = _card_extras(ctx, [d.product_code for d in decisions])
    return JsonResponse(
        {"apps": [_public_dict(d, extras.get(d.product_code)) for d in decisions]}
    )


@require_http_methods(["POST"])
def launch_view(request: HttpRequest, org_slug: str, product_code: str) -> HttpResponse:
    """Re-check access and, if allowed, redirect the browser at the
    product's own OIDC authorization endpoint (Task 16).

    **The browser never sees ``launch_url``.** This view builds the
    redirect target itself, server-side, from data ``platform/launcher``
    read through ``platform/ecosystem`` - never from anything the request
    supplied (Task 55: "backend controls launch target"). ``product_code``
    in the URL selects *which* decision to re-run; it does not select where
    the browser goes if that decision is ACTIVE.

    A POST, not a GET: unlike ``/authorize`` itself (a pure navigation),
    this is the launcher's own state transition - "I am now leaving the
    platform for this product" - and CSRF protection applies normally, as
    it does to every other tenant-scoped write in this codebase.

    **Denial is a redirect, not a JSON error.** The frontend submits this as
    a genuine HTML form POST (never ``fetch``) so the browser follows the
    success path's redirect as a real top-level navigation rather than
    silently fetching the product's page in the background (Task 54). A
    plain form submission has no way to read a JSON body, so a denial has
    to be answered the same way success is - a redirect the browser
    actually navigates - back to the launcher with the reason in the query
    string, where the shell renders it (Task 26: an explicit error state,
    never a raw JSON response filling the tab).
    """
    ctx = request.tenant_context  # type: ignore[attr-defined]
    decision = services.launch(ctx, product_code)
    wants_json = _wants_json(request)

    if not decision.state.can_launch:
        metrics.increment("launcher_launch_denied_total", state=decision.state.value)
        if wants_json:
            return JsonResponse(
                {
                    "code": "launch_denied",
                    "state": decision.state.value,
                    "product_code": product_code,
                },
                status=409,
            )
        query = urlencode({"launch_error": decision.state.value, "product": product_code})
        return HttpResponseRedirect(f"{portal_links.app_url()}/o/{org_slug}/apps?{query}")

    # ADR-044: which way in. The documented convention every product
    # integration guide names (docs/phase-3/09-product-integration-guide.md)
    # is unchanged for Workeasy360 sign-in: the browser arrives at
    # launch_url?organization=<uuid>, and the product itself starts the OIDC
    # authorization request from there, generating its own PKCE pair - the
    # launcher never touches a code, a token or a client credential (Task 17).
    # A person who prefers the product's own sign-in goes to that page.
    route = services.launch_route(ctx, decision)
    metrics.increment("launcher_launch_allowed_total", sign_in=route.sign_in)
    if wants_json:
        # The same server-owned destination, handed to the page's own script
        # instead of a 302. The page cannot choose it: it is what this
        # function computed from the registered integration. A 302 answering
        # a form POST is refused by Chromium under the app's CSP
        # (``form-action 'self'``) the moment it leaves the origin - which is
        # every real product - so the shell submits with ``fetch`` and then
        # navigates itself.
        return JsonResponse(
            {
                "redirect_url": route.url,
                "state": decision.state.value,
                "sign_in": route.sign_in,
            }
        )
    return HttpResponseRedirect(route.url)


@require_http_methods(["GET"])
def seat_overview_view(
    request: HttpRequest, org_slug: str, product_code: str
) -> JsonResponse:
    """Seat capacity and per-member assignment for one product (Task 13).
    Authorization lives in the selector (F-7)."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    limit = min(int(request.GET.get("limit", 100)), 100)
    cursor = request.GET.get("cursor") or None
    overview = selectors.seat_overview(ctx, product_code, limit=limit, cursor=cursor)
    return JsonResponse(overview.as_dict())


@require_http_methods(["POST", "DELETE"])
def seat_member_view(
    request: HttpRequest, org_slug: str, product_code: str, membership_id: str
) -> JsonResponse:
    """Assign (POST) or release (DELETE) one member's seat. Both are
    idempotent - ``platform.launcher.services`` already makes them so; the
    admin UI's assign/unassign buttons never fail on a double click."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "POST":
        result = services.assign_seat(
            ctx, product_code=product_code, membership_id=membership_id
        )
        return JsonResponse(result, status=201)

    result = services.release_seat(
        ctx, product_code=product_code, membership_id=membership_id
    )
    return JsonResponse(result)


def _wants_json(request: HttpRequest) -> bool:
    """The shell asks for JSON explicitly; a plain form POST (no script, or
    the compatibility path) still gets the redirect."""
    return "application/json" in request.headers.get("Accept", "")


def _days_left(ends_at: datetime, now: datetime) -> int:
    """Whole days left, rounded up, never negative: "1 day left" until the
    last moment, not "0 days left" for a trial that still works."""
    return max(0, math.ceil((ends_at - now).total_seconds() / 86400))


def _card_extras(ctx: Any, product_codes: list[str]) -> dict[str, dict[str, Any]]:
    """What a product card shows beyond its access state, in a fixed number of
    queries however many products there are: presentation, connection facts,
    the plan / trial / renewal the organization holds, and seat usage.

    Money and renewal are shown only to a member who may see the organization's
    subscriptions; seat usage only to one who may administer seats. Everyone
    sees what a product is, whether they hold it and - for a trial - when it
    ends, because that decides whether *they* can use it next week."""
    from django.utils import timezone

    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.subscriptions import my_products
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.ecosystem import selectors as ecosystem
    from workeasy360.platform.ecosystem import sign_in
    from workeasy360.platform.entitlements.services import seats as seat_services

    now = timezone.now()
    permissions = authz.effective_permissions(ctx)
    may_see_billing = "commercial.subscription.view" in permissions
    may_see_seats = "platform.seat.assign" in permissions or may_see_billing

    presentation = catalog.product_presentations(product_codes)
    presence = ecosystem.presence_for(product_codes)
    # ADR-044: how this person enters each product - two fixed queries for the
    # whole directory (the registry, and this person's own preferences).
    signing_in = sign_in.product_sign_in(ctx.user_id, product_codes)
    held = my_products.holdings(at=now)
    usage = seat_services.usage_by_pool(ctx.organization_id) if may_see_seats else {}

    extras: dict[str, dict[str, Any]] = {}
    for code in product_codes:
        shown = presentation.get(code, {})
        link = presence.get(code)
        holding = held.get(code)
        card: dict[str, Any] = {
            "slug": shown.get("slug", ""),
            "tagline": shown.get("tagline", ""),
            "category": shown.get("category", ""),
            "icon": shown.get("icon", ""),
            "sso": link.sso if link else "integration_required",
            "health": {
                "status": link.health_status if link else "unknown",
                "checked_at": (
                    link.health_checked_at.isoformat()
                    if link and link.health_checked_at
                    else None
                ),
            },
            "trial": None,
            "plan": None,
            "seats": None,
            "renews_at": None,
            "cancel_at": None,
            "sign_in": _sign_in_json(signing_in.get(code)),
        }
        if holding is not None:
            if holding.trial_ends_at is not None:
                card["trial"] = {
                    "id": holding.trial_id,
                    "ends_at": holding.trial_ends_at.isoformat(),
                    "days_left": _days_left(holding.trial_ends_at, now),
                }
            if may_see_billing:
                card["plan"] = {"code": holding.plan_code, "name": holding.plan_name}
                card["renews_at"] = (
                    holding.renews_at.isoformat() if holding.renews_at else None
                )
                card["cancel_at"] = (
                    holding.cancel_at.isoformat() if holding.cancel_at else None
                )
            if may_see_seats and code in usage:
                total, assigned = usage[code]
                card["seats"] = {"total": total, "used": assigned}
        extras[code] = card
    return extras


def _sign_in_json(found: Any) -> dict[str, Any] | None:
    """The card's sign-in facts (ADR-044): whether Workeasy360 sign-in works
    for this product (``sso``: enabled / integration_required / disabled),
    the product's own mode, this person's setting and what therefore happens.
    The server decides all of it; the card only words it."""
    if found is None:
        return None
    full = found.as_dict()
    return {
        key: full[key]
        for key in (
            "sso",
            "mode",
            "product_login_available",
            "override",
            "preference",
            "effective",
            "constraint",
        )
    }


def _public_dict(
    decision: AppAccessDecision, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The app-directory wire shape. Deliberately excludes ``launch_url`` -
    the launcher never hands the browser a URL to navigate to directly; it
    hands it a state and the browser POSTs to ``launch_view`` when the state
    is ACTIVE (Task 6: "do not expose ... redirect configuration")."""
    return {
        "product_code": decision.product_code,
        "display_name": decision.display_name,
        "description": decision.description,
        "licensing_mode": decision.licensing_mode.value,
        "state": decision.state.value,
        "can_launch": decision.state.can_launch,
        "entitlement_status": decision.entitlement_status or None,
        **(extra or {}),
    }
