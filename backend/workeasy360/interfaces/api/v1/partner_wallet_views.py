"""The partner portal's Platform Wallet, wallet-paid plans, subscriptions,
billing records, help and sales material (ADR-063).

Every view is ``partner_view``: the partner id in the path is the caller's own
active membership (or the staff guest view of exactly that partner, which can
only read). The services authorize every action again - permission, KYC and
account state, the customer's live assignment, the product grant - and price
every amount themselves; the browser sends identifiers, choices and the amount
it was shown.
"""

from __future__ import annotations

from uuid import UUID

from django.http import HttpRequest, JsonResponse

from workeasy360.commercial.controlplane import partner_billing, partner_wallet
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.control_plane_http import limited, partner_view

_body = legacy._body


def _key(request: HttpRequest, body: dict) -> str:
    return legacy._idempotency_key(request, body)


def _email(request: HttpRequest) -> str:
    return str(getattr(request.user, "email", "") or "")


# --- the wallet ----------------------------------------------------------------------


@partner_view(["GET"])
def wallet_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_wallet.wallet(ctx))


@partner_view(["GET"])
def wallet_transactions_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        partner_wallet.transactions(
            ctx,
            currency=request.GET.get("currency"),
            entry_type=request.GET.get("type"),
            limit=request.GET.get("limit", 25),
            offset=request.GET.get("offset", 0),
        )
    )


@partner_view(["POST"])
def wallet_top_up_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    blocked = limited(request, "partner_wallet", user=request.user)
    if blocked is not None:
        return blocked
    body = _body(request)
    payment = partner_wallet.start_top_up(
        ctx,
        amount_minor=body.get("amount_minor"),
        amount=body.get("amount"),
        currency=body.get("currency"),
        email=_email(request),
    )
    return JsonResponse(payment, status=201)


# --- plan activation, renewal and plan change paid from the wallet -------------------


@partner_view(["POST"])
def activation_quote_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    return JsonResponse(
        partner_wallet.activation_quote(ctx, organization_id, _body(request))
    )


@partner_view(["POST"])
def activation_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    blocked = limited(request, "partner_wallet", user=request.user)
    if blocked is not None:
        return blocked
    body = _body(request)
    return JsonResponse(
        partner_wallet.activate(
            ctx, organization_id, body, idempotency_key=_key(request, body)
        ),
        status=201,
    )


@partner_view(["GET", "POST"])
def renewal_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, subscription_id: UUID
) -> JsonResponse:
    """GET: what renewing the next period from the wallet costs. POST: pay it now."""
    if request.method == "POST":
        blocked = limited(request, "partner_wallet", user=request.user)
        if blocked is not None:
            return blocked
        return JsonResponse(
            partner_wallet.renew(
                ctx,
                organization_id,
                subscription_id,
                expected_charge_minor=_body(request).get("expected_charge_minor"),
            )
        )
    return JsonResponse(
        partner_wallet.renewal_quote(ctx, organization_id, subscription_id)
    )


@partner_view(["POST"])
def plan_change_quote_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    return JsonResponse(partner_wallet.change_quote(ctx, organization_id, _body(request)))


@partner_view(["POST"])
def plan_change_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    blocked = limited(request, "partner_wallet", user=request.user)
    if blocked is not None:
        return blocked
    body = _body(request)
    return JsonResponse(
        partner_wallet.change_plan(
            ctx, organization_id, body, idempotency_key=_key(request, body)
        )
    )


# --- reads ----------------------------------------------------------------------------


@partner_view(["GET"])
def customer_products_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    return JsonResponse(partner_billing.customer_products(ctx, organization_id))


@partner_view(["GET"])
def subscriptions_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        partner_billing.subscriptions(
            ctx, status=request.GET.get("status", "all"), q=request.GET.get("q", "")
        )
    )


@partner_view(["GET"])
def billing_records_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_billing.billing_records(ctx))


@partner_view(["GET"])
def help_articles_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_billing.help_articles(ctx, kind=request.GET.get("kind")))


@partner_view(["GET"])
def sales_resources_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_billing.sales_resources(ctx))
