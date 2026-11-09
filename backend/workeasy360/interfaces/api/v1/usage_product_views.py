"""The product-facing usage endpoint (2026-11-09).

Under ``/integrations/`` with the same trust boundary as event ingestion: a registered
product authenticates with its client credential, never a session, so this is CSRF-exempt
and listed in ``CREDENTIALED_VIEWS``. The product comes from the credential, and the
organization must be entitled to that product right now. Form-encoded, like its neighbours:
the JSON payload is one more field (``event``).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from workeasy360.commercial.controlplane import usage_intake
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.platform.ecosystem import clientauth, protocol, ratelimit, verification
from workeasy360.platform.observability.correlation import current_correlation_id


def _error(code: str, description: str, status: int) -> JsonResponse:
    response = JsonResponse(
        {
            "error": code,
            "error_description": description,
            "correlation_id": str(current_correlation_id() or ""),
        },
        status=status,
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
@never_cache
def usage_events(request: HttpRequest) -> JsonResponse:
    """One billable event: ``organization_id`` and ``event`` (JSON with ``meter``,
    ``quantity``, ``idempotency_key`` and optionally ``occurred_at``)."""
    try:
        client = clientauth.authenticate(request, endpoint="usage_events")
        ratelimit.consume("usage_events", client.client_id, endpoint="usage_events")
    except protocol.OAuthError as exc:
        response = JsonResponse(
            exc.as_dict(correlation_id=str(current_correlation_id() or "")),
            status=exc.http_status,
        )
        for header, value in exc.headers.items():
            response[header] = value
        return response
    try:
        organization = UUID(str(request.POST.get("organization_id")))
        event: Any = json.loads(request.POST.get("event") or "")
    except (ValueError, TypeError):
        return _error("invalid_request", "Send organization_id and event.", 400)
    if not isinstance(event, dict):
        return _error("invalid_request", "event must be an object.", 400)
    decision = verification.verify(organization_id=organization, product_code=client.product_code)
    if not decision.allowed:
        return _error(
            "organization_not_entitled", "This organization does not hold this product.", 403
        )
    try:
        answer = usage_intake.report(
            product_code=client.product_code,
            organization_id=organization,
            meter=event.get("meter"),
            quantity=event.get("quantity"),
            idempotency_key=event.get("idempotency_key"),
            occurred_at=event.get("occurred_at"),
        )
    except CatalogValidationError as exc:
        return _error("invalid_request", exc.message, 400)
    response = JsonResponse(answer)
    response["Cache-Control"] = "no-store"
    return response


CREDENTIALED_VIEWS = (usage_events,)
