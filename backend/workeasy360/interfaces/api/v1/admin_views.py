"""The Super Admin API (Phase 7): ``/api/v1/admin/...``.

Thin adapters over ``commercial/controlplane`` and the partner/commission
services. Every handler: authenticates the session, requires ``User.is_staff``
as a precondition, builds the staff context, calls **one** service and shapes
the result. The service authorizes (an explicit ``platform.staff_*``
permission; dangerous ones are refused under impersonation) and owns the transaction, so a
refusal raised *before or inside* the service leaves nothing behind.

Inputs are read field by field - there is no generic serializer, so a client
cannot set ``organization``, ``status``, ``approved_by``, an amount or any audit
field by adding it to a body (mass assignment, Task 114). Money is never
accepted from a body except where a staff member states an adjustment, which is
an integer of minor units checked by the service.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.debug import sensitive_variables

from workeasy360.commercial.commissions import ledger, payouts, rules
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane import customers as cp_customers
from workeasy360.commercial.controlplane import dashboard as cp_dashboard
from workeasy360.commercial.controlplane import diagnostics as cp_diagnostics
from workeasy360.commercial.controlplane import (
    partner_console,
    partner_detail,
    partner_onboarding,
    partner_upgrades,
    reseller_setup,
    white_label_setup,
)
from workeasy360.commercial.controlplane import reads as cp_reads
from workeasy360.commercial.controlplane import security as cp_security
from workeasy360.commercial.controlplane import subscriptions as cp_subscriptions
from workeasy360.commercial.partners import activation
from workeasy360.commercial.partners import branding as partner_branding
from workeasy360.commercial.partners import domains as partner_domains
from workeasy360.commercial.partners import experience as partner_experience
from workeasy360.commercial.partners import profile as partner_profile
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners import services as partner_services
from workeasy360.commercial.partners import upgrades as partner_upgrade_requests
from workeasy360.commercial.partners.domain import clean_text
from workeasy360.contracts.enums import (
    ChangeTiming,
    PartnerLinkStatus,
    PartnerStatus,
    SubscriptionStatus,
)
from workeasy360.contracts.errors import (
    CatalogValidationError,
    PartnerValidationError,
    PermissionDenied,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    page_response,
    query_page_args,
    staff_view,
)
from workeasy360.platform.authz.control_plane import acting_scope

_body = legacy._body
_required = legacy._required
_uuid = legacy._uuid
_enum = legacy._enum
_int = legacy._int
_flag = legacy._flag


def _idempotency(request: HttpRequest, body: dict[str, Any]) -> str:
    return legacy._idempotency_key(request, body)


def _optional_uuid(request: HttpRequest, name: str) -> UUID | None:
    raw = request.GET.get(name)
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise CatalogValidationError(f"{name} is not a valid id.", field=name) from exc


def _datetime(body: dict[str, Any], field: str) -> datetime:
    raw = _required(body, field)
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogValidationError(
            f"{field} is not a valid timestamp.", field=field
        ) from exc
    if moment.tzinfo is None:
        raise CatalogValidationError(f"{field} needs a timezone.", field=field)
    return moment


# --- session, dashboard ---------------------------------------------------


@staff_view(["GET"])
def session_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Who the staff member is, and which control-plane permissions they hold -
    the admin shell's navigation. The backend re-checks every request.

    **This is the answer to "may this person enter the Admin Console?"** The
    sign-in page lets a person say they *intend* to sign in as an
    administrator; that choice reaches no server and grants nothing. What
    decides is this endpoint: a staff identity (``is_staff``), a signed-in session
    (``staff_view``), and at least one ``StaffGrant``
    in force, read live. A staff identity whose every grant was revoked or has
    expired is refused here on its very next request - the same rule as the
    session's ``consoles.staff`` flag - instead of being shown an empty
    console. Django's ``is_superuser`` is never consulted.
    """
    permissions = cp_security.my_permissions(ctx)
    if not permissions:
        raise PermissionDenied(
            "Not permitted.", permission="platform.staff", reason="no_staff_grant"
        )
    return JsonResponse({"is_staff": True, "permissions": permissions})


@staff_view(["GET"])
def dashboard_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(cp_dashboard.summary(ctx))


@staff_view(["GET"])
def audit_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return page_response(
        cp_dashboard.control_plane_audit(
            ctx,
            action_prefix=request.GET.get("action") or None,
            **query_page_args(request),
        )
    )


# --- customers ------------------------------------------------------------


@staff_view(["GET"])
def customers_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """The customer directory. Phase 9 adds commercial filters, which the
    Subscriptions and Entitlements pages use: ``subscription`` (a subscription
    status, or ``none``), ``access`` (``customer``/``trial``/``none``) and
    ``product`` (holds this product now)."""
    page = cp_customers.directory(
        ctx,
        search=request.GET.get("q") or None,
        status=request.GET.get("status") or None,
        subscription_status=_choice(
            request, "subscription", {s.value for s in SubscriptionStatus}
        ),
        access_class=_choice(request, "access", {"customer", "trial"}),
        product_code=_product_filter(request),
        **query_page_args(request),
    )
    return page_response(page)


