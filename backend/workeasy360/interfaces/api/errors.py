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

    Sits *outside* the tenancy middleware in the stack, so the exception has
    already propagated through the request's transaction and rolled it back
    before the response is built. A denial must never leave partial writes
    committed.
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
