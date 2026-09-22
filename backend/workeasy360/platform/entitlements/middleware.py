"""The per-request entitlement memo, installed.

``entitlement_request_scope()`` is what makes the R-14 budget a *per-request*
cost rather than a per-check one: the first check in a request reads the
snapshot, and every check after it is a dictionary lookup. Without it, each
check is a cache-backend round trip - correct, but measured against a budget
nothing in production was meeting.

It lives here rather than in the tenancy middleware because ``platform/
tenancy`` sits below ``platform/entitlements`` in the platform layering
contract (F-5) and may not import it. Registered **after** ``TenancyMiddleware``
in ``MIDDLEWARE``, which makes it the innermost of the two, so the memo exists
for exactly the span in which tenant context does.

The scope is per request, so nothing survives into the next one - a memo that
outlived a request would be a cross-tenant read.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from workeasy360.platform.entitlements.services.read import entitlement_request_scope


class EntitlementRequestScopeMiddleware:
    """Memoise entitlement reads for the duration of one request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        with entitlement_request_scope():
            return self.get_response(request)
