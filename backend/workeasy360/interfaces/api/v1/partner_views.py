"""The partner portal API (Phase 7): ``/api/v1/partner/<partner_id>/...``.

The partner id in the path is **verified against the caller's own active
membership** before anything else (``partner_view``); a partner the user does
not belong to - or that does not exist - is the same 404. What a partner may do
is then decided by the service from its *capability grants*, never from a
partner type, and a customer-scoped action additionally requires an active
relationship with exactly that customer.

Nothing here exposes another partner's data, a customer's members, contact
details or business records, a margin or a commission rate: the selectors these
call return narrow, partner-shaped rows.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.commissions import referrals
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane import diagnostics as cp_diagnostics
from workeasy360.commercial.controlplane import partner_network
from workeasy360.commercial.partners import activation, branding, customers, domains
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.enums import BillingInterval, ChangeTiming
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.control_plane_http import (
    authenticated_user,
    end_guest_view,
    guest_context,
    limited,
    live_guest_view,
    page_response,
    partner_view,
    query_page_args,
)
from workeasy360.platform.authz import services as authz

_body = legacy._body
_required = legacy._required
_uuid = legacy._uuid
_enum = legacy._enum
_int = legacy._int
_flag = legacy._flag


def _owner_name(body: dict[str, Any]) -> str:
    """The owner's name from the first/last fields the form sends (or a single
    ``owner_name``). Joined here; validated as text where it is used."""
    first = str(body.get("owner_first_name") or "").strip()
    last = str(body.get("owner_last_name") or "").strip()
    return f"{first} {last}".strip() or str(body.get("owner_name") or "")


def _key(request: HttpRequest, body: dict[str, Any]) -> str:
    return legacy._idempotency_key(request, body)


# --- who am I (session level: no partner id) ---------------------------------


@require_http_methods(["GET"])
def portal_view(request: HttpRequest) -> JsonResponse:
    """The partners the signed-in user works for, with their capabilities.

    The portal derives its navigation from ``capabilities`` - never from a
    partner type - so an affiliate sees referral and commission pages and a
    white-label partner adds branding, with no per-type switch anywhere.
    Empty for a user who is not a partner user; that is not an error.
    """
    user = authenticated_user(request)
    partners = partner_selectors.partners_for_user(user.id)
    guest_entry = _guest_entry(request, {p["partner_id"] for p in partners})
    if guest_entry is not None:
        partners.append(guest_entry)
    return JsonResponse({"partners": partners})


def _guest_entry(request: HttpRequest, listed: set[str]) -> dict[str, Any] | None:
    """The partner a staff member is viewing as a guest, marked ``guest``
    (``commercial.partners.guest``); ``None`` without a live guest view."""
    from workeasy360.contracts.errors import AccessPolicyDenied, PermissionDenied

    view = live_guest_view(request)
    if view is None or str(view.partner_id) in listed:
        return None
    try:
        ctx = guest_context(request, view.partner_id)
    except (PermissionDenied, AccessPolicyDenied):
        return None
    entry = partner_selectors.guest_portal_entry(ctx) if ctx is not None else None
    if entry is None:
        return None
    return {
        **entry,
        "guest": {"reason": view.reason, "expires_at": view.expires_at.isoformat()},
    }


@require_http_methods(["POST"])
def guest_view_end_view(request: HttpRequest) -> JsonResponse:
    """ "Exit guest session": ends this sign-in's guest view, if any (always 200)."""
    return JsonResponse({"ended": end_guest_view(request)})


# --- customers ------------------------------------------------------------


@partner_view(["GET", "POST"])
def customers_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        authz.require(ctx, "platform.partner_customer.view")
        return page_response(
            partner_selectors.partner_customers_page(
                ctx.partner_id,
                status=request.GET.get("status") or None,
                search=request.GET.get("q") or None,
                **query_page_args(request),
            )
        )
    limited_response = limited(request, "partner_provision", user=request.user)
    if limited_response is not None:
        return limited_response
    body = _body(request)
    result = customers.provision_customer(
        ctx,
        organization_name=body.get("organization_name"),
        owner_email=body.get("owner_email"),
        idempotency_key=_key(request, body),
        currency=body.get("currency"),
        owner_name=_owner_name(body),
    )
    return JsonResponse(
        {
            "organization_id": str(result.organization_id),
            "slug": result.slug,
            "link_id": str(result.link_id),
            "replayed": result.replayed,
            # The same shape whether the owner's address was new or known.
            "owner": "invited",
        },
        status=200 if result.replayed else 201,
    )


@partner_view(["GET"])
def customer_subscription_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    return JsonResponse(
        {"subscriptions": customers.customer_subscription(ctx, organization_id)}
    )


@partner_view(["POST"])
def customer_billing_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    body = _body(request)
    account = customers.open_billing(ctx, organization_id, currency=body.get("currency"))
    return JsonResponse({"currency": account.currency})


@partner_view(["POST"])
def owner_invitation_resend_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    """Send the owner of one of *this partner's* customers a new activation link."""
    blocked = limited(request, "owner_invitation_send", user=request.user)
    if blocked is not None:
        return blocked
    invite = activation.resend(ctx, organization_id)
    return JsonResponse(activation.public_state(invite))


