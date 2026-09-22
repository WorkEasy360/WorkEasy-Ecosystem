"""Correlation, request-logging and security-header middleware."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import lru_cache

from django.conf import settings
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


class SecurityHeadersMiddleware:
    """Emits Content-Security-Policy and Permissions-Policy on every response.

    **The CSP trigger, closed.** Phase 3's audit found "no HTML surface
    exists - zero templates in the repository" and recorded the CSP trigger
    as not reached (docs/phase-3/17-audit-findings.md, "Verified, no
    finding"). Phase 4 introduces a browser-facing frontend, and with it the
    trigger: docs/phase-4/08-csp-and-browser-security.md.

    **Why this process, when the frontend is a separate Next.js deployment.**
    This Django process still answers browser requests that carry the session
    cookie - the login, organization and app-directory endpoints chief among
    them - and a strict CSP on every one of its responses is a real
    defence-in-depth layer against a markup-injection bug in anything this
    process ever renders, at zero cost to a JSON API that renders nothing
    today. The frontend's *own* CSP is a Next.js concern, set on its own
    responses, and is not this middleware's job (Task 35: "do not
    duplicate/conflict across ... layers" - each layer sets its own, once).

    **Why a five-line middleware and not a dependency.** The policy is one
    header with a fixed value computed once at import time; ``django-csp``
    would add a dependency (Task 56/57: justify every one) to do what
    ``HttpResponse.__setitem__`` already does. If the policy ever needs
    per-view nonces or reporting, that is the point to reconsider - not
    before.

    Placed last in ``MIDDLEWARE`` (nearest the response) so it runs on every
    response this process emits, including one built entirely by an earlier
    middleware, such as a 429 from the rate limiter.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        csp = _csp_header_value()
        if csp:
            response.setdefault("Content-Security-Policy", csp)
        policy = _permissions_policy_header_value()
        if policy:
            response.setdefault("Permissions-Policy", policy)
        return response


@lru_cache(maxsize=1)
def _csp_header_value() -> str:
    """Rendered once per process. The policy is process-wide static
    configuration, not per-request state."""
    directives = getattr(settings, "CONTENT_SECURITY_POLICY", None) or {}
    return "; ".join(
        f"{directive} {' '.join(sources)}" if sources else directive
        for directive, sources in directives.items()
    )


@lru_cache(maxsize=1)
def _permissions_policy_header_value() -> str:
    policy = getattr(settings, "PERMISSIONS_POLICY", None) or {}
    return ", ".join(
        f"{feature}=({' '.join(allowlist)})" for feature, allowlist in policy.items()
    )
