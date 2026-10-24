"""Control-plane HTTP adapters for the product integration registry.

Separate from ``ecosystem_views.py`` because these are a different surface
with a different trust model, and conflating them is how an endpoint ends up
with the wrong one:

    ecosystem_views          external, credential-authenticated, OAuth error
                             shape, CSRF-exempt, rate-limited
    integration_admin_views  internal, session + staff, platform error shape,
                             CSRF-protected, tenant-path scoped

These sit under ``/api/v1/orgs/<slug>/...`` like every other administrative
endpoint, which is what gives them a ``TenantContext`` - the acting staff
member's - for authorization and for the audit entry. Exactly the shape Phase
2 established for catalog administration, deliberately reused rather than
reinvented: control-plane writes performed by an identified staff member,
inside an organization context, audited against it.

Every mutation below is gated twice in the service it calls
(``platform.integration.*`` **and** ``require_platform_staff``) and every one
of those permissions is ``dangerous``, so impersonation
is blocked outright. None of that is enforced here - a view-only check is
bypassed by any internal caller (F-7, C7).
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.contracts.values import json_input
from workeasy360.platform.ecosystem import selectors, services


@require_http_methods(["GET", "POST"])
def integrations_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """List registrations, or register a new one from a submitted manifest."""
    ctx = request.tenant_context  # type: ignore[attr-defined]

    if request.method == "GET":
        return JsonResponse(
            {"results": [i.as_dict() for i in selectors.integrations(ctx)]}
        )

    integration = services.register_integration(ctx, manifest=_body(request))
    return JsonResponse(
        {
            "client_id": integration.client_id,
            "product_code": integration.product_code,
            "status": integration.status,
        },
        status=201,
    )


@require_http_methods(["GET", "PUT"])
def integration_detail_view(
    request: HttpRequest, org_slug: str, client_id: str
) -> JsonResponse:
    """Read one registration, or replace its manifest."""
    ctx = request.tenant_context  # type: ignore[attr-defined]

    if request.method == "GET":
        found = selectors.integration(ctx, client_id=client_id)
        if found is None:
            return JsonResponse(
                {"code": "product_integration_not_found", "detail": "Not found."},
                status=404,
            )
        return JsonResponse(found.as_dict())

    integration = services.update_manifest(
        ctx, client_id=client_id, manifest=_body(request)
    )
    return JsonResponse(
        {"client_id": integration.client_id, "status": integration.status}
    )


@require_http_methods(["POST"])
def integration_status_view(
    request: HttpRequest, org_slug: str, client_id: str
) -> JsonResponse:
    """Move a registration through its lifecycle.

    The target status is passed through as the string the caller sent. This
    view does not import ``ecosystem.models`` to build the enum itself -
    that would be cross-module ORM access (F-6), welding ``interfaces`` to
    another module's model file, and it would put validation in a view where
    the task path and every internal caller bypass it (F-7).
    """
    ctx = request.tenant_context  # type: ignore[attr-defined]
    body = _body(request)

    integration = services.transition(
        ctx,
        client_id=client_id,
        to_status=str(body.get("status", "")),
        reason=str(body.get("reason", "")),
    )
    return JsonResponse(
        {"client_id": integration.client_id, "status": integration.status}
    )


@require_http_methods(["GET", "POST"])
def credentials_view(request: HttpRequest, org_slug: str, client_id: str) -> JsonResponse:
    """List credential metadata, or issue a new credential.

    The POST response is the **only** time a generated secret exists outside
    the caller's own request: nothing stores it and no read path can produce
    it (services.IssuedCredential).
    """
    ctx = request.tenant_context  # type: ignore[attr-defined]

    if request.method == "GET":
        return JsonResponse(
            {
                "results": [
                    c.as_dict() for c in selectors.credentials(ctx, client_id=client_id)
                ]
            }
        )

    body = _body(request)
    issued = services.create_credential(
        ctx,
        client_id=client_id,
        kind=str(body.get("kind", services.DEFAULT_CREDENTIAL_KIND)),
        public_key_pem=str(body.get("public_key_pem", "")),
        algorithm=str(body.get("algorithm", "")),
        label=str(body.get("label", "")),
        valid_days=int(body.get("valid_days", services.DEFAULT_CREDENTIAL_DAYS)),
    )

    payload: dict[str, Any] = {
        "credential_id": issued.credential_id,
        "kind": issued.kind,
        "expires_at": issued.expires_at.isoformat(),
    }
    if issued.secret:
        payload["client_secret"] = issued.secret
        payload["notice"] = (
            "This secret is shown once and is not recoverable. Store it now; "
            "if it is lost, issue a new credential and revoke this one."
        )
    return JsonResponse(payload, status=201)


@require_http_methods(["DELETE"])
def credential_detail_view(
    request: HttpRequest, org_slug: str, client_id: str, credential_id: str
) -> JsonResponse:
    """Revoke a credential. Effective on the next request, with no delay."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    reason = request.GET.get("reason", "")
    credential = services.revoke_credential(
        ctx, client_id=client_id, credential_id=credential_id, reason=reason
    )
    return JsonResponse(
        {"credential_id": credential.credential_id, "status": credential.status}
    )


def _body(request: HttpRequest) -> dict[str, Any]:
    """Parse a JSON request body into a dict, or refuse.

    Refuses a non-object body rather than coercing it: the manifest validator
    reports "a manifest must be a JSON object", and a list arriving here
    would otherwise reach it as something it has to defend against twice.
    """
    from workeasy360.contracts.errors import ManifestRejected

    try:
        payload = json_input.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise ManifestRejected("The request body must be valid JSON.") from None
    if not isinstance(payload, dict):
        raise ManifestRejected("The request body must be a JSON object.")
    return payload
