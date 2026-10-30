"""The partner business panel API (ADR-045): ``/api/v1/partner/<partner_id>/...``.

Thin adapters, like every view here: parse, call one service or read model,
shape the response. The partner id is verified against the caller's own active
membership by ``partner_view`` before anything runs; what the caller may then do
is decided by the service from the permissions their role and the partner's
capabilities resolve to. Nothing a browser sends is trusted as an amount, a
partner id, a capability or a role name - each is validated server-side against
a registry.
"""

from __future__ import annotations

import csv
import io
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse

from workeasy360.commercial.controlplane import (
    partner_help,
    partner_network,
    partner_portal,
    partner_workspaces,
)
from workeasy360.commercial.partners import (
    api_keys,
    hierarchy,
    panel_selectors,
    product_settings,
    profile,
    services,
    translations,
    upgrades,
)
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    page_response,
    partner_view,
    query_page_args,
)

_body = legacy._body


# --- the overview, money and the honest feature list -----------------------------


@partner_view(["GET"])
def dashboard_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        partner_portal.dashboard(
            ctx,
            range_key=request.GET.get("range"),
            date_from=request.GET.get("from"),
            date_to=request.GET.get("to"),
        )
    )


@partner_view(["GET"])
def affiliate_overview_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """The affiliate workspace's dashboard (ADR-047). Needs the referral view."""
    return JsonResponse(
        partner_workspaces.affiliate_overview(
            ctx,
            range_key=request.GET.get("range"),
            date_from=request.GET.get("from"),
            date_to=request.GET.get("to"),
        )
    )


@partner_view(["GET"])
def affiliate_products_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """ADR-054 My Products: what the partner may promote and how its links do."""
    return JsonResponse(
        partner_workspaces.my_products(
            ctx,
            range_key=request.GET.get("range"),
            date_from=request.GET.get("from"),
            date_to=request.GET.get("to"),
        )
    )


@partner_view(["GET"])
def affiliate_store_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """ADR-055 Store Console: Explore Products and My Requests in one read."""
    return JsonResponse(partner_workspaces.store_console(ctx))


@partner_view(["POST"])
def affiliate_product_request_view(
    request: HttpRequest, ctx: TenantContext
) -> JsonResponse:
    """ADR-055: ask to promote one Store product. Grants nothing; staff decide."""
    from workeasy360.commercial.partners import promotion_requests

    return JsonResponse(promotion_requests.submit(ctx, body=_body(request)), status=201)


@partner_view(["GET"])
def reseller_store_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """ADR-055 Store Console for sellers: Explore Products and My Requests."""
    return JsonResponse(partner_workspaces.store_console(ctx, kind="sell"))


@partner_view(["POST"])
def reseller_product_request_view(
    request: HttpRequest, ctx: TenantContext
) -> JsonResponse:
    """ADR-055: ask to sell one Store product. Grants nothing; staff decide."""
    from workeasy360.commercial.partners import promotion_requests

    return JsonResponse(
        promotion_requests.submit(ctx, body=_body(request), kind="sell"), status=201
    )


@partner_view(["GET"])
def referral_trail_view(
    request: HttpRequest, ctx: TenantContext, referral_id: UUID
) -> JsonResponse:
    """ADR-054: the attribution trail of one of the partner's referrals."""
    return JsonResponse(partner_workspaces.referral_trail(ctx, referral_id))


@partner_view(["GET"])
def reports_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Reports & Analytics: factual counts and ledger sums, each section only
    for the caller who may see it (ADR-047)."""
    return JsonResponse(
        partner_workspaces.reports(
            ctx,
            range_key=request.GET.get("range"),
            date_from=request.GET.get("from"),
            date_to=request.GET.get("to"),
        )
    )


@partner_view(["GET"])
def white_label_status_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Where the brand setup and the custom domain stand (ADR-047)."""
    return JsonResponse(partner_workspaces.white_label_status(ctx))


@partner_view(["GET"])
def balance_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.balance(ctx))


@partner_view(["GET"])
def features_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.features(ctx))


