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

from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_http_methods

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
    return JsonResponse({"apps": [_public_dict(d) for d in decisions]})


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
    """
    ctx = request.tenant_context  # type: ignore[attr-defined]
    decision = services.launch(ctx, product_code)

    if not decision.state.can_launch:
        metrics.increment("launcher_launch_denied_total", state=decision.state.value)
        return JsonResponse(
            {"code": "launch_denied", "state": decision.state.value}, status=409
        )

    metrics.increment("launcher_launch_allowed_total")
    # The documented convention every product integration guide names
    # (docs/phase-3/09-product-integration-guide.md): the browser arrives at
    # launch_url?organization=<uuid>, and the product itself starts the OIDC
    # authorization request from there, generating its own PKCE pair - the
    # launcher never touches a code, a token or a client credential (Task 17).
    query = urlencode({"organization": str(ctx.organization_id)})
    return HttpResponseRedirect(f"{decision.launch_url}?{query}")


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


def _public_dict(decision: AppAccessDecision) -> dict[str, Any]:
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
    }
