"""The User Admin Panel's own reads and commands (2026-11-11): ``/orgs/<slug>/...``.

- ``wallets`` (+ ``/transactions``, ``/usage``): the organization's three balances,
  their movements and the usage behind them - ``platform.billing.view``.
- ``wallets/top-up`` and ``wallets/credit-packs``: an order for money into a wallet or
  for AI credits, paid through the invoice like any other order -
  ``platform.billing.manage``. ``Idempotency-Key`` required.
- ``notifications``: the caller's alerts for the top bar's bell - any member, each
  category behind the permission its data needs.
- ``notification-preferences``: the caller's own in-app choices - any member, for
  themselves only.

Thin: each handler reads named fields and calls one service. The organization is the
tenancy middleware's verified context, never a body or query value.
"""

from __future__ import annotations

import json
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.controlplane import customer_alerts
from workeasy360.commercial.payments import wallet_orders
from workeasy360.commercial.wallets import customer as wallets
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values import json_input
from workeasy360.interfaces.api.v1.control_plane_http import limited
from workeasy360.platform.notifications import preferences


def _ctx(request: HttpRequest) -> Any:
    return request.tenant_context  # type: ignore[attr-defined]


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json_input.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CatalogValidationError("Malformed JSON body.", field="body") from exc
    if not isinstance(payload, dict):
        raise CatalogValidationError("The body must be a JSON object.", field="body")
    return payload


def _key(request: HttpRequest) -> str:
    return str(request.headers.get("Idempotency-Key") or "")


@require_http_methods(["GET"])
def wallets_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    return JsonResponse(wallets.overview(_ctx(request)))


@require_http_methods(["GET"])
def wallet_transactions_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    q = request.GET
    return JsonResponse(
        wallets.transactions(
            _ctx(request),
            kind=q.get("kind") or None,
            entry_type=q.get("type") or None,
            date_from=q.get("from"),
            date_to=q.get("to"),
            limit=q.get("limit"),
            offset=q.get("offset"),
        )
    )


@require_http_methods(["GET"])
def wallet_usage_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    q = request.GET
    return JsonResponse(
        wallets.usage_events(
            _ctx(request),
            meter=q.get("meter") or None,
            product=q.get("product") or None,
            date_from=q.get("from"),
            date_to=q.get("to"),
            limit=q.get("limit"),
            offset=q.get("offset"),
        )
    )


@require_http_methods(["POST"])
def wallet_top_up_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    blocked = limited(request, "order_place", user=getattr(request, "user", None))
    if blocked is not None:
        return blocked
    body = _body(request)
    placed = wallet_orders.member_top_up(
        _ctx(request),
        wallet_kind=body.get("wallet_kind"),
        amount_minor=body.get("amount_minor"),
        currency=body.get("currency"),
        idempotency_key=_key(request),
    )
    return JsonResponse(placed, status=201)


@require_http_methods(["POST"])
def wallet_credit_pack_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    blocked = limited(request, "order_place", user=getattr(request, "user", None))
    if blocked is not None:
        return blocked
    placed = wallet_orders.member_credit_pack(
        _ctx(request),
        pack_code=_body(request).get("pack_code"),
        idempotency_key=_key(request),
    )
    return JsonResponse(placed, status=201)


@require_http_methods(["GET"])
def notifications_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    return JsonResponse(customer_alerts.for_member(_ctx(request)))


@require_http_methods(["GET", "PUT"])
def notification_preferences_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    ctx = _ctx(request)
    if request.method == "PUT":
        return JsonResponse(preferences.update(ctx, _body(request).get("categories")))
    return JsonResponse(preferences.read(ctx))
