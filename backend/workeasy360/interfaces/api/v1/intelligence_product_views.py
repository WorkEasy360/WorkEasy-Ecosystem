"""The product-facing AI, retrieval and analytics endpoints (Phase 8).

Under ``/integrations/`` beside event ingestion, because they share its trust
boundary exactly: a registered product authenticates with its Phase 3 client
credential (``client_assertion`` per RFC 7523, or HTTP Basic), never a
session, so these views are CSRF-exempt and enumerate themselves in
``CREDENTIALED_VIEWS`` (A23). The product code - and with it every namespace,
skill, source and metric the call may touch - comes from the credential and
never from the request (Tasks 14, 18, 22, 64).

**Form-encoded, JSON response**, like the token, entitlement and event
endpoints and for the same reason: client authentication travels as form
fields, so a request's JSON payload travels as one more field (``request``,
``skill``, ``document``, ``query``, ``metric``). One authentication path.

Thin (F-7): authenticate, call one service, render. Every refusal is JSON with
the ``error`` key a product's client library already switches on - OAuth codes
for authentication and rate limits (``invalid_client``,
``temporarily_unavailable``), and the intelligence codes
(``contracts.errors.IntelligenceError``) for everything else. No provider
message, credential, prompt or stack ever reaches a response (Task 107).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from workeasy360.contracts.errors import IntelligenceError
from workeasy360.platform.ecosystem import clientauth, protocol, verification
from workeasy360.platform.observability.correlation import current_correlation_id
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: Headroom over a payload for the client-authentication fields that share
#: the form body (a signed ``client_assertion`` is ~1 KB).
_AUTH_OVERHEAD_BYTES = 16_384


def _handle(
    request: HttpRequest,
    *,
    endpoint: str,
    limit_bytes: int,
    call: Callable[[Any], tuple[int, Any]],
) -> JsonResponse:
    """Authenticate the product, run ``call(client)``, render the answer or
    the refusal. The oversized-body check runs before anything is parsed."""
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    if declared > limit_bytes + _AUTH_OVERHEAD_BYTES:
        return _oauth_error(
            protocol.OAuthError(
                protocol.INVALID_REQUEST, "The request is too large.", http_status=413
            )
        )
    try:
        client = clientauth.authenticate(request, endpoint=endpoint)
        status, body = call(client)
    except protocol.OAuthError as exc:
        return _oauth_error(exc)
    except verification.VerificationUnavailable:
        return _oauth_error(
            protocol.OAuthError(
                protocol.TEMPORARILY_UNAVAILABLE,
                "The request could not be decided right now. Retry.",
                http_status=503,
                headers={"Retry-After": "5"},
            )
        )
    except IntelligenceError as exc:
        return _intelligence_error(exc)
    response = JsonResponse(body, status=status, safe=not isinstance(body, list))
    response["Cache-Control"] = "no-store"
    return response


def _oauth_error(exc: protocol.OAuthError) -> JsonResponse:
    response = JsonResponse(
        exc.as_dict(correlation_id=str(current_correlation_id() or "")),
        status=exc.http_status,
    )
    for header, value in exc.headers.items():
        response[header] = value
    response["Cache-Control"] = "no-store"
    return response


def _intelligence_error(exc: IntelligenceError) -> JsonResponse:
    body = {
        "error": exc.code,
        "error_description": exc.message,
        "correlation_id": str(current_correlation_id() or ""),
        **{k: v for k, v in exc.context.items() if v is not None},
    }
    response = JsonResponse(body, status=exc.http_status)
    if exc.http_status == 503:
        response["Retry-After"] = str(getattr(exc, "retry_after", 30))
    response["Cache-Control"] = "no-store"
    level = logger.error if exc.http_status >= 500 else logger.info
    level("ai.request.refused", error_code=exc.code, http_status=exc.http_status)
    return response


def _payload_limit() -> int:
    return int(settings.AI_MAX_INPUT_BYTES) + 8_192


# --- skills --------------------------------------------------------------------------------


@csrf_exempt
@require_POST
@never_cache
def ai_skills(request: HttpRequest) -> JsonResponse:
    """Register one skill version (``skill``: JSON), or - with no ``skill``
    field - list the calling product's registered skills."""
    from workeasy360.intelligence.gateway import service

    def call(client: Any) -> tuple[int, Any]:
        if "skill" not in request.POST:
            return 200, {"results": service.product_skills(client)}
        skill, created = service.register_skill(client, request.POST.get("skill"))
        return (201 if created else 200), {"skill": skill, "created": created}

    return _handle(request, endpoint="ai_registry", limit_bytes=65_536, call=call)


@csrf_exempt
@require_POST
@never_cache
def ai_skill_retire(request: HttpRequest) -> JsonResponse:
    from workeasy360.intelligence.gateway import service

    return _handle(
        request,
        endpoint="ai_registry",
        limit_bytes=4_096,
        call=lambda client: (
            200,
            {"skill": service.retire_skill(client, request.POST.get("skill"))},
        ),
    )


# --- invocation ----------------------------------------------------------------------------


@csrf_exempt
@require_POST
@never_cache
def ai_invoke(request: HttpRequest) -> JsonResponse:
    """Invoke a skill for an organization: ``organization_id`` and ``request``
    (JSON). 200 with the output (sync), 202 with a request id (async)."""
    from workeasy360.intelligence.gateway import service

    def call(client: Any) -> tuple[int, Any]:
        answer = service.invoke(
            client,
            organization_id=request.POST.get("organization_id"),
            raw=request.POST.get("request"),
        )
        return answer.status, answer.body

    return _handle(request, endpoint="ai_invoke", limit_bytes=_payload_limit(), call=call)