def _choice(request: HttpRequest, name: str, allowed: set[str]) -> str | None:
    """A filter value from a closed set; ``none`` means "has none" (empty)."""
    raw = request.GET.get(name)
    if not raw:
        return None
    if raw == "none":
        return ""
    if raw not in allowed:
        raise CatalogValidationError(f"Unknown {name} filter.", field=name)
    return raw


def _product_filter(request: HttpRequest) -> str | None:
    from workeasy360.contracts.values.identifiers import ProductCode

    raw = request.GET.get("product")
    if not raw:
        return None
    try:
        return str(ProductCode(raw))
    except ValueError as exc:
        raise CatalogValidationError("Unknown product filter.", field="product") from exc


@staff_view(["GET"])
def customer_detail_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    return JsonResponse(cp_customers.detail(ctx, organization_id))


@staff_view(["POST"])
def customer_suspend_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        cp_customers.suspend_organization(ctx, organization_id, reason=body.get("reason"))
    )


@staff_view(["POST"])
def customer_reactivate_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID
) -> JsonResponse:
    body = _body(request)
    return JsonResponse(
        cp_customers.reactivate_organization(
            ctx, organization_id, reason=body.get("reason")
        )
    )


@staff_view(["POST"])
def customer_diagnose_view(
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


# --- staff subscription operations ---------------------------------------


def _sub_result(sub: Any) -> JsonResponse:
    return JsonResponse({"id": str(sub.id), "status": sub.status, "version": sub.version})


@staff_view(["POST"])
def subscription_suspend_view(
    request, ctx, organization_id: UUID, subscription_id: UUID
) -> JsonResponse:
    body = _body(request)
    return _sub_result(
        cp_subscriptions.suspend(
            ctx,
            organization_id,
            subscription_id,
            reason=body.get("reason"),
            idempotency_key=_idempotency(request, body),
            expected_version=_int(body, "expected_version"),
        )
    )


@staff_view(["POST"])
def subscription_resume_view(
    request, ctx, organization_id: UUID, subscription_id: UUID
) -> JsonResponse:
    body = _body(request)
    return _sub_result(
        cp_subscriptions.resume(
            ctx,
            organization_id,
            subscription_id,
            reason=body.get("reason"),
            idempotency_key=_idempotency(request, body),
            expected_version=_int(body, "expected_version"),
        )
    )


@staff_view(["POST"])
def subscription_activate_view(
    request, ctx, organization_id: UUID, subscription_id: UUID
) -> JsonResponse:
    body = _body(request)
    return _sub_result(
        cp_subscriptions.activate(
            ctx,
            organization_id,
            subscription_id,
            reason=body.get("reason"),
            idempotency_key=_idempotency(request, body),
            expected_version=_int(body, "expected_version"),
        )
    )


@staff_view(["POST"])
def subscription_cancel_view(
    request, ctx, organization_id: UUID, subscription_id: UUID
) -> JsonResponse:
    body = _body(request)
    return _sub_result(
        cp_subscriptions.cancel(
            ctx,
            organization_id,
            subscription_id,
            reason=body.get("reason"),
            idempotency_key=_idempotency(request, body),
            immediate=_flag(body, "immediate"),
            expected_version=_int(body, "expected_version"),
        )
    )


@staff_view(["POST"])
def item_change_plan_view(
    request, ctx, organization_id: UUID, item_id: UUID
) -> JsonResponse:
    body = _body(request)
    item = cp_subscriptions.change_plan(
        ctx,
        organization_id,
        item_id,
        to_plan_version_id=_uuid(body, "to_plan_version_id"),
        reason=body.get("reason"),
        idempotency_key=_idempotency(request, body),
        timing=_enum(body, "timing", ChangeTiming, "") or None,
        expected_version=_int(body, "expected_version"),
    )
    return JsonResponse(
        {"item_id": str(item.id), "plan_code": item.plan_code}, status=201
    )


@staff_view(["POST"])
def item_change_quantity_view(
    request, ctx, organization_id: UUID, item_id: UUID
) -> JsonResponse:
    body = _body(request)
    item = cp_subscriptions.change_quantity(
        ctx,
        organization_id,
        item_id,
        quantity=_int(body, "quantity"),
        reason=body.get("reason"),
        idempotency_key=_idempotency(request, body),
        expected_version=_int(body, "expected_version"),
    )
    return JsonResponse({"item_id": str(item.id), "quantity": item.quantity}, status=201)


@staff_view(["POST"])
def entitlement_override_view(request, ctx, organization_id: UUID) -> JsonResponse:
    body = _body(request)
    grant = cp_subscriptions.grant_entitlement_override(
        ctx,
        organization_id,
        product_code=str(body.get("product_code") or ""),
        feature_key=str(body.get("feature_key") or ""),
        value=body.get("value"),
        reason=body.get("reason"),
        expires_at=_datetime(body, "expires_at"),
    )
    return JsonResponse({"id": str(grant.id)}, status=201)


@staff_view(["POST"])
def entitlement_override_revoke_view(
    request, ctx, organization_id: UUID, grant_id: UUID
) -> JsonResponse:
    body = _body(request)
    cp_subscriptions.revoke_entitlement_override(
        ctx, organization_id, grant_id, reason=body.get("reason")
    )
    return JsonResponse({"revoked": True})


# --- catalog and pricing: the existing commercial core, mounted here ----------
#
# The catalog services already exist and are the only implementation. These
# views call the *same handlers* the Phase 2 internal routes used, with the
# staff context in place of a tenant one - there is no second catalog code path.


def _catalog(handler: Any, methods: list[str]) -> Any:
    @staff_view(methods)
    def view(request: HttpRequest, ctx: TenantContext, **kwargs: Any) -> HttpResponse:
        request.tenant_context = ctx  # type: ignore[attr-defined]
        with acting_scope(ctx):
            return handler(request, "", **kwargs)

    view.__name__ = f"admin_{handler.__name__}"
    return view


catalog_products_view = _catalog(legacy.products_view, ["GET", "POST"])
catalog_product_status_view = _catalog(legacy.product_status_view, ["POST"])
catalog_features_view = _catalog(legacy.features_view, ["POST"])
catalog_plans_view = _catalog(legacy.plans_view, ["POST"])
catalog_plan_status_view = _catalog(legacy.plan_status_view, ["POST"])
catalog_plan_versions_view = _catalog(legacy.plan_versions_view, ["POST"])
catalog_bundles_view = _catalog(legacy.bundles_view, ["POST"])
catalog_bundle_versions_view = _catalog(legacy.bundle_versions_view, ["POST"])
catalog_price_books_view = _catalog(legacy.price_books_view, ["POST"])
catalog_prices_view = _catalog(legacy.prices_view, ["POST"])


# --- partners -------------------------------------------------------------


def _partner_json(row: Any) -> JsonResponse:
    return JsonResponse(partner_selectors.partner_dict(row))


@staff_view(["GET"])
def partner_capabilities_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_services.capability_presentation(ctx))


