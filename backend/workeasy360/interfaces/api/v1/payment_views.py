"""Payments over HTTP (Phase 8 extension).

Three audiences, three trust models:

- **The organization** (``/orgs/<slug>/billing/...``): session + tenancy
  middleware; services authorize (``platform.billing.view`` to read,
  ``platform.billing.manage`` to start a payment - not dangerous since Phase 9:
  the provider's hosted page authenticates the payer).
- **The provider** (``/public/payments/webhooks/<provider>``): no session and no
  cookie - the provider's **signature over the raw body** is the credential, so
  the view is CSRF-exempt and listed in ``SIGNED_WEBHOOK_VIEWS`` for the
  architecture test that permits exactly that. It never reads the session.
- **Staff** (``/admin/payments/...``, ``/admin/customers/<org>/...``): the staff
  session decorator; services require ``platform.staff_payment.*``.

The development test checkout (``/public/payments/test-checkout/complete``)
exists only while ``PAYMENTS_FAKE_PROVIDER_ENABLED`` (production pins it off)
and signs its outcome like a provider would, then takes the same webhook path.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.payments import checkout, orders, refunds, staff, webhooks
from workeasy360.commercial.payments import selectors as payment_selectors
from workeasy360.contracts.errors import AuthenticationRequired, PaymentValidationError
from workeasy360.contracts.values import json_input
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1.control_plane_http import limited, staff_view


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json_input.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PaymentValidationError("Malformed JSON body.", field="body") from exc
    if not isinstance(payload, dict):
        raise PaymentValidationError("The body must be a JSON object.", field="body")
    return payload


# --- the organization --------------------------------------------------------------

#: The fields an order request may carry - identifiers and choices, never a
#: price, currency, status or amount (mass-assignment allowlist, Phase 9).
_ORDER_FIELDS = (
    "plan_version_id",
    "bundle_version_id",
    "activate_subscription_id",
    "declined_plan_version_ids",
    "billing_interval",
    "quantity",
    "with_trial",
)


def _order_request(body: dict[str, Any]) -> dict[str, Any]:
    return {key: body[key] for key in _ORDER_FIELDS if key in body}


@require_http_methods(["POST"])
def order_quote_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """What an order would cost now - the checkout page's summary."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    return JsonResponse(orders.quote(ctx, **_order_request(_body(request))))


@require_http_methods(["GET", "POST"])
def orders_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """GET: the organization's orders. POST: place one (``Idempotency-Key``
    required). The response says whether its invoice must now be paid."""
    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        return JsonResponse(
            orders.orders(
                ctx,
                cursor=request.GET.get("cursor") or None,
                limit=_limit(request),
            )
        )
    user = getattr(request, "user", None)
    blocked = limited(request, "order_place", user=user)
    if blocked is not None:
        return blocked
    body = _body(request)
    placed = orders.place(
        ctx,
        idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
        **_order_request(body),
    )
    return JsonResponse(placed, status=201)


def _limit(request: HttpRequest) -> int:
    from workeasy360.contracts.values.cursor import clamp_limit

    return clamp_limit(
        request.GET.get("limit"), default=orders.PAGE_SIZE, maximum=orders.MAX_PAGE
    )


@require_http_methods(["GET"])
def order_detail_view(
    request: HttpRequest, org_slug: str, order_id: UUID
) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    return JsonResponse(orders.order_detail(ctx, order_id))


@require_http_methods(["POST"])
def order_cancel_view(
    request: HttpRequest, org_slug: str, order_id: UUID
) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    return JsonResponse(orders.cancel(ctx, order_id))


@require_http_methods(["GET"])
def invoices_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    return JsonResponse(
        payment_selectors.invoices(
            ctx,
            status=request.GET.get("status") or None,
            cursor=request.GET.get("cursor") or None,
        )
    )


@require_http_methods(["GET"])
def invoice_view(request: HttpRequest, org_slug: str, invoice_id: UUID) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    return JsonResponse(payment_selectors.invoice(ctx, invoice_id))


@require_http_methods(["POST"])
def invoice_pay_view(
    request: HttpRequest, org_slug: str, invoice_id: UUID
) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    blocked = limited(request, "payment_start", user=request.user)
    if blocked is not None:
        return blocked
    key = request.headers.get("Idempotency-Key") or _body(request).get("idempotency_key")
    result = checkout.start_payment(
        ctx, invoice_id=invoice_id, idempotency_key=str(key or "")[:129]
    )
    return JsonResponse(result, status=202)


