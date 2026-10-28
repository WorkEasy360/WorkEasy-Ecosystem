"""Super Admin: the partner programme's commercial settings (ADR-053).

Two settings the owner configures, both behind the staff session and the
services' own permissions:

- **Margins** (``/admin/margins``): the partner discount off list price - a
  default per experience and per-partner overrides - from which a partner's
  cost and margin are computed. Versioned; never retroactive.
- **Set-up fees** (``/admin/partner-programme/fees``): the optional fee for
  becoming a Reseller or White-label partner, settled offline and confirmed at
  approval.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse

from workeasy360.commercial.commissions import margins
from workeasy360.commercial.partners import capabilities, upgrades
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values import json_input
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.interfaces.api.v1.control_plane_http import staff_view
from workeasy360.platform.authz import services as authz


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json_input.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PartnerValidationError("Malformed JSON body.", field="body") from exc
    if not isinstance(payload, dict):
        raise PartnerValidationError("The body must be a JSON object.", field="body")
    return payload


def _partner(raw: Any) -> UUID | None:
    if raw in (None, ""):
        return None
    try:
        return UUID(str(raw))
    except ValueError as exc:
        raise PartnerValidationError(
            "partner_id is not valid.", field="partner_id"
        ) from exc


@staff_view(["GET", "POST"])
def margins_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """GET: the margin rules (``?partner_id=`` narrows to one partner's
    overrides). POST: set one - ``discount_bp`` with either ``experience`` or
    ``partner_id``, optionally ``product_code`` and ``recurring``."""
    if request.method == "GET":
        return JsonResponse(
            margins.margin_rules(ctx, partner_id=_partner(request.GET.get("partner_id")))
        )
    body = _body(request)
    rule = margins.set_margin(
        ctx,
        discount_bp=body.get("discount_bp"),
        reason=body.get("reason") or "Partner margin set in the console.",
        experience=body.get("experience", ""),
        partner_id=_partner(body.get("partner_id")),
        product_code=body.get("product_code", ""),
        recurring=body.get("recurring", True),
    )
    return JsonResponse(margins._view(rule), status=201)


@staff_view(["POST"])
def margin_end_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """End a margin chain now: later sales earn commission again."""
    body = _body(request)
    rule = margins.end_margin(
        ctx,
        code=body.get("code"),
        reason=body.get("reason") or "Partner margin ended in the console.",
    )
    return JsonResponse(margins._view(rule))


@staff_view(["GET", "PUT"])
def program_fees_view(request: HttpRequest, ctx: TenantContext) -> JsonResponse:
    """GET: the set-up fee per target. PUT: set one (zero removes it)."""
    if request.method == "PUT":
        body = _body(request)
        upgrades.set_program_fee(
            ctx,
            experience=body.get("experience"),
            amount_minor=body.get("amount_minor"),
            amount=body.get("amount"),
            currency=body.get("currency"),
        )
    else:
        authz.require(ctx, upgrades.STAFF_VIEW)
    return JsonResponse(
        {
            "fees": {
                key: upgrades.program_fee(key)
                for key in (capabilities.RESELLER, capabilities.WHITE_LABEL)
            }
        }
    )
