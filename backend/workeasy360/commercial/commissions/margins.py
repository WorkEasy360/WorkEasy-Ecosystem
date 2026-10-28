"""Partner margin rules: a discount off list price (ADR-053).

The owner's reseller model. Super Admin sets a **discount** - a programme
default per partner experience (Reseller, White-label), optionally overridden
for one partner and narrowed to one product. From it:

- **partner cost** = list price less the discount;
- **customer price** = the partner's price book, else list;
- **margin** = customer price - partner cost.

A managed sale under a margin rule earns the margin in the one commission
ledger *instead of* a commission (``accrual._margin_evaluation``); never both.

Rules are versioned exactly like commission rules: a change appends the next
version and retires the live one at the instant the new one starts, nothing is
backdated, an accrual stamps the version it used, and a trigger freezes every
column but ``retired_at``. Ending a rule retires it - the partner returns to its
commission rule for later sales; history is untouched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.db import connection, transaction
from django.utils import timezone

from workeasy360.commercial.commissions.rules import BACKDATE_TOLERANCE, MANAGE
from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.domain import clean_reason
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.identifiers import ProductCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.metrics import metrics

VIEW: Final = "platform.staff_commission.view"
EXPERIENCES: Final = (capabilities.RESELLER, capabilities.WHITE_LABEL)


def chain_code(
    *, experience: str = "", partner_id: UUID | None = None, product_code: str = ""
) -> str:
    """The code of a margin chain: one per (scope, product)."""
    suffix = f"-{product_code}" if product_code else ""
    if partner_id is not None:
        return f"mo-{partner_id.hex[:12]}{suffix}"
    return f"md-{experience.replace('_', '-')}{suffix}"


def _discount(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10000:
        raise PartnerValidationError(
            "discount_bp must be a whole number of basis points between 0 and 10000.",
            field="discount_bp",
        )
    return value


def _product(value: Any) -> str:
    product = str(value or "").strip()
    if product:
        try:
            ProductCode(product)
        except ValueError as exc:
            raise PartnerValidationError(
                "product_code is not valid.", field="product_code"
            ) from exc
    return product


def _scope(experience: Any, partner_id: UUID | None) -> str:
    """Exactly one of a programme default (experience) or a partner override."""
    exp = str(experience or "").strip()
    if (partner_id is None) == (not exp):
        raise PartnerValidationError(
            "Name either a partner experience (a programme default) or a partner "
            "(an override), not both.",
            field="experience",
        )
    if partner_id is None and exp not in EXPERIENCES:
        raise PartnerValidationError(
            "experience must be reseller or white_label.", field="experience"
        )
    if partner_id is not None:
        row = partner_selectors.partner_row(partner_id)
        if row is None:
            raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
        if row.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot be given a margin.", field="partner_id"
            )
    return exp


@in_scope
def set_margin(
    ctx: TenantContext,
    *,
    discount_bp: Any,
    reason: Any,
    experience: Any = "",
    partner_id: UUID | None = None,
    product_code: Any = "",
    recurring: Any = True,
    effective_from: datetime | None = None,
) -> Any:
    """Make ``discount_bp`` the partner discount for a scope from
    ``effective_from`` (default now, never earlier). Idempotent: the discount
    already in force changes nothing and returns the live version."""
    from workeasy360.commercial.commissions.models import MarginRule

    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    discount = _discount(discount_bp)
    product = _product(product_code)
    exp = _scope(experience, partner_id)
    if not isinstance(recurring, bool):
        raise PartnerValidationError(
            "recurring must be true or false.", field="recurring"
        )
    now = timezone.now()
    start = effective_from or now
    if start < now - BACKDATE_TOLERANCE:
        raise PartnerValidationError(
            "A margin cannot take effect in the past: that would change what "
            "earlier sales are worth.",
            field="effective_from",
        )
    code = chain_code(experience=exp, partner_id=partner_id, product_code=product)

    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"margin:{code}"])
        previous = list(MarginRule.objects.filter(code=code).order_by("-version"))
        live = next((m for m in previous if m.retired_at is None), None)
        if (
            live is not None
            and live.discount_bp == discount
            and live.recurring == recurring
            and effective_from is None
            and live.effective_from <= timezone.now()
        ):
            return live
        if live is not None and start < live.effective_from:
            raise PartnerValidationError(
                "The new version must start no earlier than the version it replaces.",
                field="effective_from",
            )
        if live is not None:
            live.retired_at = start
            live.save(update_fields=["retired_at"])
        rule = MarginRule.objects.create(
            code=code,
            version=(previous[0].version + 1) if previous else 1,
            experience=exp,
            partner_id=partner_id,
            product_code=product,
            discount_bp=discount,
            recurring=recurring,
            effective_from=start,
            reason=why,
            created_by_id=ctx.user_id,
        )
        audit.record(
            ctx,
            "commission.margin_rule_created",
            resource_type="commissions.margin_rule",
            resource_id=rule.id,
            changes={
                "code": code,
                "version": rule.version,
                "discount_bp": discount,
                "previous_discount_bp": live.discount_bp if live is not None else None,
                "experience": exp or None,
                "partner_id": str(partner_id) if partner_id else None,
                "product_code": product,
                "recurring": recurring,
                "effective_from": start.isoformat(),
            },
            reason=why,
        )
    metrics.increment("commission_margin_rule_changes_total")
    return rule


@in_scope
def end_margin(ctx: TenantContext, *, code: Any, reason: Any) -> Any:
    """Retire a margin chain now. Later sales earn under the commission rules
    again; every accrual already written keeps the margin it was made at."""
    from workeasy360.commercial.commissions.models import MarginRule

    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    clean = str(code or "").strip()
    with transaction.atomic():
        live = (
            MarginRule.objects.select_for_update()
            .filter(code=clean, retired_at__isnull=True)
            .first()
        )
        if live is None:
            raise PartnerRecordNotFound("No live margin rule with that code.", code=clean)
        live.retired_at = timezone.now()
        live.save(update_fields=["retired_at"])
        audit.record(
            ctx,
            "commission.margin_rule_ended",
            resource_type="commissions.margin_rule",
            resource_id=live.id,
            changes={"code": clean, "version": live.version},
            reason=why,
        )
    return live


def _view(rule: Any) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "code": rule.code,
        "version": rule.version,
        "experience": rule.experience or None,
        "partner_id": str(rule.partner_id) if rule.partner_id else None,
        "product_code": rule.product_code,
        "discount_bp": rule.discount_bp,
        "recurring": rule.recurring,
        "effective_from": rule.effective_from.isoformat(),
        "retired_at": rule.retired_at.isoformat() if rule.retired_at else None,
        "reason": rule.reason,
    }


@in_scope
def margin_rules(ctx: TenantContext, *, partner_id: UUID | None = None) -> dict[str, Any]:
    """The live programme defaults and overrides, plus each chain's history."""
    from workeasy360.commercial.commissions.models import MarginRule

    if not (authz.check(ctx, VIEW) or authz.check(ctx, MANAGE)):
        authz.require(ctx, VIEW)
    rows = MarginRule.objects.all().order_by("code", "-version")
    if partner_id is not None:
        rows = rows.filter(partner_id=partner_id)
    out = [_view(r) for r in rows]
    return {
        "live": [r for r in out if r["retired_at"] is None],
        "history": out,
    }


def effective_discount(
    partner_id: UUID, *, product_code: str, at: datetime | None = None
) -> tuple[int, Any] | None:
    """``(discount_bp, rule)`` that would govern this partner's sale of
    ``product_code`` now, or ``None`` (the partner earns commission). The
    same selection the accrual makes, for the partner's pricing screens."""
    from workeasy360.commercial.commissions import accrual, domain, selectors

    moment = at or timezone.now()
    margin = domain.select_margin(
        selectors.margin_views(),
        partner_id=partner_id,
        experience=accrual._experience_at(partner_id, moment),
        product_code=product_code,
        at=moment,
    )
    if margin is None:
        return None
    return margin.discount_bp, margin