@partner_view(["POST"])
def owner_invitation_revoke_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    blocked = limited(request, "owner_invitation_send", user=request.user)
    if blocked is not None:
        return blocked
    return JsonResponse(activation.public_state(activation.revoke(ctx, organization_id)))


@partner_view(["GET"])
def catalog_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    interval = _enum(
        {"interval": request.GET.get("interval") or None},
        "interval",
        BillingInterval,
        BillingInterval.MONTHLY,
    )
    return JsonResponse({"results": customers.approved_catalog(ctx, interval=interval)})


@partner_view(["POST"])
def sell_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    body = _body(request)
    # An absent quantity is one seat; an explicit 0 or negative one is refused by
    # the service - it must not be coerced into a sale of one.
    quantity = _int(body, "quantity", 1)
    item = customers.sell_plan(
        ctx,
        organization_id,
        plan_version_id=_uuid(body, "plan_version_id"),
        quantity=1 if quantity is None else quantity,
        idempotency_key=_key(request, body),
        billing_interval=_enum(
            body, "billing_interval", BillingInterval, BillingInterval.MONTHLY
        ),
        with_trial=_flag(body, "with_trial"),
    )
    return JsonResponse(
        {"subscription_item_id": str(item.id), "product_code": item.product_code},
        status=201,
    )


#: ADR-053: what a partner's order for a customer may carry - identifiers and
#: choices only (the same allowlist as a customer's own order), never a price.
_ORDER_FIELDS = (
    "plan_version_id",
    "billing_interval",
    "quantity",
    "with_trial",
    "cart_items",
    "activate_subscription_id",
)


def _order_request(body: dict[str, Any]) -> dict[str, Any]:
    return {key: body[key] for key in _ORDER_FIELDS if key in body}


@partner_view(["POST"])
def customer_order_quote_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    """What an order for this customer would cost: the server's price."""
    return JsonResponse(
        customers.quote_customer_order(
            ctx, organization_id, **_order_request(_body(request))
        )
    )


@partner_view(["POST"])
def customer_order_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    """Place a central Order for this customer (``Idempotency-Key`` required)."""
    body = _body(request)
    order = customers.place_customer_order(
        ctx,
        organization_id,
        idempotency_key=_key(request, body),
        **_order_request(body),
    )
    return JsonResponse(order, status=201)


@partner_view(["POST"])
def change_plan_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, item_id: UUID
) -> JsonResponse:
    body = _body(request)
    item = customers.change_customer_plan(
        ctx,
        organization_id,
        subscription_item_id=item_id,
        to_plan_version_id=_uuid(body, "to_plan_version_id"),
        idempotency_key=_key(request, body),
        expected_version=_int(body, "expected_version"),
        timing=_enum(body, "timing", ChangeTiming, "") or None,
    )
    return JsonResponse(
        {"subscription_item_id": str(item.id), "plan_code": item.plan_code}, status=201
    )


@partner_view(["POST"])
def change_quantity_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, item_id: UUID
) -> JsonResponse:
    body = _body(request)
    item = customers.change_customer_quantity(
        ctx,
        organization_id,
        subscription_item_id=item_id,
        quantity=_int(body, "quantity"),
        idempotency_key=_key(request, body),
        expected_version=_int(body, "expected_version"),
    )
    return JsonResponse(
        {"subscription_item_id": str(item.id), "quantity": item.quantity}, status=201
    )


@partner_view(["POST"])
def cancel_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, subscription_id: UUID
) -> JsonResponse:
    body = _body(request)
    sub = customers.cancel_customer_subscription(
        ctx,
        organization_id,
        subscription_id=subscription_id,
        idempotency_key=_key(request, body),
        expected_version=_int(body, "expected_version"),
    )
    return JsonResponse({"id": str(sub.id), "status": sub.status})


@partner_view(["POST"])
def activate_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, subscription_id: UUID
) -> JsonResponse:
    body = _body(request)
    sub = customers.activate_customer_subscription(
        ctx,
        organization_id,
        subscription_id=subscription_id,
        idempotency_key=_key(request, body),
        expected_version=_int(body, "expected_version"),
    )
    return JsonResponse({"id": str(sub.id), "status": sub.status})


@partner_view(["POST"])
def diagnose_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    limited_response = limited(request, "diagnose", user=request.user)
    if limited_response is not None:
        return limited_response
    body = _body(request)
    return JsonResponse(
        cp_diagnostics.diagnose(
            ctx,
            organization_id,
            product_code=str(body.get("product_code") or ""),
            member_email=(
                str(body["member_email"]) if body.get("member_email") else None
            ),
        )
    )


# --- commissions and payouts ------------------------------------------------


@partner_view(["GET"])
def commissions_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    authz.require(ctx, "platform.partner_commission.view")
    page = commission_selectors.accruals_page(
        partner_id=ctx.partner_id, for_partner=True, **query_page_args(request)
    )
    return JsonResponse(
        {
            **page.as_dict(),
            "balances": commission_selectors.balance_summary(ctx.partner_id),
        }
    )


