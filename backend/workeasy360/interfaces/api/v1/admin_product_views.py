"""The Super Admin products console over HTTP (Phase 11): ``/api/v1/admin/products``.

Thin, like every view (F-7): parse, call one service, shape the response.
Authorization, validation, the second factor and audit all live in the
services they call - ``controlplane.products`` and, beneath it,
``ecosystem.update_manifest`` / ``ecosystem.transition``, the same staff-only,
dangerous, audited writes the integration registry API uses.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, JsonResponse

from workeasy360.commercial.controlplane import products
from workeasy360.contracts.errors import ManifestRejected
from workeasy360.contracts.values import json_input
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.health import probe
from workeasy360.interfaces.api.v1.control_plane_http import limited, staff_view
from workeasy360.platform.authz.control_plane import acting_scope
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        payload = json_input.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise ManifestRejected("The request body must be valid JSON.") from None
    if not isinstance(payload, dict):
        raise ManifestRejected("The request body must be a JSON object.")
    return payload


@staff_view(["GET"])
def console_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(products.console(ctx))


@staff_view(["PATCH"])
def integration_view(
    request: HttpRequest, ctx: TenantContext, product_code: str
) -> JsonResponse:
    """Change a product's launch address and related public settings."""
    return JsonResponse(
        products.update_integration(
            ctx, product_code=product_code, changes=_body(request)
        )
    )


@staff_view(["POST"])
def integration_status_view(
    request: HttpRequest, ctx: TenantContext, product_code: str
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        products.set_integration_status(
            ctx,
            product_code=product_code,
            status=str(body.get("status", "")),
            reason=str(body.get("reason", "")),
        )
    )


@staff_view(["POST"])
def integration_health_check_view(
    request: HttpRequest, ctx: TenantContext, product_code: str
) -> JsonResponse:
    """Check one product's front door now and store what was seen.

    Authorized as a read of the integration (the selector demands the staff
    grant); the check itself goes through the SSRF-guarded egress client, so
    a staff member can make the platform look only at what a manifest
    registered - never at an address they typed here.
    """
    blocked = limited(request, "product_health_check", user=request.user)
    if blocked is not None:
        return blocked
    with acting_scope(ctx):
        found = ecosystem_selectors.integration_for_product(
            ctx, product_code=product_code
        )
    if found is None:
        raise ManifestRejected(
            "That product has no registered integration.", field="product_code"
        )
    result = probe.check_product(product_code)
    if result is None:
        # A draft or retired integration has nothing to check yet.
        return JsonResponse({"status": "unknown", "detail": "not_launchable"})
    return JsonResponse({"status": result.status.value, "detail": result.detail})