@staff_view(["GET"])
def partners_overview_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Factual counts for the Partners overview (Phase 10a)."""
    return JsonResponse(partner_console.overview(ctx))


@staff_view(["GET"])
def partners_recent_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Recent activity for the Partner Control Center dashboard: a few rows of
    existing data per section; money sections are ``null`` without the
    commission permission."""
    return JsonResponse(partner_console.recent(ctx))


@staff_view(["GET"])
def partner_resellers_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """The reseller list: partners wearing the Reseller label (a filter over
    capability grants), or every partner with ``scope=all``."""
    return page_response(
        partner_console.resellers_page(
            ctx,
            query=request.GET.get("q") or None,
            status=request.GET.get("status") or None,
            product=request.GET.get("product") or None,
            capability=request.GET.get("capability") or None,
            scope=request.GET.get("scope") or None,
            label=request.GET.get("type") or None,
            profile=request.GET.get("profile") or None,
            **query_page_args(request),
        )
    )


@staff_view(["GET"])
def partner_customers_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID | None = None
) -> JsonResponse:
    """Customers with a partner relationship: one partner's (with its id in the
    path) or every partner's. The owner column is the factual activation state
    of the invitation - never the token."""
    return page_response(
        partner_detail.customers_page(
            ctx,
            partner_id=partner_id,
            query=request.GET.get("q") or None,
            status=request.GET.get("status") or None,
            activation=request.GET.get("activation") or None,
            subscription=request.GET.get("subscription") or None,
            **query_page_args(request),
        )
    )


@staff_view(["POST"])
def owner_invitation_resend_view(
    request, ctx, partner_id: UUID, organization_id: UUID
) -> JsonResponse:
    """Send a customer's owner a new activation link (the old one dies)."""
    blocked = limited(request, "owner_invitation_send", user=request.user)
    if blocked is not None:
        return blocked
    invite = activation.resend(ctx, organization_id, partner_id=partner_id)
    return JsonResponse(activation.public_state(invite))


@staff_view(["POST"])
def owner_invitation_revoke_view(
    request, ctx, partner_id: UUID, organization_id: UUID
) -> JsonResponse:
    blocked = limited(request, "owner_invitation_send", user=request.user)
    if blocked is not None:
        return blocked
    invite = activation.revoke(ctx, organization_id, partner_id=partner_id)
    return JsonResponse(activation.public_state(invite))


@staff_view(["GET"])
def partner_summary_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    return JsonResponse(partner_detail.summary(ctx, partner_id))


@staff_view(["GET"])
def partner_pricing_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    return JsonResponse(partner_detail.pricing(ctx, partner_id))


@staff_view(["GET"])
def partner_activity_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    return page_response(
        partner_detail.activity(ctx, partner_id, **query_page_args(request))
    )


@staff_view(["GET"])
def partner_referrals_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """One partner's referral codes and referrals, for staff."""
    return JsonResponse(
        partner_detail.referrals(ctx, partner_id, **query_page_args(request))
    )


@staff_view(["POST"])
def partner_referral_code_create_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """Staff mint a referral code for a partner that holds the referral
    capability (Complete Your Profile). The code is the partner's own."""
    from workeasy360.commercial.commissions import referrals as referral_services

    code = referral_services.create_referral_code_for_partner(
        ctx, partner_id, label=_body(request).get("label", "")
    )
    return JsonResponse(
        {
            "id": str(code.id),
            "code": code.code,
            "label": code.label,
            "status": code.status,
            "created_at": code.created_at.isoformat(),
        },
        status=201,
    )


