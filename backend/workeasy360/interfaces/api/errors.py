"""Domain errors to HTTP responses.

Two jobs, both security-relevant:

**Correct status codes.** A ``PermissionDenied`` must reach the client as 403
and an ``EntitlementDenied`` as 402, because the remedies differ - one is an
admin conversation, the other a sales one. Letting them surface as unhandled
500s conflates "you may not" with "we broke", and makes error rate useless as
a signal.

**Safe error bodies.** An unrecognised exception never leaks its type, its
message or a traceback. The client gets a generic failure plus the
correlation id, which is enough for support to find the exact request in the
logs without disclosing anything about internals (12-security.md section 18).
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from workeasy360.contracts.errors import Workeasy360Error
from workeasy360.platform.observability.correlation import current_correlation_id
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)


class DomainErrorMiddleware:
    """Converts domain exceptions into their declared HTTP responses.

    **Transaction semantics - read before relying on a rollback.** Listed
    outside the tenancy middleware, but Django invokes ``process_exception``
    inside the innermost handler, so this runs *inside* the tenant request
    transaction, and the tenancy middleware rolls back only a >= 500
    response. A write made before a 4xx domain error is therefore
    **committed**. Services keep that from mattering by validating before
    their first write or by writing inside an inner ``transaction.atomic()``
    the error propagates out of (docs/architecture/debt-register.md D-016).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception):
        if not isinstance(exception, Workeasy360Error):
            # Not ours. Let Django's handler deal with it, so that DEBUG
            # still shows a useful page locally and production still returns
            # its standard 500 - rather than this middleware inventing a
            # third behaviour.
            return None

        status = exception.http_status
        body = exception.as_dict()
        body["correlation_id"] = str(current_correlation_id() or "")

        # A denial is an expected outcome, not a system failure. Logging it at
        # error level would destroy the value of error rate as a signal
        # (11-observability.md section 4 rule 6).
        level = logger.error if status >= 500 else logger.info
        level(
            "http.domain_error",
            error_code=exception.code,
            http_status=status,
            http_path=_route(request),
        )
        return JsonResponse(body, status=status)


def _route(request: HttpRequest) -> str:
    match = getattr(request, "resolver_match", None)
    if match is not None and match.route:
        return f"/{match.route.lstrip('/')}"
    return "<unmatched>"


def csrf_failure(request: HttpRequest, reason: str = "") -> JsonResponse:
    """``CSRF_FAILURE_VIEW``: the API's own error shape instead of Django's
    HTML page (Phase 12). Django's ``reason`` is logged, never returned - it
    names the missing or mismatched cookie or origin, which only helps someone
    probing the check."""
    logger.info("http.csrf_refused", reason=reason, http_path=request.path)
    return JsonResponse(
        {
            "code": "csrf_failed",
            "detail": "This request could not be verified. Reload the page and try again.",
            "correlation_id": str(current_correlation_id() or ""),
        },
        status=403,
    )