@require_http_methods(["GET"])
def payment_status_view(
    request: HttpRequest, org_slug: str, attempt_id: UUID
) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    return JsonResponse(checkout.attempt_status(ctx, attempt_id))


# --- the provider ------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def webhook_view(request: HttpRequest, provider_key: str) -> JsonResponse:
    """Signature-authenticated. Reads the raw body and headers only - never the
    session, never a cookie."""
    blocked = limited(request, "payment_webhook")
    if blocked is not None:
        return blocked
    headers = dict(request.headers.items())
    return JsonResponse(webhooks.process(provider_key[:32], headers, request.body))


#: CSRF-exempt because a provider signature, not a cookie, authenticates them
#: (``test_every_session_authenticated_write_is_csrf_protected``).
SIGNED_WEBHOOK_VIEWS = (webhook_view,)


@require_http_methods(["POST"])
def test_checkout_complete_view(request: HttpRequest) -> JsonResponse:
    """Development only (404 otherwise): the test provider's "hosted page"
    reports the outcome the developer chose - signed like a provider callback
    and applied through the same webhook path (``payments.testing``)."""
    from workeasy360.commercial.payments import testing

    if not settings.PAYMENTS_FAKE_PROVIDER_ENABLED:
        return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise AuthenticationRequired("Sign in to continue.")
    body = _body(request)
    return JsonResponse(
        testing.complete(
            body.get("organization_id"),
            reference=str(body.get("reference") or "")[:128],
            outcome=body.get("outcome"),
            user_id=user.id,
        )
    )


# --- staff -------------------------------------------------------------------------


@staff_view(["GET", "POST"])
def providers_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse({"results": staff.providers(ctx)})
    return JsonResponse(staff.create_provider(ctx, _body(request)), status=201)


@staff_view(["PATCH"])
def provider_detail_view(
    request: HttpRequest, ctx: TenantContext, key: str
) -> JsonResponse:
    return JsonResponse(staff.update_provider(ctx, key, _body(request)))


@staff_view(["GET"])
def events_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse({"results": staff.event_log(ctx)})


@staff_view(["GET"])
def staff_orders_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Every order across customers (Phase 9)."""
    from workeasy360.contracts.values.cursor import clamp_limit

    return JsonResponse(
        orders.staff_orders(
            ctx,
            status=request.GET.get("status") or None,
            cursor=request.GET.get("cursor") or None,
            limit=clamp_limit(request.GET.get("limit"), maximum=orders.MAX_PAGE),
        )
    )


@staff_view(["POST"])
def billing_terms_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    """Set a customer's collection mode and billing region (Phase 9)."""
    from workeasy360.commercial.billing.services import set_billing_terms
    from workeasy360.contracts.enums import CollectionMode

    body = _body(request)
    try:
        mode = CollectionMode(str(body.get("collection_mode") or ""))
    except ValueError as exc:
        raise PaymentValidationError(
            "collection_mode is automatic, invoice or partner_billed.",
            field="collection_mode",
        ) from exc
    account = set_billing_terms(
        ctx,
        organization_id,
        collection_mode=mode,
        billing_region=str(body.get("billing_region") or ""),
        reason=str(body.get("reason") or ""),
    )
    return JsonResponse(
        {
            "collection_mode": account.collection_mode,
            "billing_region": account.billing_region,
        }
    )


@staff_view(["GET"])
def customer_billing_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    return JsonResponse(staff.customer_billing(ctx, organization_id))


@staff_view(["POST"])
def invoice_mark_paid_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, invoice_id: UUID
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        staff.mark_invoice_paid(
            ctx,
            organization_id,
            invoice_id,
            reference=body.get("reference"),
            reason=body.get("reason"),
            expected_version=body.get("version"),
        )
    )


@staff_view(["POST"])
def invoice_void_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, invoice_id: UUID
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        staff.void_invoice(
            ctx,
            organization_id,
            invoice_id,
            reason=body.get("reason"),
            expected_version=body.get("version"),
        )
    )


@staff_view(["POST"])
def refund_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, attempt_id: UUID
) -> HttpResponse:
    body = _body(request)
    return JsonResponse(
        refunds.request_refund(
            ctx,
            organization_id=organization_id,
            attempt_id=attempt_id,
            amount_minor=body.get("amount_minor"),
            reason=str(body.get("reason") or ""),
            idempotency_key=str(
                request.headers.get("Idempotency-Key")
                or body.get("idempotency_key")
                or ""
            )[:129],
        ),
        status=202,
    )