@partner_view(["GET"])
def orders_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return page_response(partner_portal.orders(ctx, **query_page_args(request)))


@partner_view(["GET"])
def activity_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return page_response(partner_portal.activity(ctx, **query_page_args(request)))


# --- customers: the export ------------------------------------------------------------


@partner_view(["GET"])
def customers_export_view(request: HttpRequest, ctx: TenantContext) -> HttpResponse:
    """The customer list as CSV: bounded, audited, rate-limited, formula-safe.
    Served as a download, never inline, with no caching."""
    blocked = limited(request, "partner_export", user=request.user)
    if blocked is not None:
        return blocked
    status = request.GET.get("status") or None
    if status not in (None, "active", "suspended", "ended"):
        raise PartnerValidationError("Unknown status.", field="status")
    rows, truncated = partner_portal.export_customers(ctx, status=status)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(partner_portal.EXPORT_COLUMNS)
    writer.writerows(rows)
    response = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="customers.csv"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Export-Truncated"] = "true" if truncated else "false"
    return response


# --- products, plans, pricing -------------------------------------------------------------


@partner_view(["GET"])
def products_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.products(ctx))


@partner_view(["PUT"])
def product_order_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        {
            "order": product_settings.set_display_order(
                ctx, product_codes=body.get("order")
            )
        }
    )


@partner_view(["PUT"])
def product_setting_view(
    request: HttpRequest, ctx: TenantContext, product_code: str
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        product_settings.update_product_setting(
            ctx,
            product_code=product_code,
            enabled_for_sale=body.get("enabled_for_sale"),
            show_in_explore=body.get("show_in_explore"),
            show_in_my_products=body.get("show_in_my_products"),
        )
    )


@partner_view(["GET"])
def plans_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.plans(ctx))


@partner_view(["GET"])
def packages_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.packages(ctx))


@partner_view(["GET"])
def currencies_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.currencies(ctx))


@partner_view(["GET"])
def price_book_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.price_book(ctx))


# --- sub-partners and affiliates ------------------------------------------------------------


@partner_view(["GET", "POST"])
def sub_partners_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return page_response(
            partner_network.sub_partners(ctx, **query_page_args(request))
        )
    blocked = limited(request, "partner_provision", user=request.user)
    if blocked is not None:
        return blocked
    body = _body(request)
    created = hierarchy.create_sub_partner(
        ctx,
        display_name=body.get("display_name"),
        administrator_email=body.get("administrator_email"),
        administrator_name=body.get("administrator_name", ""),
        capability_keys=body.get("capabilities"),
        product_codes=body.get("products"),
    )
    return JsonResponse(
        {
            "id": str(created.child_id),
            "code": created.code,
            "status": "draft",
            "administrator": "attached",
        },
        status=201,
    )


@partner_view(["GET"])
def sub_partner_options_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_network.sub_partner_options(ctx))


@partner_view(["POST"])
def sub_partner_activate_view(
    request: HttpRequest, ctx: TenantContext, child_id: UUID
) -> JsonResponse:
    child = hierarchy.activate_sub_partner(ctx, child_id)
    return JsonResponse({"id": str(child.id), "status": child.status})


@partner_view(["POST"])
def sub_partner_suspend_view(
    request: HttpRequest, ctx: TenantContext, child_id: UUID
) -> JsonResponse:
    child = hierarchy.suspend_sub_partner(
        ctx, child_id, reason=_body(request).get("reason")
    )
    return JsonResponse({"id": str(child.id), "status": child.status})


@partner_view(["POST"])
def sub_partner_resume_view(
    request: HttpRequest, ctx: TenantContext, child_id: UUID
) -> JsonResponse:
    child = hierarchy.resume_sub_partner(ctx, child_id)
    return JsonResponse({"id": str(child.id), "status": child.status})


@partner_view(["PUT"])
def sub_partner_equipment_view(
    request: HttpRequest, ctx: TenantContext, child_id: UUID
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        hierarchy.set_sub_partner_equipment(
            ctx,
            child_id,
            capability_keys=body.get("capabilities"),
            product_codes=body.get("products"),
        )
    )


@partner_view(["GET"])
def affiliates_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_network.affiliates(ctx))


