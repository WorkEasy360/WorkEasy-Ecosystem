"""Helpdesk and Wallet & Payouts API (2026-11-03, the unified partner dashboard).

Thin adapters, like every view here. Three callers:

- the partner (``/api/v1/partner/<partner_id>/...``, ``partner_view``): its own
  tickets to Workeasy360, its customers' tickets (the desk), saved replies,
  payout details and withdrawal requests;
- Workeasy360 staff (``/api/v1/admin/...``, ``staff_view``): the upward ticket
  desk and the withdrawal / payout-details queue;
- a customer organization's member (``/api/v1/orgs/<slug>/support/...``): its
  tickets to its partner, from the verified org context.

The services decide what each caller may do; nothing here trusts an id, an
amount or a role from the request body.
"""

from __future__ import annotations

from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.controlplane import partner_withdrawals
from workeasy360.commercial.partners import tickets
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.control_plane_http import (
    limited,
    partner_view,
    staff_view,
)

_body = legacy._body


# --- the partner: tickets -------------------------------------------------------------------


@partner_view(["GET", "POST"])
def tickets_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """GET: the partner's tickets (``?direction=platform|customer&status=``).
    POST: open a ticket to Workeasy360."""
    if request.method == "POST":
        blocked = limited(request, "partner_ticket", user=request.user)
        if blocked is not None:
            return blocked
        body = _body(request)
        return JsonResponse(
            tickets.open_platform_ticket(
                ctx,
                subject=body.get("subject"),
                message=body.get("message"),
                category=body.get("category"),
                priority=body.get("priority"),
                related_ref=body.get("related_ref"),
            ),
            status=201,
        )
    return JsonResponse(
        tickets.partner_list(
            ctx,
            direction=request.GET.get("direction"),
            status=request.GET.get("status"),
            limit=request.GET.get("limit"),
            offset=request.GET.get("offset"),
        )
    )


@partner_view(["GET", "POST"])
def ticket_view(
    request: HttpRequest, ctx: TenantContext, ticket_id: UUID
) -> JsonResponse:
    """GET: one ticket and its conversation. POST: a reply and/or a status."""
    if request.method == "POST":
        blocked = limited(request, "partner_ticket", user=request.user)
        if blocked is not None:
            return blocked
        body = _body(request)
        return JsonResponse(
            tickets.partner_reply(
                ctx,
                ticket_id,
                message=body.get("message"),
                internal=body.get("internal") is True,
                status=body.get("status"),
            )
        )
    return JsonResponse(tickets.partner_detail(ctx, ticket_id))


@partner_view(["GET", "POST"])
def canned_responses_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    if request.method == "POST":
        body = _body(request)
        return JsonResponse(
            tickets.canned_save(ctx, title=body.get("title"), body=body.get("body")),
            status=201,
        )
    return JsonResponse(tickets.canned_list(ctx))


@partner_view(["PUT", "DELETE"])
def canned_response_view(
    request: HttpRequest, ctx: TenantContext, response_id: UUID
) -> HttpResponse:
    if request.method == "DELETE":
        tickets.canned_delete(ctx, response_id)
        return HttpResponse(status=204)
    body = _body(request)
    return JsonResponse(
        tickets.canned_save(
            ctx, response_id=response_id, title=body.get("title"), body=body.get("body")
        )
    )


# --- the partner: wallet & payouts -------------------------------------------------------------


@partner_view(["GET"])
def wallet_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(partner_withdrawals.wallet(ctx))


@partner_view(["PUT"])
def payout_method_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    blocked = limited(request, "partner_payout", user=request.user)
    if blocked is not None:
        return blocked
    return JsonResponse(partner_withdrawals.save_payout_method(ctx, body=_body(request)))


@partner_view(["POST"])
def withdrawals_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    blocked = limited(request, "partner_payout", user=request.user)
    if blocked is not None:
        return blocked
    return JsonResponse(partner_withdrawals.request_withdrawal(ctx), status=201)


@partner_view(["POST"])
def withdrawal_cancel_view(
    request: HttpRequest, ctx: TenantContext, withdrawal_id: UUID
) -> JsonResponse:
    return JsonResponse(partner_withdrawals.cancel_withdrawal(ctx, withdrawal_id))


# --- Workeasy360 staff ---------------------------------------------------------------------------


@staff_view(["GET"])
def staff_tickets_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        tickets.staff_queue(
            ctx,
            status=request.GET.get("status"),
            search=request.GET.get("q"),
            limit=request.GET.get("limit"),
            offset=request.GET.get("offset"),
        )
    )


@staff_view(["GET", "POST"])
def staff_ticket_view(
    request: HttpRequest, ctx: TenantContext, ticket_id: UUID
) -> JsonResponse:
    if request.method == "POST":
        body = _body(request)
        return JsonResponse(
            tickets.staff_reply(
                ctx,
                ticket_id,
                message=body.get("message"),
                internal=body.get("internal") is True,
                status=body.get("status"),
            )
        )
    return JsonResponse(tickets.staff_detail(ctx, ticket_id))


@staff_view(["GET"])
def staff_withdrawals_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    return JsonResponse(
        partner_withdrawals.staff_queue(ctx, status=request.GET.get("status"))
    )


@staff_view(["POST"])
def staff_withdrawal_action_view(
    request: HttpRequest, ctx: TenantContext, withdrawal_id: UUID
) -> JsonResponse:
    """``{"action": "start_payout"}`` or ``{"action": "reject", "note": ...}``."""
    body = _body(request)
    if body.get("action") == "start_payout":
        return JsonResponse(partner_withdrawals.staff_start_payout(ctx, withdrawal_id))
    return JsonResponse(
        partner_withdrawals.staff_reject(ctx, withdrawal_id, note=body.get("note"))
    )


@staff_view(["GET", "POST"])
def staff_payout_method_view(
    request: HttpRequest, ctx: TenantContext, partner_id: UUID
) -> JsonResponse:
    """GET: the partner's payout details (``?reveal=1`` opens the account
    number and PAN, audited). POST: ``{"decision": "verified"|"rejected", "note"}``."""
    if request.method == "POST":
        body = _body(request)
        return JsonResponse(
            partner_withdrawals.staff_verify_method(
                ctx, partner_id, decision=body.get("decision"), note=body.get("note")
            )
        )
    response = JsonResponse(
        partner_withdrawals.staff_payout_method(
            ctx, partner_id, reveal=request.GET.get("reveal") == "1"
        )
    )
    response["Cache-Control"] = "private, no-store"
    return response


# --- a customer organization: tickets to its partner -------------------------------------------------


@require_http_methods(["GET", "POST"])
def org_tickets_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "POST":
        blocked = limited(request, "partner_ticket", user=request.user)
        if blocked is not None:
            return blocked
        body = _body(request)
        return JsonResponse(
            tickets.customer_open(
                ctx,
                subject=body.get("subject"),
                message=body.get("message"),
                category=body.get("category"),
                priority=body.get("priority"),
            ),
            status=201,
        )
    return JsonResponse(
        tickets.customer_overview(ctx, status=request.GET.get("status") or "all")
    )


@require_http_methods(["GET", "POST"])
def org_ticket_view(request: HttpRequest, org_slug: str, ticket_id: UUID) -> JsonResponse:
    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "POST":
        body = _body(request)
        return JsonResponse(
            tickets.customer_reply(
                ctx, ticket_id, message=body.get("message"), status=body.get("status")
            )
        )
    return JsonResponse(tickets.customer_detail(ctx, ticket_id))
