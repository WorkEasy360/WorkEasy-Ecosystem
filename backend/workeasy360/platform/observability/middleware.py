"""Correlation and request-logging middleware."""

from __future__ import annotations

import time
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from workeasy360.platform.observability.correlation import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    correlation_scope,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    metrics,
)

logger = get_logger(__name__)


class CorrelationIdMiddleware:
    """Establishes the correlation spine. First in the chain.

    First so that a failure anywhere later - including in security middleware
    - still carries an id that ties the log line to the client's report.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming_correlation = request.headers.get(CORRELATION_ID_HEADER)
        incoming_request = request.headers.get(REQUEST_ID_HEADER)

        with correlation_scope(incoming_correlation, incoming_request) as correlation_id:
            request.correlation_id = correlation_id  # type: ignore[attr-defined]
            response = self.get_response(request)
            # Returned so a customer reporting "this failed" can quote an id
            # that locates the exact request. This alone removes most of the
            # guesswork from support escalations.
            response[CORRELATION_ID_HEADER] = str(correlation_id)
            response[REQUEST_ID_HEADER] = str(correlation_id)
            return response


class RequestLogMiddleware:
    """One structured line per request, plus RED metrics."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.perf_counter()
        response = self.get_response(request)
        duration = time.perf_counter() - started

        route = self._route(request)
        status_class = f"{response.status_code // 100}xx"

        metrics.increment(
            HTTP_REQUESTS,
            method=request.method or "?",
            route=route,
            status_class=status_class,
        )
        metrics.observe(
            HTTP_REQUEST_DURATION, duration, method=request.method or "?", route=route
        )

        # 5xx is an error; 4xx is an expected outcome. Logging a 404 at error
        # level destroys the value of error rate as a signal
        # (11-observability.md section 4 rule 6).
        log = logger.error if response.status_code >= 500 else logger.info
        log(
            "http.request",
            http_method=request.method,
            http_path=route,
            http_status=response.status_code,
            duration_ms=round(duration * 1000, 2),
        )
        return response

    @staticmethod
    def _route(request: HttpRequest) -> str:
        """The URL *pattern*, never the resolved path.

        ``/api/v1/orgs/{slug}/crm/deals/{id}`` rather than the concrete path -
        otherwise every object id becomes its own metric series.
        """
        match = getattr(request, "resolver_match", None)
        if match is not None and match.route:
            return f"/{match.route.lstrip('/')}"
        return "<unmatched>"