@partner_view(["GET"])
def conversions_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return page_response(partner_network.conversions(ctx, **query_page_args(request)))


# --- the team --------------------------------------------------------------------------------


@partner_view(["GET", "POST"])
def members_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(partner_network.members(ctx))
    blocked = limited(request, "invite", user=request.user)
    if blocked is not None:
        return blocked
    body = _body(request)
    invite = services.invite_member(
        ctx,
        ctx.partner_id,
        email=body.get("email"),
        role=body.get("role"),
        full_name=body.get("full_name", ""),
    )
    return JsonResponse(
        # The same answer whether the address was new or known.
        {"id": str(invite.member.id), "status": "invited"},
        status=201,
    )


def _member_in_partner(ctx: TenantContext, member_id: UUID) -> None:
    # Active or paused: an administrator may act on both (the service narrows
    # each action to the state it applies to). Removed members are history.
    if not panel_selectors.has_listed_member(ctx.partner_id, member_id):
        raise PartnerRecordNotFound("No such member.", member_id=str(member_id))


@partner_view(["PATCH", "DELETE"])
def member_view(
    request: HttpRequest, ctx: TenantContext, member_id: UUID
) -> JsonResponse:
    _member_in_partner(ctx, member_id)
    if request.method == "DELETE":
        services.remove_member(ctx, ctx.partner_id, member_id=member_id)
        return JsonResponse({"id": str(member_id), "status": "removed"})
    member = services.change_member_role(
        ctx, ctx.partner_id, member_id=member_id, role=_body(request).get("role")
    )
    return JsonResponse({"id": str(member.id), "role": member.role})


@partner_view(["POST"])
def member_suspend_view(
    request: HttpRequest, ctx: TenantContext, member_id: UUID
) -> JsonResponse:
    _member_in_partner(ctx, member_id)
    member = services.suspend_member(ctx, ctx.partner_id, member_id=member_id)
    return JsonResponse({"id": str(member.id), "status": member.status})


@partner_view(["POST"])
def member_resume_view(
    request: HttpRequest, ctx: TenantContext, member_id: UUID
) -> JsonResponse:
    _member_in_partner(ctx, member_id)
    member = services.resume_member(ctx, ctx.partner_id, member_id=member_id)
    return JsonResponse({"id": str(member.id), "status": member.status})


@partner_view(["GET"])
def roles_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_network.roles(ctx))


@partner_view(["POST"])
def member_resend_view(
    request: HttpRequest, ctx: TenantContext, member_id: UUID
) -> JsonResponse:
    blocked = limited(request, "invite", user=request.user)
    if blocked is not None:
        return blocked
    _member_in_partner(ctx, member_id)
    sent = services.resend_member_setup(ctx, ctx.partner_id, member_id=member_id)
    return JsonResponse({"id": str(member_id), "sent": sent})


# --- settings, authentication, communication -----------------------------------------------------


@partner_view(["GET", "PUT"])
def profile_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(profile.get_own_profile(ctx))
    body = _body(request)
    expected = body.get("expected_version")
    data = {k: v for k, v in body.items() if k != "expected_version"}
    return JsonResponse(
        profile.update_own_profile(ctx, expected_version=expected, data=data)
    )


@partner_view(["GET"])
def authentication_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.authentication(ctx))


@partner_view(["GET"])
def communication_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.communication(ctx))


@partner_view(["GET"])
def channels_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_portal.channel_manager(ctx))


# --- translations ------------------------------------------------------------------------------


@partner_view(["GET", "PUT"])
def translations_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(
            translations.overview(ctx, locale=request.GET.get("locale") or "en")
        )
    blocked = limited(request, "partner_translation", user=request.user)
    if blocked is not None:
        return blocked
    body = _body(request)
    return JsonResponse(
        translations.save(ctx, locale=body.get("locale"), strings=body.get("strings"))
    )


# --- API keys ------------------------------------------------------------------------------------


def _no_store(response: JsonResponse) -> JsonResponse:
    """A response that carries a secret is never cached, anywhere."""
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