@partner_view(["GET"])
def payouts_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    authz.require(ctx, "platform.partner_payout.view")
    return page_response(
        commission_selectors.payouts_page(
            partner_id=ctx.partner_id, for_partner=True, **query_page_args(request)
        )
    )


# --- referrals ------------------------------------------------------------


@partner_view(["GET", "POST"])
def referrals_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        payload = referrals.summary(ctx)
        page = commission_selectors.referrals_page(
            ctx.partner_id,
            status=request.GET.get("status") or None,
            **query_page_args(request),
        )
        return JsonResponse(
            {
                **payload,
                # Everyone who followed a link, whatever became of them: made here,
                # so no screen adds up the statuses itself.
                "total": sum(payload["counts"].values()),
                "referrals": page.as_dict(),
            }
        )
    body = _body(request)
    code = referrals.create_referral_code(
        ctx,
        label=body.get("label", ""),
        code=body.get("code"),
        product_code=body.get("product_code", ""),
        plan_code=body.get("plan_code", ""),
        campaign_id=body.get("campaign_id"),
    )
    return JsonResponse(
        {
            "id": str(code.id),
            "code": code.code,
            "label": code.label,
            "product_code": code.product_code,
            "plan_code": code.plan_code,
            "campaign_id": str(code.campaign_id) if code.campaign_id else None,
            "url": partner_network.referral_url(code.code),
        },
        status=201,
    )


@partner_view(["GET", "POST"])
def campaigns_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """ADR-053: the partner's campaigns - list, or start one."""
    if request.method == "GET":
        authz.require(ctx, "platform.partner_referral.view")
        return JsonResponse({"campaigns": commission_selectors.campaigns(ctx.partner_id)})
    campaign = referrals.create_campaign(ctx, name=_body(request).get("name"))
    return JsonResponse(
        {"id": str(campaign.id), "name": campaign.name, "status": campaign.status},
        status=201,
    )


@partner_view(["POST"])
def campaign_status_view(
    request: HttpRequest, ctx: TenantContext, campaign_id: UUID
) -> JsonResponse:
    campaign = referrals.set_campaign_status(
        ctx, campaign_id, active=_body(request).get("active")
    )
    return JsonResponse({"id": str(campaign.id), "status": campaign.status})


@partner_view(["POST"])
def referral_code_disable_view(
    request: HttpRequest, ctx: TenantContext, code_id: UUID
) -> JsonResponse:
    code = referrals.disable_referral_code(ctx, code_id)
    return JsonResponse({"id": str(code.id), "status": code.status})


# --- branding and domains ------------------------------------------------------


@partner_view(["GET", "PUT"])
def branding_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse({"brand": branding.get_brand(ctx)})
    body = _body(request)
    return JsonResponse(
        branding.update_brand(
            ctx,
            brand_name=body.get("brand_name"),
            accent_color=body.get("accent_color", ""),
            theme=body.get("theme"),
            support_email=body.get("support_email", ""),
            support_url=body.get("support_url", ""),
            logo_file_id=_uuid(body, "logo_file_id", required=False),
            expected_version=_int(body, "expected_version"),
            portal=body.get("portal"),
        )
    )


@partner_view(["POST"])
def logo_upload_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """A logo as a raw request body. The type comes from ``Content-Type`` and is
    then **checked against the bytes** by the files platform; size is capped
    before and after."""
    file_id = branding.upload_logo(
        ctx, content=request.body, content_type=request.headers.get("Content-Type", "")
    )
    return JsonResponse({"logo_file_id": str(file_id)}, status=201)


def _domain_json(row: Any) -> dict[str, Any]:
    return partner_selectors.domain_dict(
        row, txt_name=domains.verification_record_name(row.hostname)
    )


@partner_view(["GET", "POST"])
def domains_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        authz.require(ctx, "platform.partner_domain.view")
        return JsonResponse(
            {
                "results": [
                    _domain_json(d) for d in partner_selectors.domains_for(ctx.partner_id)
                ]
            }
        )
    body = _body(request)
    return JsonResponse(
        _domain_json(domains.request_domain(ctx, hostname=body.get("hostname"))),
        status=201,
    )


@partner_view(["POST"])
def domain_verify_view(
    request: HttpRequest, ctx: TenantContext, domain_id: UUID
) -> JsonResponse:
    limited_response = limited(request, "domain_verify", user=request.user)
    if limited_response is not None:
        return limited_response
    return JsonResponse(_domain_json(domains.verify_domain(ctx, domain_id=domain_id)))


@partner_view(["POST"])
def domain_activate_view(
    request: HttpRequest, ctx: TenantContext, domain_id: UUID
) -> JsonResponse:
    return JsonResponse(_domain_json(domains.activate_domain(ctx, domain_id=domain_id)))


@partner_view(["POST"])
def domain_disable_view(
    request: HttpRequest, ctx: TenantContext, domain_id: UUID
) -> JsonResponse:
    return JsonResponse(_domain_json(domains.disable_domain(ctx, domain_id=domain_id)))