@staff_view(["POST"])
def partner_guest_view_start_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """All Partners → Guest login: open this partner's portal read-only, for a
    reason, for a limited time (``commercial.partners.guest``)."""
    from workeasy360.commercial.partners import guest
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    view = guest.start(
        ctx,
        partner_id,
        reason=_body(request).get("reason", ""),
        sign_in_id=request.session.get(SESSION_SIGN_IN_ID_KEY),
    )
    return JsonResponse(
        {**view.as_json(), "portal_path": guest.portal_path(view.partner_id)},
        status=201,
    )


@staff_view(["GET"])
def partner_white_label_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """Partners holding branding or domain capability, with brand and domain."""
    return page_response(
        partner_console.white_label_page(
            ctx,
            query=request.GET.get("q") or None,
            status=request.GET.get("status") or None,
            domain_status=request.GET.get("domain_status") or None,
            brand=request.GET.get("brand") or None,
            **query_page_args(request),
        )
    )


@staff_view(["GET", "PUT"])
def partner_brand_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """A partner's brand: read it, or (staff) set it for the
    partner - through the very command the partner's own portal uses."""
    if request.method == "GET":
        return JsonResponse(partner_detail.brand(ctx, partner_id))
    body = _body(request)
    return JsonResponse(
        partner_branding.update_brand(
            ctx,
            partner_id=partner_id,
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


@staff_view(["GET", "PUT"])
def partner_profile_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """A partner's business details, contacts, support details, payout
    preferences and staff notes: read them, or (staff) change
    them. Staff only - a partner portal has no route to this."""
    if request.method == "GET":
        return JsonResponse(partner_profile.get_profile(ctx, partner_id))
    body = _body(request)
    return JsonResponse(
        partner_profile.update_profile(
            ctx,
            partner_id,
            expected_version=_int(body, "expected_version"),
            data={k: v for k, v in body.items() if k != "expected_version"},
        )
    )


@staff_view(["GET"])
def partner_white_label_setup_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """Everything the white-label wizard shows for one partner, in one read."""
    return JsonResponse(white_label_setup.setup(ctx, partner_id))


@staff_view(["POST"])
def partner_white_label_enable_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """Give the partner the white-label pair of capabilities (never a new
    account): the same two grants, made together."""
    body = _body(request)
    granted = partner_services.enable_white_label(
        ctx, partner_id, reason=body.get("reason")
    )
    return JsonResponse({"granted": granted}, status=201 if granted else 200)


@staff_view(["POST"])
def partner_logo_upload_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """A logo as a raw request body, exactly as the partner portal sends one:
    the type comes from ``Content-Type`` and is checked against the bytes by
    the files platform; size is capped before and after; SVG is refused."""
    file_id = partner_branding.upload_logo(
        ctx,
        partner_id=partner_id,
        content=request.body,
        content_type=request.headers.get("Content-Type", ""),
    )
    return JsonResponse({"logo_file_id": str(file_id)}, status=201)


@staff_view(["GET"])
def partner_logo_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> HttpResponse:
    """The stored logo, for the staff preview. Resolved from the partner's own
    brand (there is no file id in the request), same-origin, and served with
    the same isolation headers as the public logo."""
    stored = partner_detail.brand(ctx, partner_id)["brand"]
    logo = partner_branding.public_logo(stored["logo_file_id"] if stored else None)
    if logo is None:
        return HttpResponse(status=404)
    data, content_type = logo
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Content-Disposition"] = "inline"
    return response


def _domain_json(row: Any) -> dict[str, Any]:
    return partner_selectors.domain_dict(
        row, txt_name=partner_domains.verification_record_name(row.hostname)
    )


@staff_view(["POST"])
def partner_domain_request_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    body = _body(request)
    row = partner_domains.request_domain(
        ctx, hostname=body.get("hostname"), partner_id=partner_id
    )
    return JsonResponse(_domain_json(row), status=201)


@staff_view(["POST"])
def domain_verify_view(request, ctx, domain_id: UUID) -> JsonResponse:
    """Look for the DNS proof now. Rate limited per staff member and address."""
    limited_response = limited(request, "domain_verify", user=request.user)
    if limited_response is not None:
        return limited_response
    return JsonResponse(
        _domain_json(partner_domains.verify_domain(ctx, domain_id=domain_id))
    )


@staff_view(["POST"])
def domain_activate_view(request, ctx, domain_id: UUID) -> JsonResponse:
    """Serve the brand on a **verified** domain. Never from any other state."""
    return JsonResponse(
        _domain_json(partner_domains.activate_domain(ctx, domain_id=domain_id))
    )


@staff_view(["GET"])
def partner_console_products_view(
    request: HttpRequest, ctx: TenantContext
) -> JsonResponse:
    """Products and their sellable plans, for filters and the wizard."""
    return JsonResponse(partner_console.console_products(ctx))


@staff_view(["GET", "POST"])
def partners_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return page_response(
            cp_reads.partners(
                ctx,
                status=request.GET.get("status") or None,
                query=request.GET.get("q") or None,
                **query_page_args(request),
            )
        )
    body = _body(request)
    partner = partner_services.create_partner(
        ctx,
        code=body.get("code"),
        display_name=body.get("display_name"),
        payout_currency=body.get("payout_currency"),
        legal_name=body.get("legal_name", ""),
        contact_name=body.get("contact_name", ""),
        contact_email=body.get("contact_email", ""),
    )
    return JsonResponse(partner_selectors.partner_dict(partner), status=201)


@staff_view(["GET", "PATCH"])
def partner_detail_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(cp_reads.partner(ctx, partner_id))
    body = _body(request)
    return _partner_json(
        partner_services.update_partner(
            ctx,
            partner_id,
            expected_version=_int(body, "expected_version"),
            display_name=body.get("display_name"),
            legal_name=body.get("legal_name"),
            contact_name=body.get("contact_name"),
            contact_email=body.get("contact_email"),
        )
    )


@staff_view(["POST"])
def partner_status_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    body = _body(request)
    return _partner_json(
        partner_services.set_partner_status(
            ctx,
            partner_id,
            target=_enum(body, "status", PartnerStatus),
            reason=body.get("reason"),
            expected_version=_int(body, "expected_version"),
        )
    )


@staff_view(["POST"])
def partner_capability_grant_view(request, ctx, partner_id: UUID) -> JsonResponse:
    body = _body(request)
    expires = _datetime(body, "expires_at") if body.get("expires_at") else None
    grant = partner_services.grant_capability(
        ctx,
        partner_id,
        capability=body.get("capability"),
        reason=body.get("reason"),
        expires_at=expires,
    )
    return JsonResponse({"id": str(grant.id), "capability": grant.capability}, status=201)


@staff_view(["POST"])
def partner_capability_revoke_view(request, ctx, partner_id: UUID) -> JsonResponse:
    body = _body(request)
    grant = partner_services.revoke_capability(
        ctx, partner_id, capability=body.get("capability"), reason=body.get("reason")
    )
    return JsonResponse({"id": str(grant.id), "status": grant.status})


@staff_view(["POST"])
def partner_member_add_view(request, ctx, partner_id: UUID) -> JsonResponse:
    body = _body(request)
    member = partner_services.add_member(
        ctx, partner_id, email=body.get("email"), role=body.get("role")
    )
    return JsonResponse({"id": str(member.id), "role": member.role}, status=201)


@staff_view(["POST"])
def partner_member_remove_view(
    request, ctx, partner_id: UUID, member_id: UUID
) -> JsonResponse:
    member = partner_services.remove_member(ctx, partner_id, member_id=member_id)
    return JsonResponse({"id": str(member.id), "status": member.status})


@staff_view(["POST"])
def partner_administrator_invite_view(request, ctx, partner_id: UUID) -> JsonResponse:
    """Add a person to a partner, creating their account if the address has
    none. The answer is the same whether the address was new or known."""
    body = _body(request)
    timing = body.get("send_invitation", "now")
    if timing not in ("now", "on_activation"):
        raise CatalogValidationError(
            "send_invitation must be 'now' or 'on_activation'.", field="send_invitation"
        )
    names = [
        clean_text(body.get(k), field=k, max_length=100, required=False)
        for k in ("first_name", "last_name")
    ]
    invite = partner_services.invite_member(
        ctx,
        partner_id,
        email=body.get("email"),
        role=body.get("role"),
        full_name=" ".join(n for n in names if n),
        send_link=timing == "now",
    )
    return JsonResponse(
        {"id": str(invite.member.id), "role": invite.member.role, "invited": True},
        status=201,
    )


@staff_view(["POST"])
def partner_member_role_view(
    request, ctx, partner_id: UUID, member_id: UUID
) -> JsonResponse:
    """Move a partner user between administrator and viewer."""
    body = _body(request)
    member = partner_services.change_member_role(
        ctx, partner_id, member_id=member_id, role=body.get("role")
    )
    return JsonResponse({"id": str(member.id), "role": member.role})


@staff_view(["POST"])
def partner_member_resend_setup_view(
    request, ctx, partner_id: UUID, member_id: UUID
) -> JsonResponse:
    sent = partner_services.resend_member_setup(ctx, partner_id, member_id=member_id)
    return JsonResponse({"sent": sent})


@staff_view(["POST"])
def partner_price_view(request, ctx, partner_id: UUID) -> JsonResponse:
    """Set the price a customer of this partner pays for one plan and cycle."""
    body = _body(request)
    price = partner_services.set_partner_price(
        ctx,
        partner_id,
        plan_version_id=_uuid(body, "plan_version_id"),
        interval=body.get("interval"),
        amount_minor=body.get("amount_minor"),
        currency=body.get("currency") or None,
    )
    return JsonResponse(
        {
            "id": str(price.id),
            "interval": price.interval,
            "amount_minor": price.amount_minor,
        },
        status=201,
    )


@staff_view(["POST"])
def reseller_review_view(request, ctx) -> JsonResponse:
    """The Add Reseller review: validates the whole draft and computes every
    financial figure on the server. Writes nothing."""
    return JsonResponse(reseller_setup.review(ctx, _body(request)))


# --- Add Partner with a login (ADR-047) -----------------------------------------


def _no_store(response: JsonResponse) -> JsonResponse:
    """Nothing about creating a login is kept by a browser or a proxy."""
    response["Cache-Control"] = "no-store"
    return response


@staff_view(["POST"])
@sensitive_variables()
def partner_credentials_check_view(request, ctx) -> JsonResponse:
    """Can this email and password be used? Writes nothing, echoes no password,
    and says only whether the address already has an account."""
    blocked = limited(request, "partner_onboard", user=request.user)
    if blocked is not None:
        return blocked
    return _no_store(
        JsonResponse(partner_onboarding.credentials_check(ctx, _body(request)))
    )


@staff_view(["POST"])
@sensitive_variables()
def partner_create_view(request, ctx) -> JsonResponse:
    """Create the partner (a draft) and its administrator's login in one
    transaction. The answer names the partner, the type and the administrator's
    email - never a password."""
    blocked = limited(request, "partner_onboard", user=request.user)
    if blocked is not None:
        return blocked
    created = partner_onboarding.create(ctx, _body(request))
    return _no_store(
        JsonResponse(
            {
                "id": str(created.partner_id),
                "code": created.code,
                "display_name": created.display_name,
                "status": created.status,
                "experience": created.experience,
                "default_experience": created.default_experience,
                "administrator": {
                    "email": created.administrator_email,
                    "account_created": created.account_created,
                },
                "capabilities": created.capabilities,
                "payout_currency": created.payout_currency,
            },
            status=201,
        )
    )


@staff_view(["POST"])
def partner_experience_view(request, ctx, partner_id: UUID) -> JsonResponse:
    """Change a partner's experience after creation: ``grant`` (add one),
    ``retire`` (withdraw what only that one needs), ``transition`` (move to one,
    optionally retiring another) or ``default`` (choose the landing workspace).
    Additive by default; no history is deleted."""
    body = _body(request)
    action = body.get("action")
    reason = body.get("reason")
    if action == "default":
        partner = partner_experience.set_default_experience(
            ctx, partner_id, experience=body.get("experience"), reason=reason
        )
        return JsonResponse({"default_experience": partner.default_experience})
    if action == "grant":
        result = partner_experience.grant_experience(
            ctx,
            partner_id,
            experience=body.get("experience"),
            reason=reason,
            make_default=_flag(body, "make_default"),
            products=body.get("products") or (),
        )
    elif action == "retire":
        result = partner_experience.retire_experience(
            ctx, partner_id, experience=body.get("experience"), reason=reason
        )
    elif action == "transition":
        result = partner_experience.transition_experience(
            ctx,
            partner_id,
            to=body.get("experience"),
            retire=body.get("retire") or None,
            products=body.get("products") or (),
            reason=reason,
        )
    else:
        raise CatalogValidationError(
            "action must be grant, retire, transition or default.", field="action"
        )
    return JsonResponse(
        {
            "experience": result.experience,
            "capabilities_granted": result.capabilities_granted,
            "capabilities_revoked": result.capabilities_revoked,
            "products_granted": result.products_granted,
            "products_revoked": result.products_revoked,
            "default_experience": result.default_experience,
        }
    )


@staff_view(["GET"])
def partner_upgrade_queue_view(request, ctx) -> JsonResponse:
    """The review queue: upgrade requests across partners (ADR-049)."""
    return JsonResponse(
        partner_upgrade_requests.staff_queue(
            ctx, status=request.GET.get("status") or None
        )
    )


@staff_view(["GET"])
def partner_upgrades_view(request, ctx, partner_id: UUID) -> JsonResponse:
    """One partner's upgrade standing: level, request, checklist and history."""
    return JsonResponse(partner_upgrade_requests.staff_for_partner(ctx, partner_id))


@staff_view(["POST"])
def partner_upgrade_decision_view(
    request, ctx, partner_id: UUID, request_id: UUID
) -> JsonResponse:
    """Decide an upgrade request: ``approve`` (grants the bundle - every
    server-side rule must pass), ``reject`` or ``request_info``. Staff and
    ``platform.staff_partner.manage``: nothing a partner sends
    reaches this."""
    body = _body(request)
    action = body.get("action")
    if action == "approve":
        result = partner_upgrades.approve(
            ctx,
            partner_id,
            request_id,
            products=body.get("products"),
            commercial_reference=body.get("commercial_reference", ""),
            note=body.get("note", ""),
        )
    elif action == "reject":
        result = partner_upgrade_requests.reject(
            ctx, partner_id, request_id, note=body.get("note")
        )
    elif action == "request_info":
        result = partner_upgrade_requests.request_info(
            ctx, partner_id, request_id, note=body.get("note")
        )
    else:
        raise CatalogValidationError(
            "action must be approve, reject or request_info.", field="action"
        )
    return JsonResponse(result)


@staff_view(["POST"])
def partner_member_password_reset_view(
    request, ctx, partner_id: UUID, member_id: UUID
) -> JsonResponse:
    """Send a partner's person the ordinary forgotten-password link. The current
    password is never read or shown, and none is set by this action."""
    blocked = limited(request, "partner_password_reset", user=request.user)
    if blocked is not None:
        return blocked
    sent = partner_services.send_member_password_reset(
        ctx, partner_id, member_id=member_id
    )
    return JsonResponse({"sent": sent})


@staff_view(["GET", "POST"])
def partner_commission_view(request, ctx, partner_id: UUID) -> JsonResponse:
    """What a partner earns (GET), and assigning it a rate (POST)."""
    if request.method == "GET":
        return JsonResponse(cp_reads.partner_commission(ctx, partner_id))
    body = _body(request)
    result = rules.assign_partner_commission(
        ctx,
        partner_id,
        rate_bp=body.get("rate_bp"),
        reason=body.get("reason"),
        expected_version=body.get("expected_version"),
        product_code=body.get("product_code", ""),
        effective_from=_datetime(body, "effective_from")
        if body.get("effective_from")
        else None,
    )
    rule = result.rule
    return JsonResponse(
        {
            "id": str(rule.id),
            "code": rule.code,
            "version": rule.version,
            "rate_bp": rule.rate_bp,
            "effective_from": rule.effective_from.isoformat(),
            "created": result.created,
            "previous_rate_bp": result.previous_rate_bp,
        },
        status=201 if result.created else 200,
    )


@staff_view(["POST"])
def partner_product_grant_view(request, ctx, partner_id: UUID) -> JsonResponse:
    body = _body(request)
    grant = partner_services.grant_product(
        ctx,
        partner_id,
        product_code=body.get("product_code"),
        plan_code=body.get("plan_code") or None,
        trial_allowed=body.get("trial_allowed"),
        portal_visible=body.get("portal_visible"),
    )
    return JsonResponse({"id": str(grant.id)}, status=201)


@staff_view(["PATCH"])
def partner_product_grant_update_view(
    request, ctx, partner_id: UUID, grant_id: UUID
) -> JsonResponse:
    """Change whether a granted product may be trialled by this partner's
    customers and whether it shows on its portal."""
    body = _body(request)
    grant = partner_services.update_product_grant(
        ctx,
        partner_id,
        grant_id=grant_id,
        trial_allowed=body.get("trial_allowed"),
        portal_visible=body.get("portal_visible"),
    )
    return JsonResponse(
        {
            "id": str(grant.id),
            "trial_allowed": grant.trial_allowed,
            "portal_visible": grant.portal_visible,
        }
    )


@staff_view(["POST"])
def partner_product_grant_revoke_view(
    request, ctx, partner_id: UUID, grant_id: UUID
) -> JsonResponse:
    grant = partner_services.revoke_product_grant(ctx, partner_id, grant_id=grant_id)
    return JsonResponse({"id": str(grant.id), "status": grant.status})


@staff_view(["POST"])
def partner_price_book_view(request, ctx, partner_id: UUID) -> JsonResponse:
    body = _body(request)
    book = partner_services.create_partner_price_book(
        ctx,
        partner_id,
        code=str(_required(body, "code")),
        name=str(_required(body, "name")),
        currency=_enum(body, "currency", CurrencyCode),
        priority=_int(body, "priority", 50) or 50,
    )
    return JsonResponse({"id": str(book.id), "code": book.code}, status=201)


@staff_view(["POST"])
def partner_link_view(request, ctx, partner_id: UUID) -> JsonResponse:
    body = _body(request)
    link = partner_services.link_customer(
        ctx, partner_id, _uuid(body, "organization_id"), reason=body.get("reason")
    )
    return JsonResponse({"id": str(link.id), "status": link.status}, status=201)


@staff_view(["POST"])
def partner_link_status_view(request, ctx, link_id: UUID) -> JsonResponse:
    body = _body(request)
    link = partner_services.change_link_status(
        ctx,
        link_id,
        target=_enum(body, "status", PartnerLinkStatus),
        reason=body.get("reason"),
    )
    return JsonResponse({"id": str(link.id), "status": link.status})


@staff_view(["GET"])
def partner_domains_view(request, ctx, partner_id: UUID) -> JsonResponse:
    return JsonResponse({"results": cp_reads.partner_domains_list(ctx, partner_id)})


@staff_view(["POST"])
def domain_disable_view(request, ctx, domain_id: UUID) -> JsonResponse:
    row = partner_domains.disable_domain(ctx, domain_id=domain_id)
    return JsonResponse({"id": str(row.id), "status": row.status})


# --- commissions and payouts ---------------------------------------------------


@staff_view(["GET", "POST"])
def commission_rules_view(request, ctx, **_kw: Any) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(
            {"results": cp_reads.rules_list(ctx, live_only=request.GET.get("all") != "1")}
        )
    body = _body(request)
    start = _datetime(body, "effective_from") if body.get("effective_from") else None
    rule = rules.create_rule_version(
        ctx,
        code=body.get("code"),
        rate_bp=body.get("rate_bp"),
        reason=body.get("reason"),
        product_code=body.get("product_code", ""),
        partner_id=_uuid(body, "partner_id", required=False),
        effective_from=start,
    )
    return JsonResponse(
        {"id": str(rule.id), "code": rule.code, "version": rule.version}, status=201
    )


@staff_view(["GET"])
def commission_accruals_view(request, ctx, **_kw: Any) -> JsonResponse:
    return page_response(
        cp_reads.accruals(
            ctx,
            partner_id=_optional_uuid(request, "partner_id"),
            kind=request.GET.get("kind") or None,
            unpaid=request.GET.get("unpaid") == "1",
            **query_page_args(request),
        )
    )


@staff_view(["POST"])
def commission_reverse_view(request, ctx, accrual_id: UUID) -> JsonResponse:
    body = _body(request)
    ledger.reverse_accrual(
        ctx,
        accrual_id,
        reason_code=body.get("reason_code"),
        reason=body.get("reason"),
        idempotency_key=_idempotency(request, body),
        amount_minor=_int(body, "amount_minor"),
    )
    return JsonResponse({"reversed": True}, status=201)


@staff_view(["POST"])
def commission_adjust_view(request, ctx, partner_id: UUID) -> JsonResponse:
    body = _body(request)
    row = ledger.record_adjustment(
        ctx,
        partner_id,
        amount_minor=body.get("amount_minor"),
        currency=body.get("currency"),
        reason=body.get("reason"),
        idempotency_key=_idempotency(request, body),
    )
    return JsonResponse({"id": str(row.id)}, status=201)


@staff_view(["GET"])
def commission_reconcile_view(request, ctx, **_kw: Any) -> JsonResponse:
    return JsonResponse(cp_reads.commission_reconciliation(ctx))


@staff_view(["GET", "POST"])
def payouts_view(request, ctx, **_kw: Any) -> JsonResponse:
    if request.method == "GET":
        return page_response(
            cp_reads.payouts_list(
                ctx,
                partner_id=_optional_uuid(request, "partner_id"),
                status=request.GET.get("status") or None,
                **query_page_args(request),
            )
        )
    body = _body(request)
    try:
        period_end = date.fromisoformat(str(_required(body, "period_end")))
    except ValueError as exc:
        raise PartnerValidationError(
            "period_end must be a date.", field="period_end"
        ) from exc
    payout = payouts.prepare_payout(
        ctx,
        partner_id=_uuid(body, "partner_id"),
        period_end=period_end,
        idempotency_key=_idempotency(request, body),
    )
    return JsonResponse(
        commission_selectors.payout_dict(payout, for_partner=False), status=201
    )


def _payout_action(
    func: Any, ctx: TenantContext, payout_id: UUID, body: dict[str, Any], **extra: Any
) -> JsonResponse:
    payout = func(
        ctx, payout_id, expected_version=_int(body, "expected_version"), **extra
    )
    return JsonResponse(commission_selectors.payout_dict(payout, for_partner=False))


@staff_view(["POST"])
def payout_ready_view(request, ctx, payout_id: UUID) -> JsonResponse:
    return _payout_action(payouts.mark_payout_ready, ctx, payout_id, _body(request))


@staff_view(["POST"])
def payout_approve_view(request, ctx, payout_id: UUID) -> JsonResponse:
    body = _body(request)
    return _payout_action(
        payouts.approve_payout, ctx, payout_id, body, reason=body.get("reason")
    )


@staff_view(["POST"])
def payout_paid_view(request, ctx, payout_id: UUID) -> JsonResponse:
    body = _body(request)
    return _payout_action(
        payouts.record_payout_paid, ctx, payout_id, body, reference=body.get("reference")
    )


@staff_view(["POST"])
def payout_cancel_view(request, ctx, payout_id: UUID) -> JsonResponse:
    body = _body(request)
    return _payout_action(
        payouts.cancel_payout, ctx, payout_id, body, reason=body.get("reason")
    )


@staff_view(["GET"])
def payout_reconcile_view(request, ctx, payout_id: UUID) -> JsonResponse:
    return JsonResponse(cp_reads.payout_reconciliation(ctx, payout_id))


# --- staff permissions -------------------------------------------------------


@staff_view(["GET", "POST"])
def security_grants_view(request, ctx, **_kw: Any) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse({"results": cp_security.list_grants(ctx)})
    body = _body(request)
    grant = cp_security.grant_permission(
        ctx,
        email=body.get("email"),
        permission=body.get("permission"),
        reason=body.get("reason"),
        expires_at=_datetime(body, "expires_at") if body.get("expires_at") else None,
    )
    return JsonResponse(
        {
            "id": str(grant.id),
            "permission": grant.permission,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        },
        status=201,
    )


@staff_view(["POST"])
def security_grant_revoke_view(request, ctx, grant_id: UUID) -> JsonResponse:
    body = _body(request)
    grant = cp_security.revoke_permission(
        ctx, grant_id=grant_id, reason=body.get("reason")
    )
    return JsonResponse({"id": str(grant.id), "revoked": grant.revoked_at is not None})


@staff_view(["GET", "POST"])
def security_roles_view(request, ctx, **_kw: Any) -> JsonResponse:
    """Named staff roles (Phase 9): GET the catalog with holders; POST grants
    every permission of one role to a staff member."""
    if request.method == "GET":
        return JsonResponse({"results": cp_security.role_catalog(ctx)})
    body = _body(request)
    return JsonResponse(
        cp_security.grant_role(
            ctx,
            email=body.get("email"),
            role=body.get("role"),
            reason=body.get("reason"),
            expires_at=_datetime(body, "expires_at") if body.get("expires_at") else None,
        ),
        status=201,
    )


@staff_view(["POST"])
def security_role_revoke_view(request, ctx, **_kw: Any) -> JsonResponse:
    body = _body(request)
    try:
        user_id = UUID(str(body.get("user_id")))
    except ValueError:
        return JsonResponse({"code": "not_found", "detail": "No such user."}, status=404)
    return JsonResponse(
        cp_security.revoke_role(
            ctx, user_id=user_id, role=body.get("role"), reason=body.get("reason")
        )
    )