@csrf_exempt
@require_POST
@never_cache
def ai_request_status(request: HttpRequest) -> JsonResponse:
    """Poll one of this product's requests: ``organization_id``, ``request_id``."""
    from workeasy360.intelligence.gateway import service

    def call(client: Any) -> tuple[int, Any]:
        answer = service.job_status(
            client,
            organization_id=request.POST.get("organization_id"),
            request_id=request.POST.get("request_id"),
        )
        return answer.status, answer.body

    return _handle(request, endpoint="ai_invoke", limit_bytes=4_096, call=call)


# --- retrieval ------------------------------------------------------------------------------


@csrf_exempt
@require_POST
@never_cache
def rag_documents(request: HttpRequest) -> JsonResponse:
    """Submit one document version for indexing: ``organization_id`` and
    ``document`` (JSON). 202 accepted, 200 duplicate."""
    from workeasy360.intelligence.rag import ingestion

    def call(client: Any) -> tuple[int, Any]:
        result = ingestion.submit(
            client,
            organization_id=request.POST.get("organization_id"),
            raw=request.POST.get("document"),
        )
        return (202 if result["status"] == "accepted" else 200), result

    return _handle(
        request,
        endpoint="rag_ingest",
        limit_bytes=int(settings.RAG_MAX_DOCUMENT_CHARS) * 6 + 65_536,
        call=call,
    )


@csrf_exempt
@require_POST
@never_cache
def rag_document_delete(request: HttpRequest) -> JsonResponse:
    from workeasy360.intelligence.rag import ingestion

    return _handle(
        request,
        endpoint="rag_ingest",
        limit_bytes=4_096,
        call=lambda client: (
            200,
            ingestion.delete_document(
                client,
                organization_id=request.POST.get("organization_id"),
                source=request.POST.get("source"),
                document_id=request.POST.get("document_id"),
            ),
        ),
    )


@csrf_exempt
@require_POST
@never_cache
def rag_document_status(request: HttpRequest) -> JsonResponse:
    from workeasy360.intelligence.rag import ingestion

    return _handle(
        request,
        endpoint="rag_ingest",
        limit_bytes=4_096,
        call=lambda client: (
            200,
            ingestion.status(
                client,
                organization_id=request.POST.get("organization_id"),
                source=request.POST.get("source"),
                document_id=request.POST.get("document_id"),
            ),
        ),
    )


@csrf_exempt
@require_POST
@never_cache
def rag_source_revoke(request: HttpRequest) -> JsonResponse:
    from workeasy360.intelligence.rag import ingestion

    return _handle(
        request,
        endpoint="rag_ingest",
        limit_bytes=4_096,
        call=lambda client: (
            200,
            ingestion.revoke_source(
                client,
                organization_id=request.POST.get("organization_id"),
                source=request.POST.get("source"),
            ),
        ),
    )


@csrf_exempt
@require_POST
@never_cache
def rag_retrieve(request: HttpRequest) -> JsonResponse:
    """Nearest authorized passages: ``organization_id`` and ``query`` (JSON)."""
    from workeasy360.intelligence.gateway import service

    def call(client: Any) -> tuple[int, Any]:
        answer = service.retrieve(
            client,
            organization_id=request.POST.get("organization_id"),
            raw=request.POST.get("query"),
        )
        return answer.status, answer.body

    return _handle(request, endpoint="rag_retrieve", limit_bytes=16_384, call=call)


# --- analytics ------------------------------------------------------------------------------


@csrf_exempt
@require_POST
@never_cache
def analytics_metrics(request: HttpRequest) -> JsonResponse:
    """Register one metric version (``metric``: JSON), or - with no ``metric``
    field - list the metrics this product owns or was shared."""
    from workeasy360.intelligence.analytics import service

    def call(client: Any) -> tuple[int, Any]:
        if "metric" not in request.POST:
            return 200, {"results": service.product_metrics(client)}
        metric, created = service.register_metric(client, request.POST.get("metric"))
        return (201 if created else 200), {"metric": metric, "created": created}

    return _handle(request, endpoint="analytics_registry", limit_bytes=65_536, call=call)


@csrf_exempt
@require_POST
@never_cache
def analytics_query(request: HttpRequest) -> JsonResponse:
    """Query one metric for one organization: ``organization_id`` and
    ``query`` (JSON: metric, version, start, end, granularity, group_by)."""
    from workeasy360.intelligence.analytics import service

    return _handle(
        request,
        endpoint="analytics_query",
        limit_bytes=4_096,
        call=lambda client: (
            200,
            service.product_query(
                client,
                organization_id=request.POST.get("organization_id"),
                raw=request.POST.get("query"),
            ),
        ),
    )


#: Credential-authenticated and therefore CSRF-exempt; appended to
#: ``ecosystem_views.CREDENTIALED_VIEWS`` so the A23 fitness test and
#: ``test_credentialed_endpoints_ignore_session_authentication`` cover them.
CREDENTIALED_VIEWS = (
    ai_skills,
    ai_skill_retire,
    ai_invoke,
    ai_request_status,
    rag_documents,
    rag_document_delete,
    rag_document_status,
    rag_source_revoke,
    rag_retrieve,
    analytics_metrics,
    analytics_query,
)
