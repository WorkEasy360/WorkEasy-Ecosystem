"""The Admin Panel programme's endpoints (2026-11-09).

Thin adapters, like ``admin_views``: authenticate, read the fields that matter, call one
service, shape the result. Every service authorizes for itself with an explicit
``platform.staff_*`` permission, so nothing here decides who may do what.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.controlplane import (
    customer_admin,
    domains_console,
    overview,
    partner_applications,
    reports,
    search,
    wallet_console,
)
from workeasy360.commercial.controlplane import security as cp_security
from workeasy360.commercial.partners import kyc, support_desk
from workeasy360.commercial.payments import admin_reads as payment_reads
from workeasy360.commercial.payments import wallet_orders
from workeasy360.commercial.provisioning import setup as customer_setup
from workeasy360.commercial.subscriptions import admin_reads as subscription_reads
from workeasy360.commercial.wallets import admin as wallets_admin
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1.commercial_views import _body as read_json_body
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    partner_view,
    staff_view,
)
from workeasy360.platform.administration import help_content
from workeasy360.platform.observability.client_ip import client_ip

# --- dashboard ---------------------------------------------------------------------------


@staff_view(["GET"])
def dashboard_overview_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        overview.overview(
            ctx,
            raw_from=request.GET.get("from") or None,
            raw_to=request.GET.get("to") or None,
            days=request.GET.get("days") or None,
            currency=request.GET.get("currency") or None,
        )
    )


@staff_view(["GET"])
def dashboard_growth_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(overview.growth_trend(ctx, months=request.GET.get("months")))


@staff_view(["GET"])
def dashboard_money_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        overview.money_trend(
            ctx,
            months=request.GET.get("months"),
            currency=request.GET.get("currency") or None,
        )
    )


# --- search ------------------------------------------------------------------------------


@staff_view(["GET"])
def search_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(search.search(ctx, request.GET.get("q")))


# --- KYC: the review -----------------------------------------------------------------------


@staff_view(["GET", "POST"])
def kyc_requirements_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse({"results": kyc.requirements(ctx)})
    return JsonResponse(kyc.create_requirement(ctx, read_json_body(request)), status=201)


@staff_view(["PUT"])
def kyc_requirement_view(
    request: HttpRequest, ctx: TenantContext, requirement_id: UUID
) -> JsonResponse:
    return JsonResponse(kyc.update_requirement(ctx, requirement_id, read_json_body(request)))


@staff_view(["GET"])
def kyc_applications_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        kyc.queue(
            ctx,
            status=request.GET.get("status") or None,
            search=request.GET.get("q") or None,
            limit=request.GET.get("limit"),
            offset=request.GET.get("offset"),
        )
    )


@staff_view(["GET"])
def kyc_application_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    return JsonResponse(kyc.detail(ctx, partner_id))


@staff_view(["POST"])
def kyc_start_review_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    return JsonResponse(kyc.start_review(ctx, partner_id))


@staff_view(["POST"])
def kyc_decision_view(request: HttpRequest, ctx: TenantContext, partner_id: UUID) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        kyc.decide(
            ctx,
            partner_id,
            decision=body.get("decision"),
            reason=body.get("reason"),
            flagged_requirements=body.get("flagged_requirements"),
            experience=body.get("experience"),
            expected_version=body.get("expected_version"),
        )
    )


def _document_response(data: bytes, content_type: str, filename: str) -> HttpResponse:
    """A restricted document, opened in place and never cached or framed. The type
    is what the bytes were found to be, never what was declared, and the sandbox
    stops anything in it from running as this site."""
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in filename)[:120]
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{safe or "document"}"'
    response["Cache-Control"] = "no-store, private"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; object-src 'self'"
    return response


@staff_view(["GET"])
@never_cache
def kyc_document_content_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID, document_id: UUID
) -> HttpResponse:
    from workeasy360.platform.identity.devices import network_prefix

    return _document_response(
        *kyc.document_content(
            ctx, partner_id, document_id, ip_prefix=network_prefix(client_ip(request))
        )
    )


# --- KYC: the partner's side ---------------------------------------------------------------


@partner_view(["GET", "PUT"])
def partner_kyc_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    assert ctx.partner_id is not None
    if request.method == "GET":
        return JsonResponse(kyc.application(ctx, ctx.partner_id))
    body = read_json_body(request)
    return JsonResponse(
        kyc.update_details(
            ctx,
            ctx.partner_id,
            body.get("details"),
            expected_version=body.get("expected_version"),
        )
    )


@partner_view(["POST"])
def partner_kyc_documents_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """One document as a raw request body, like a partner's logo: the type comes from
    ``Content-Type`` and is checked against the bytes by the files platform."""
    assert ctx.partner_id is not None
    return JsonResponse(
        kyc.upload_document(
            ctx,
            ctx.partner_id,
            requirement_key=request.GET.get("requirement"),
            filename=request.GET.get("filename"),
            content=request.body,
            content_type=request.headers.get("Content-Type", ""),
        ),
        status=201,
    )


@partner_view(["DELETE"])
def partner_kyc_document_view(
    request: HttpRequest, ctx: TenantContext, document_id: UUID
) -> JsonResponse:
    assert ctx.partner_id is not None
    kyc.remove_document(ctx, ctx.partner_id, document_id)
    return JsonResponse({"ok": True})


@partner_view(["GET"])
@never_cache
def partner_kyc_document_content_view(
    request: HttpRequest, ctx: TenantContext, document_id: UUID
) -> HttpResponse:
    assert ctx.partner_id is not None
    return _document_response(*kyc.document_content(ctx, ctx.partner_id, document_id))


@partner_view(["POST"])
def partner_kyc_submit_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    assert ctx.partner_id is not None
    return JsonResponse(kyc.submit(ctx, ctx.partner_id))


# --- the public application --------------------------------------------------------------


@require_http_methods(["POST"])
@never_cache
def partner_application_view(request: HttpRequest) -> JsonResponse:
    """The same answer for every outcome, as signup gives: nothing here says whether
    an address is registered."""
    body: dict[str, Any] = read_json_body(request)
    email = str(body.get("email") or "").strip()
    blocked = limited(request, "signup", account_email=email or None)
    if blocked is not None:
        return blocked
    partner_applications.apply(
        business_name=body.get("business_name"),
        first_name=body.get("first_name"),
        last_name=body.get("last_name"),
        email=email,
        password=body.get("password"),
        country=body.get("country"),
        phone=body.get("phone"),
        interest=body.get("interest"),
    )
    return JsonResponse({"status": "accepted"}, status=202)


# --- billing -------------------------------------------------------------------------------


@staff_view(["GET"])
def invoices_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        payment_reads.invoices(
            ctx,
            status=request.GET.get("status") or None,
            search=request.GET.get("q") or None,
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor") or None,
        )
    )


@staff_view(["GET"])
def payments_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        payment_reads.payments(
            ctx,
            status=request.GET.get("status") or None,
            failed_open=request.GET.get("failed") == "1",
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor") or None,
        )
    )


@staff_view(["GET"])
def stuck_orders_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse({"results": payment_reads.stuck_orders(ctx, limit=request.GET.get("limit"))})


@staff_view(["POST"])
def retry_activation_view(request: HttpRequest, ctx: TenantContext, order_id: UUID) -> JsonResponse:
    return JsonResponse(payment_reads.retry_activation(ctx, order_id))


@staff_view(["GET"])
def refunds_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        payment_reads.refunds(
            ctx,
            status=request.GET.get("status") or None,
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor") or None,
        )
    )


@staff_view(["GET"])
def credit_notes_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        payment_reads.credit_notes(
            ctx,
            search=request.GET.get("q") or None,
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor") or None,
        )
    )


@staff_view(["GET"])
def subscriptions_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        subscription_reads.subscriptions(
            ctx,
            status=request.GET.get("status") or None,
            product=request.GET.get("product") or None,
            renewing_within_days=request.GET.get("renewing") or None,
            search=request.GET.get("q") or None,
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor") or None,
        )
    )


@staff_view(["POST"])
def pay_invoice_from_wallet_view(
    request: HttpRequest, ctx: TenantContext, organization_id: UUID, invoice_id: UUID
) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        wallet_orders.pay_invoice_from_wallet(
            ctx,
            organization_id,
            invoice_id,
            reason=body.get("reason"),
            expected_version=body.get("expected_version"),
        )
    )


# --- wallets -------------------------------------------------------------------------------


@staff_view(["GET", "POST"])
def wallets_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "POST":
        body = read_json_body(request)
        return JsonResponse(
            wallet_console.open_wallet(
                ctx,
                owner_type=body.get("owner_type"),
                owner_id=body.get("owner_id"),
                kind=body.get("kind"),
                currency=body.get("currency"),
            ),
            status=201,
        )
    return JsonResponse(
        wallet_console.list_wallets(
            ctx,
            kind=request.GET.get("kind") or None,
            owner_type=request.GET.get("owner_type") or None,
            search=request.GET.get("q") or None,
            low=request.GET.get("low") == "1",
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor") or None,
        )
    )


@staff_view(["GET", "PUT"])
def wallet_view(request: HttpRequest, ctx: TenantContext, wallet_id: UUID) -> JsonResponse:
    if request.method == "PUT":
        return JsonResponse(
            wallets_admin.set_threshold(
                ctx, wallet_id, read_json_body(request).get("low_balance_threshold")
            )
        )
    return JsonResponse(wallet_console.wallet_detail(ctx, wallet_id))


@staff_view(["POST"])
def wallet_top_up_view(request: HttpRequest, ctx: TenantContext, wallet_id: UUID) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        wallets_admin.record_offline_top_up(
            ctx,
            wallet_id,
            amount=body.get("amount"),
            reference=body.get("reference"),
            reason=body.get("reason"),
            idempotency_key=request.headers.get("Idempotency-Key"),
        ),
        status=201,
    )


@staff_view(["POST"])
def wallet_adjust_view(request: HttpRequest, ctx: TenantContext, wallet_id: UUID) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        wallets_admin.adjust(
            ctx,
            wallet_id,
            amount=body.get("amount"),
            reason=body.get("reason"),
            idempotency_key=request.headers.get("Idempotency-Key"),
        ),
        status=201,
    )


@staff_view(["GET"])
def wallet_entries_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    wallet = request.GET.get("wallet") or None
    return JsonResponse(
        wallet_console.list_entries(
            ctx,
            wallet_id=UUID(wallet) if wallet else None,
            kind=request.GET.get("kind") or None,
            entry_type=request.GET.get("type") or None,
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor") or None,
        )
    )


@staff_view(["POST"])
def wallet_reverse_view(request: HttpRequest, ctx: TenantContext, entry_id: UUID) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        wallets_admin.reverse_entry(
            ctx,
            entry_id,
            amount=body.get("amount"),
            reason=body.get("reason"),
            idempotency_key=request.headers.get("Idempotency-Key"),
        ),
        status=201,
    )


@staff_view(["GET"])
def wallet_reconcile_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(wallets_admin.reconciliation(ctx))


@staff_view(["POST"])
def wallet_order_view(request: HttpRequest, ctx: TenantContext, organization_id: UUID) -> JsonResponse:
    """A top-up or credit pack order for a customer, settled like any other order."""
    body = read_json_body(request)
    key = str(request.headers.get("Idempotency-Key") or "")
    if body.get("pack_code"):
        placed = wallet_orders.place_credit_pack(
            ctx, organization_id, pack_code=body.get("pack_code"), idempotency_key=key
        )
    else:
        placed = wallet_orders.place_top_up(
            ctx,
            organization_id,
            wallet_kind=body.get("wallet_kind"),
            amount_minor=body.get("amount_minor"),
            currency=body.get("currency"),
            idempotency_key=key,
        )
    return JsonResponse(placed, status=201)


@staff_view(["GET"])
def usage_events_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        wallet_console.list_events(
            ctx,
            outcome=request.GET.get("outcome") or None,
            meter=request.GET.get("meter") or None,
            duplicates=request.GET.get("duplicates") == "1",
            unreviewed=request.GET.get("unreviewed") == "1",
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor") or None,
        )
    )


@staff_view(["POST"])
def usage_event_retry_view(request: HttpRequest, ctx: TenantContext, event_id: UUID) -> JsonResponse:
    return JsonResponse(wallets_admin.retry_event(ctx, event_id))


@staff_view(["POST"])
def usage_event_review_view(request: HttpRequest, ctx: TenantContext, event_id: UUID) -> JsonResponse:
    return JsonResponse(
        wallets_admin.review_event(ctx, event_id, note=read_json_body(request).get("note"))
    )


# --- usage pricing -------------------------------------------------------------------------


@staff_view(["GET", "POST"])
def usage_rates_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(wallets_admin.rates(ctx, history=request.GET.get("history") == "1"))
    body = read_json_body(request)
    return JsonResponse(
        wallets_admin.set_rate(
            ctx,
            meter=body.get("meter"),
            currency=body.get("currency"),
            unit_amount=body.get("unit_amount"),
            per_units=body.get("per_units"),
            included_units=body.get("included_units"),
            reason=body.get("reason"),
        ),
        status=201,
    )


@staff_view(["POST"])
def usage_rate_remove_view(request: HttpRequest, ctx: TenantContext, rate_id: UUID) -> JsonResponse:
    wallets_admin.remove_rate(ctx, rate_id, reason=read_json_body(request).get("reason"))
    return JsonResponse({"ok": True})


@staff_view(["GET", "POST"])
def credit_packs_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(wallets_admin.packs(ctx))
    return JsonResponse(wallets_admin.create_pack(ctx, read_json_body(request)), status=201)


@staff_view(["PUT"])
def credit_pack_view(request: HttpRequest, ctx: TenantContext, pack_id: UUID) -> JsonResponse:
    return JsonResponse(wallets_admin.update_pack(ctx, pack_id, read_json_body(request)))


@staff_view(["PUT"])
def product_app_links_view(request: HttpRequest, ctx: TenantContext, product_id: UUID) -> JsonResponse:
    return JsonResponse(
        {"app_links": catalog_services.set_app_links(ctx, product_id, read_json_body(request).get("app_links"))}
    )


@staff_view(["PUT"])
def plan_rules_view(request: HttpRequest, ctx: TenantContext, plan_id: UUID) -> JsonResponse:
    return JsonResponse(catalog_services.set_plan_rules(ctx, plan_id, read_json_body(request)))


# --- reports -------------------------------------------------------------------------------


@staff_view(["GET"])
def reports_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse({"results": reports.catalog(ctx)})


@staff_view(["GET"])
def report_view(request: HttpRequest, ctx: TenantContext, kind: str) -> HttpResponse:
    params = request.GET.dict()
    if request.GET.get("format") == "csv":
        filename, body = reports.export_csv(ctx, kind, params)
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response
    return JsonResponse(reports.run(ctx, kind, params))


# --- support -------------------------------------------------------------------------------


@staff_view(["GET"])
def support_tickets_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        support_desk.queue(
            ctx,
            audience=request.GET.get("audience") or None,
            status=request.GET.get("status") or None,
            assigned=request.GET.get("assigned") or None,
            search=request.GET.get("q") or None,
            limit=request.GET.get("limit"),
            offset=request.GET.get("offset"),
        )
    )


@staff_view(["GET", "POST"])
def support_ticket_view(request: HttpRequest, ctx: TenantContext, ticket_id: UUID) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(support_desk.detail(ctx, ticket_id))
    body = read_json_body(request)
    return JsonResponse(
        support_desk.reply(
            ctx,
            ticket_id,
            message=body.get("message"),
            status=body.get("status"),
            internal=body.get("internal"),
        )
    )


@staff_view(["POST"])
def support_assign_view(request: HttpRequest, ctx: TenantContext, ticket_id: UUID) -> JsonResponse:
    return JsonResponse(support_desk.assign(ctx, ticket_id, user_id=read_json_body(request).get("user_id")))


@staff_view(["POST"])
def support_resolve_view(request: HttpRequest, ctx: TenantContext, ticket_id: UUID) -> JsonResponse:
    return JsonResponse(
        support_desk.resolve(ctx, ticket_id, resolution=read_json_body(request).get("resolution"))
    )


@staff_view(["GET"])
def support_people_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse({"results": support_desk.staff_people(ctx)})


@staff_view(["GET", "POST"])
def help_articles_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(
            {
                "results": help_content.articles(
                    ctx,
                    kind=request.GET.get("kind") or None,
                    status=request.GET.get("status") or None,
                    search=request.GET.get("q") or None,
                )
            }
        )
    return JsonResponse(help_content.create(ctx, read_json_body(request)), status=201)


@staff_view(["PUT"])
def help_article_view(request: HttpRequest, ctx: TenantContext, article_id: UUID) -> JsonResponse:
    return JsonResponse(help_content.update(ctx, article_id, read_json_body(request)))


@staff_view(["POST"])
def help_article_status_view(request: HttpRequest, ctx: TenantContext, article_id: UUID) -> JsonResponse:
    return JsonResponse(help_content.set_status(ctx, article_id, read_json_body(request).get("status")))


@require_http_methods(["GET"])
def public_help_view(request: HttpRequest) -> JsonResponse:
    """Published help content for a reader. No credentials and no customer data in it."""
    blocked = limited(request, "public_catalog")
    if blocked is not None:
        return blocked
    return JsonResponse(
        {
            "results": help_content.published(
                audience=request.GET.get("audience"), kind=request.GET.get("kind")
            )
        }
    )


# --- customers -----------------------------------------------------------------------------


@staff_view(["POST"])
def customer_create_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        customer_admin.create_customer(
            ctx,
            name=body.get("name"),
            owner_email=body.get("owner_email"),
            owner_name=body.get("owner_name"),
            reason=body.get("reason"),
        ),
        status=201,
    )


@staff_view(["GET"])
def customer_members_view(request: HttpRequest, ctx: TenantContext, organization_id: UUID) -> JsonResponse:
    return JsonResponse({"results": customer_admin.members(ctx, organization_id)})


@staff_view(["GET"])
def customer_history_view(request: HttpRequest, ctx: TenantContext, organization_id: UUID) -> JsonResponse:
    return JsonResponse({"results": customer_admin.assignment_history(ctx, organization_id)})


@staff_view(["POST"])
def customer_assign_plan_view(request: HttpRequest, ctx: TenantContext, organization_id: UUID) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        customer_admin.assign_plan(
            ctx,
            organization_id,
            plan_version_id=body.get("plan_version_id"),
            billing_interval=body.get("billing_interval"),
            quantity=body.get("quantity"),
            reason=body.get("reason"),
            idempotency_key=request.headers.get("Idempotency-Key"),
        ),
        status=201,
    )


@staff_view(["GET"])
def setup_queue_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    organization = request.GET.get("organization") or None
    return JsonResponse(
        customer_setup.queue(
            ctx,
            status=request.GET.get("status") or None,
            product=request.GET.get("product") or None,
            organization_id=UUID(organization) if organization else None,
            limit=request.GET.get("limit"),
            offset=request.GET.get("offset"),
        )
    )


@staff_view(["POST"])
def setup_decision_view(request: HttpRequest, ctx: TenantContext, record_id: UUID) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        customer_setup.decide(
            ctx,
            record_id,
            decision=body.get("decision"),
            reason=body.get("reason"),
            expected_version=body.get("expected_version"),
        )
    )


@staff_view(["GET", "POST"])
def setup_requirements_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(
            {"results": customer_setup.requirements(ctx, product_code=request.GET.get("product"))}
        )
    return JsonResponse(customer_setup.save_requirement(ctx, read_json_body(request)), status=201)


@staff_view(["PUT"])
def setup_requirement_view(request: HttpRequest, ctx: TenantContext, requirement_id: UUID) -> JsonResponse:
    return JsonResponse(
        customer_setup.save_requirement(ctx, read_json_body(request), requirement_id=requirement_id)
    )


# --- settings and security -----------------------------------------------------------------


@staff_view(["GET"])
def domains_overview_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(domains_console.overview(ctx))


@staff_view(["POST"])
def domains_check_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(domains_console.check_host(ctx, read_json_body(request).get("host")))


@staff_view(["GET"])
def login_clients_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse({"results": domains_console.login_clients(ctx)})


@staff_view(["POST"])
def staff_account_create_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    body = read_json_body(request)
    return JsonResponse(
        cp_security.create_staff_account(
            ctx,
            email=body.get("email"),
            full_name=body.get("full_name"),
            role=body.get("role"),
            reason=body.get("reason"),
        ),
        status=201,
    )


@staff_view(["GET"])
def sign_in_audit_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.administration.staff_users import sign_in_events

    authz.require(ctx, "platform.staff_audit.view")
    start, end = overview.optional_window(request.GET.get("from"), request.GET.get("to"))
    try:
        limit = max(1, min(int(request.GET.get("limit") or 50), 100))
        offset = max(0, int(request.GET.get("offset") or 0))
    except ValueError:
        limit, offset = 50, 0
    return JsonResponse(
        sign_in_events(
            staff_only=request.GET.get("everyone") != "1",
            kind=request.GET.get("kind") or None,
            search=request.GET.get("q") or None,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
    )


# --- a customer's own side -----------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def org_support_tickets_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        return JsonResponse(
            support_desk.customer_list(
                ctx,
                status=request.GET.get("status") or None,
                limit=request.GET.get("limit"),
                offset=request.GET.get("offset"),
            )
        )
    body = read_json_body(request)
    return JsonResponse(
        support_desk.customer_open(
            ctx,
            subject=body.get("subject"),
            message=body.get("message"),
            category=body.get("category"),
            priority=body.get("priority"),
        ),
        status=201,
    )


@require_http_methods(["GET", "POST"])
def org_support_ticket_view(request: HttpRequest, org_slug: str, ticket_id: UUID) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        return JsonResponse(support_desk.customer_detail(ctx, ticket_id))
    return JsonResponse(
        support_desk.customer_reply(ctx, ticket_id, message=read_json_body(request).get("message"))
    )


@require_http_methods(["GET"])
def org_setup_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    return JsonResponse(customer_setup.customer_view(request.tenant_context))  # type: ignore[attr-defined]


@require_http_methods(["POST"])
def org_setup_submit_view(request: HttpRequest, org_slug: str, record_id: UUID) -> JsonResponse:
    return JsonResponse(
        customer_setup.customer_submit(
            request.tenant_context,  # type: ignore[attr-defined]
            record_id,
            answers=read_json_body(request).get("answers"),
        )
    )
