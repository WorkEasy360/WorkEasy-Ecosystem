"""The partner API: a small, read-only slice of a partner's data for the partner's
own systems, authenticated by a scoped bearer key (ADR-046).

``/api/v1/partner-api/...`` and nothing else accepts a key. The rules, each pinned
by a test:

- **No session.** A key is the only credential here, and a browser's cookies are
  never read: ``request.user`` plays no part, so a signed-in person gains
  nothing and a key gains no session.
- **Read-only.** Only ``GET``. There is no write, no money movement and no
  administration behind a key.
- **The partner comes from the key**, never from the request: there is no
  ``partner_id`` in these URLs to tamper with, so one partner's key cannot name
  another partner.
- **One answer for every failure.** Missing, malformed, unknown, wrong, revoked,
  expired, a paused partner and a removed creator are the same ``401`` - the
  endpoint is not an oracle for which of them it was - and refusals are
  rate-limited by address.
- **Scope is re-checked per request** against what the key's creator may still
  do (``api_keys.authenticate``), and a missing scope is a ``403``.
- **Bounded**: a per-key and a per-address request ceiling, paged results, and
  ``Cache-Control: no-store``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.partners import api_keys
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    page_response,
    query_page_args,
)
from workeasy360.platform.observability import throttle

_WINDOW = 60


def _refuse(request: HttpRequest) -> JsonResponse:
    blocked = limited(request, "partner_api_fail")
    if blocked is not None:
        return blocked
    response = JsonResponse(
        {"code": "invalid_credentials", "detail": "The API key is not valid."},
        status=401,
    )
    response["WWW-Authenticate"] = "Bearer"
    return response


def _bearer(request: HttpRequest) -> str | None:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def key_view(
    scope: str,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Decorate ``view(request, principal)`` as an endpoint that needs ``scope``."""

    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @require_http_methods(["GET"])
        @functools.wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            blocked = limited(request, "partner_api")
            if blocked is not None:
                return blocked
            principal = api_keys.authenticate(_bearer(request))
            if principal is None:
                return _refuse(request)
            ceiling = int(getattr(settings, "PARTNER_API_RATE_PER_KEY", 120))
            if (
                throttle.hit(
                    f"w360:papi:key:{principal.key_id}:{throttle.window_slot(_WINDOW)}",
                    _WINDOW,
                )
                > ceiling
            ):
                response = JsonResponse(
                    {"code": "rate_limited", "detail": "Too many requests."}, status=429
                )
                response["Retry-After"] = str(throttle.retry_after(_WINDOW))
                return response
            if scope not in principal.scopes:
                return JsonResponse(
                    {"code": "insufficient_scope", "detail": f"This key lacks {scope}."},
                    status=403,
                )
            response = view(request, principal, *args, **kwargs)
            response["Cache-Control"] = "no-store"
            return response

        return wrapper

    return decorate


#: Bearer-authenticated, read-only and blind to the session cookie, so CSRF-exempt
#: (a cookie is never trusted here). Enumerated for the architecture test that
#: allows a CSRF exemption only where a credential, not a cookie, authenticates.
@csrf_exempt
@key_view("account.read")
def account_view(request: HttpRequest, principal: api_keys.Principal) -> JsonResponse:
    row = partner_selectors.partner_row(principal.partner_id)
    assert row is not None
    return JsonResponse(
        {
            "name": row.display_name,
            "status": row.status,
            "payout_currency": row.payout_currency,
            "scopes": sorted(principal.scopes),
        }
    )


@csrf_exempt
@key_view("customers.read")
def customers_view(request: HttpRequest, principal: api_keys.Principal) -> JsonResponse:
    return page_response(
        partner_selectors.partner_customers_page(
            principal.partner_id,
            search=request.GET.get("q") or None,
            **query_page_args(request),
        )
    )


@csrf_exempt
@key_view("commissions.read")
def commissions_view(request: HttpRequest, principal: api_keys.Principal) -> JsonResponse:
    return page_response(
        commission_selectors.accruals_page(
            partner_id=principal.partner_id,
            for_partner=True,
            **query_page_args(request),
        )
    )


CREDENTIALED_VIEWS = (account_view, customers_view, commissions_view)