@partner_view(["GET", "POST"])
def api_keys_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return _no_store(JsonResponse(api_keys.overview(ctx)))
    blocked = limited(request, "partner_apikey", user=request.user)
    if blocked is not None:
        return blocked
    body = _body(request)
    issued = api_keys.create(
        ctx,
        name=body.get("name"),
        scopes=body.get("scopes"),
        expires_in_days=body.get("expires_in_days"),
    )
    return _no_store(
        JsonResponse({"key": issued.key, "secret": issued.secret}, status=201)
    )


@partner_view(["POST"])
def api_key_rotate_view(
    request: HttpRequest, ctx: TenantContext, key_id: UUID
) -> JsonResponse:
    blocked = limited(request, "partner_apikey", user=request.user)
    if blocked is not None:
        return blocked
    issued = api_keys.rotate(
        ctx, key_id=key_id, expires_in_days=_body(request).get("expires_in_days")
    )
    return _no_store(JsonResponse({"key": issued.key, "secret": issued.secret}))


@partner_view(["POST"])
def api_key_revoke_view(
    request: HttpRequest, ctx: TenantContext, key_id: UUID
) -> JsonResponse:
    blocked = limited(request, "partner_apikey", user=request.user)
    if blocked is not None:
        return blocked
    return _no_store(JsonResponse({"key": api_keys.revoke(ctx, key_id=key_id)}))


# --- help: notifications and support ----------------------------------------------------------------


@partner_view(["GET"])
def notifications_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        partner_help.notifications(ctx, view=request.GET.get("view") or "all")
    )


@partner_view(["POST"])
def notifications_read_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        partner_help.mark_read(
            ctx, ids=body.get("ids"), everything=body.get("all") is True
        )
    )


@partner_view(["GET", "POST"])
def support_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """GET: where support is. POST (ADR-053): send a request to it."""
    if request.method == "POST":
        blocked = limited(request, "partner_support", user=request.user)
        if blocked is not None:
            return blocked
        body = _body(request)
        return JsonResponse(
            partner_help.send_support_request(
                ctx,
                subject=body.get("subject"),
                message=body.get("message"),
                priority=body.get("priority", "normal"),
                workspace=body.get("workspace", ""),
            ),
            status=201,
        )
    return JsonResponse(partner_help.support(ctx))


# --- growing the partnership: upgrade requests (ADR-049) -----------------------------------------


def _upgrade_limited(request: HttpRequest) -> JsonResponse | None:
    return limited(request, "partner_upgrade", user=request.user)


@partner_view(["GET"])
def upgrade_state_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Where this partner stands and what it may ask for. Read-only."""
    return JsonResponse(upgrades.state(ctx))


@partner_view(["POST"])
def upgrade_start_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Open an upgrade request on this partner's own account. It grants nothing."""
    blocked = _upgrade_limited(request)
    if blocked is not None:
        return blocked
    return JsonResponse(
        upgrades.start(ctx, to=_body(request).get("experience")), status=201
    )


@partner_view(["PUT"])
def upgrade_section_view(
    request: HttpRequest, ctx: TenantContext, request_id: UUID, section: str
) -> JsonResponse:
    blocked = _upgrade_limited(request)
    if blocked is not None:
        return blocked
    body = _body(request)
    return JsonResponse(
        upgrades.save_section(
            ctx,
            request_id,
            section=section,
            data=body.get("data"),
            expected_version=body.get("expected_version"),
        )
    )


@partner_view(["POST"])
def upgrade_submit_view(
    request: HttpRequest, ctx: TenantContext, request_id: UUID
) -> JsonResponse:
    blocked = _upgrade_limited(request)
    if blocked is not None:
        return blocked
    return JsonResponse(
        upgrades.submit(
            ctx, request_id, expected_version=_body(request).get("expected_version")
        )
    )


@partner_view(["POST"])
def upgrade_cancel_view(
    request: HttpRequest, ctx: TenantContext, request_id: UUID
) -> JsonResponse:
    blocked = _upgrade_limited(request)
    if blocked is not None:
        return blocked
    return JsonResponse(upgrades.cancel(ctx, request_id))
