"""Thin HTTP adapters.

Views parse input, call one application service and shape output. Business
logic in a view is bypassed by the task path and by internal callers, and is
untestable without HTTP (F-7).
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.platform.authz import services as authz
from workeasy360.platform.entitlements.services import read as entitlements


@require_http_methods(["GET", "POST"])
def tenant_context_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """Echo the resolved tenant context.

    Exists so tests can assert that the middleware resolved, denied or
    write-blocked a request through a real HTTP path. GET and POST are both
    routed so the suspension block can be exercised on an unsafe method.
    """
    ctx = getattr(request, "tenant_context", None)
    if ctx is None:
        return JsonResponse(
            {"code": "missing_tenant_context", "detail": "No tenant context."},
            status=403,
        )
    return JsonResponse(
        {
            "organization_id": str(ctx.organization_id),
            "membership_id": str(ctx.membership_id),
            "organization_status": ctx.organization_status.value,
            "write_allowed": ctx.is_write_allowed,
            "aal": ctx.aal,
            "method": request.method,
        }
    )


@require_http_methods(["GET"])
def memberships_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """List memberships visible to the caller.

    Bounded page size with no unbounded variant: an unbounded list endpoint
    becomes an outage at a scale nobody chose
    (10-performance-concurrency.md section 8).
    """
    from workeasy360.platform.organizations.selectors import membership_summaries

    ctx = request.tenant_context  # type: ignore[attr-defined]
    authz.require(ctx, "platform.member.view")

    # Clamped by the selector; a non-numeric value is the default page, not a 500.
    try:
        limit = int(request.GET.get("limit") or 25)
    except ValueError:
        limit = 25
    cursor = request.GET.get("cursor") or None

    page = membership_summaries(ctx, limit=limit, cursor=cursor)
    return JsonResponse(
        {
            "results": page.items,
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }
    )


@require_http_methods(["DELETE"])
def membership_detail_view(
    request: HttpRequest, org_slug: str, membership_id: str
) -> JsonResponse:
    """Remove a member (Phase 5, Task 20/32/67 - the minimal membership-
    lifecycle mutation this phase adds; see platform.onboarding.services.
    remove_member for why a full invitation system is not built here).
    """
    from workeasy360.platform.onboarding.services import remove_member

    ctx = request.tenant_context  # type: ignore[attr-defined]
    remove_member(ctx, membership_id)
    return JsonResponse({"ok": True})


@require_http_methods(["GET", "PATCH"])
def organization_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """The current organization's own display data (Task 39: organization
    settings). ``platform.organization.view`` is a baseline permission every
    system role carries - see ``platform.authz.services``' role table - so
    this is the one settings read every member, not only an admin, may make.
    """
    from workeasy360.platform.organizations.selectors import organization_summary

    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "PATCH":
        # Phase 8 extension: rename (``platform.organization.update``). Only
        # the display name - the slug is URL identity and never changes.
        import json

        from workeasy360.platform.administration.security import rename

        try:
            body = json.loads(request.body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        name = body.get("display_name") if isinstance(body, dict) else None
        return JsonResponse(rename(ctx, display_name=name))
    authz.require(ctx, "platform.organization.view")

    summary = organization_summary(ctx)
    if summary is None:
        return JsonResponse({"code": "not_found"}, status=404)
    return JsonResponse(summary)


@require_http_methods(["GET"])
def entitlements_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """What this organization currently holds."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    authz.require(ctx, "platform.entitlement.view")

    return JsonResponse(
        {"products": [str(p) for p in entitlements.products(ctx.organization_id)]}
    )
