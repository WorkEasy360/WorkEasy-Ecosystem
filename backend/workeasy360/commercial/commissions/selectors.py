"""Commission, referral and payout reads.

Same rule as ``partners.selectors``: everything that serves a *partner* takes
the ``partner_id`` a server-verified membership established and filters on it,
so another partner's ledger row or payout id simply does not resolve. Nothing
here returns a customer's name, contact, member list or any business record:
a ledger row identifies its sale by an opaque reference, never by who bought.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Count, Q, Sum

from workeasy360.contracts.enums import AccrualKind, PayoutStatus, ReferralStatus
from workeasy360.contracts.values.cursor import (
    Page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)

#: A payout-list filter meaning draft, ready or approved - never a stored status.
OPEN_PAYOUTS = "open"


def _limit(limit: Any) -> int:
    return clamp_limit(limit, default=25, maximum=100)


# --- ledger ---------------------------------------------------------------


def accrual_dict(row: Any, *, for_partner: bool) -> dict[str, Any]:
    """The wire shape of a ledger row.

    ``for_partner`` narrows it: a partner sees what it earned - amount, kind,
    when, the reason - and not the internal derivation (rule code, snapshot
    basis, attribution ref), which is the programme's margin arithmetic.
    Staff see everything.
    """
    payload = {
        "id": str(row.id),
        "kind": row.kind,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "event_at": row.event_at.isoformat(),
        "clears_at": row.clears_at.isoformat(),
        "reason_code": row.reason_code,
        "reverses": str(row.reverses_id) if row.reverses_id else None,
    }
    if not for_partner:
        payload.update(
            {
                "partner_id": str(row.partner_id),
                "organization_id": str(row.organization_id)
                if row.organization_id
                else None,
                "source_ref": row.source_ref,
                "rule_code": row.rule.code if row.rule_id else None,
                "rule_version": row.rule_version,
                "rate_bp": row.rate_bp,
                "basis_minor": row.basis_minor,
                "basis_currency": row.basis_currency,
                "attribution_kind": row.attribution_kind,
                "reason": row.reason,
                "created_by": str(row.created_by_id) if row.created_by_id else None,
            }
        )
    return payload


def accruals_page(
    *,
    partner_id: UUID | None,
    for_partner: bool,
    kind: str | None = None,
    unpaid: bool = False,
    limit: int = 25,
    cursor: str | None = None,
) -> Page:
    """A bounded page of ledger rows, newest first. Never an export.

    ``unpaid`` keeps only the entries that are in no live payout - not yet
    claimed by one, or released from a cancelled one (Phase 10: the Partners
    overview's "pending commissions" card lands here).
    """
    from django.db.models import Exists, OuterRef

    from workeasy360.commercial.commissions.models import CommissionAccrual, PayoutLine

    limit = _limit(limit)
    queryset = CommissionAccrual.objects.select_related("rule").order_by(
        "-event_at", "-id"
    )
    if partner_id is not None:
        queryset = queryset.filter(partner_id=partner_id)
    if kind:
        queryset = queryset.filter(kind=kind)
    if unpaid:
        queryset = queryset.filter(
            ~Exists(
                PayoutLine.objects.filter(
                    accrual=OuterRef("pk"), released_at__isnull=True
                )
            )
        )
    decoded = decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(event_at__lt=stamp) | Q(event_at=stamp, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [accrual_dict(r, for_partner=for_partner) for r in rows]
    statuses = entry_statuses(rows)
    for row, item in zip(rows, items, strict=True):
        item.update(statuses[row.id])
    if not for_partner:
        _link_staff_context(rows, items)
    return Page(
        items=items,
        next_cursor=encode_cursor(rows[-1].event_at, rows[-1].id) if has_more else None,
        has_more=has_more,
    )


def entry_statuses(
    rows: list[Any], now: datetime | None = None
) -> dict[Any, dict[str, Any]]:
    """What each ledger row is now, for the partner (ADR-054). Two queries.

    - ``paid``: in a payout confirmed as paid;
    - ``reversed``: an earning reversed in full (``reversed_minor`` shows a
      partial reversal of one that is otherwise pending or approved);
    - ``pending``: still inside the holdback - a refund may yet reverse it;
    - ``approved``: cleared, and payable in the next payout.

    A reversal or adjustment row is itself ``approved`` once cleared - it
    changes the balance - or ``paid`` when a payout settled it.
    """
    from django.utils import timezone

    from workeasy360.commercial.commissions.models import CommissionAccrual, PayoutLine

    moment = now or timezone.now()
    ids = [r.id for r in rows]
    paid = set(
        PayoutLine.objects.filter(
            accrual_id__in=ids,
            released_at__isnull=True,
            payout__status=PayoutStatus.PAID.value,
        ).values_list("accrual_id", flat=True)
    )
    reversed_by = {
        r["reverses_id"]: r["total"] or 0
        for r in CommissionAccrual.objects.filter(reverses_id__in=ids)
        .values("reverses_id")
        .annotate(total=Sum("amount_minor"))
    }
    out: dict[Any, dict[str, Any]] = {}
    for r in rows:
        back = -reversed_by.get(r.id, 0)
        if r.id in paid:
            status = "paid"
        elif r.kind == AccrualKind.ACCRUAL.value and back >= r.amount_minor:
            status = "reversed"
        elif r.clears_at > moment:
            status = "pending"
        else:
            status = "approved"
        out[r.id] = {"status": status, "reversed_minor": back}
    return out


def _link_staff_context(rows: list[Any], items: list[dict[str, Any]]) -> None:
    """Add to each staff ledger row the two things a reader asks next: **which
    payout, if any, pays it** and **whose sale it was** - two queries for the
    whole page, never one per row. A partner never gets either (the payout is
    the programme's bookkeeping, and the customer is not the partner's to
    read from its ledger)."""
    from workeasy360.commercial.commissions.models import PayoutLine
    from workeasy360.platform.organizations.selectors import organization_names

    if not rows:
        return
    paid_by = {
        line.accrual_id: {"id": str(line.payout_id), "status": line.payout.status}
        for line in PayoutLine.objects.filter(
            accrual_id__in=[r.id for r in rows], released_at__isnull=True
        ).select_related("payout")
    }
    names = organization_names({r.organization_id for r in rows if r.organization_id})
    for row, item in zip(rows, items, strict=True):
        item["payout"] = paid_by.get(row.id)
        item["organization_name"] = (
            names.get(row.organization_id, "") if row.organization_id else ""
        )


def balance_summary(partner_id: UUID) -> list[dict[str, Any]]:
    """Per-currency totals of one partner's ledger, in **one query**.

    ``earned`` is accruals, ``adjustments`` is reversals plus adjustments, and
    ``balance`` their sum. Currencies are reported separately and never added
    together: there is no conversion in this platform.
    """
    from workeasy360.commercial.commissions.models import CommissionAccrual

    rows = (
        CommissionAccrual.objects.filter(partner_id=partner_id)
        .values("currency")
        .annotate(
            earned=Sum("amount_minor", filter=Q(kind=AccrualKind.ACCRUAL.value)),
            adjustments=Sum("amount_minor", filter=~Q(kind=AccrualKind.ACCRUAL.value)),
            entries=Count("id"),
        )
        .order_by("currency")
    )
    return [
        {
            "currency": r["currency"],
            "earned_minor": r["earned"] or 0,
            "adjustments_minor": r["adjustments"] or 0,
            "balance_minor": (r["earned"] or 0) + (r["adjustments"] or 0),
            "entries": r["entries"],
        }
        for r in rows
    ]


def get_accrual(accrual_id: UUID, *, partner_id: UUID | None = None) -> Any | None:
    from workeasy360.commercial.commissions.models import CommissionAccrual

    queryset = CommissionAccrual.objects.filter(id=accrual_id)
    if partner_id is not None:
        queryset = queryset.filter(partner_id=partner_id)
    return queryset.first()


# --- rules ----------------------------------------------------------------


def rules_list(*, live_only: bool = True) -> list[dict[str, Any]]:
    from workeasy360.commercial.commissions.models import CommissionRule

    queryset = CommissionRule.objects.select_related("partner").order_by(
        "code", "-version"
    )
    if live_only:
        queryset = queryset.filter(retired_at__isnull=True)
    return [
        {
            "id": str(r.id),
            "code": r.code,
            "version": r.version,
            "partner_id": str(r.partner_id) if r.partner_id else None,
            "product_code": r.product_code,
            "rate_bp": r.rate_bp,
            "recurring": r.recurring,
            "effective_from": r.effective_from.isoformat(),
            "retired_at": r.retired_at.isoformat() if r.retired_at else None,
        }
        for r in queryset[:200]
    ]


def _rule_state(rule: Any, now: datetime) -> str:
    if rule.retired_at is not None and rule.retired_at <= now:
        return "superseded"
    if rule.effective_from > now:
        return "scheduled"
    return "in_force"


def _rule_payload(rule: Any, now: datetime) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "code": rule.code,
        "version": rule.version,
        "product_code": rule.product_code,
        "rate_bp": rule.rate_bp,
        "recurring": rule.recurring,
        "effective_from": rule.effective_from.isoformat(),
        "retired_at": rule.retired_at.isoformat() if rule.retired_at else None,
        "state": _rule_state(rule, now),
        "reason": rule.reason,
    }


def partner_rules(partner_id: UUID, *, now: datetime) -> list[dict[str, Any]]:
    """Every version of every rule that names this partner, newest first per
    chain, each with where it stands *now* (``in_force``, ``scheduled`` or
    ``superseded``). One query; bounded, because a partner has a handful of
    rules and each is one row per change."""
    from workeasy360.commercial.commissions.models import CommissionRule

    return [
        _rule_payload(r, now)
        for r in CommissionRule.objects.filter(partner_id=partner_id).order_by(
            "code", "-version"
        )[:200]
    ]


def effective_rule(
    partner_id: UUID, *, product_code: str, at: datetime
) -> dict[str, Any] | None:
    """The rule that would govern a sale of ``product_code`` by ``partner_id``
    at ``at`` - chosen by the same pure function accrual uses
    (``domain.select_rule``), so what a screen shows can never disagree with
    what a sale earns. ``scope`` says whether it is the partner's own or the
    programme default."""
    from workeasy360.commercial.commissions import domain

    chosen = domain.select_rule(
        rule_views(), partner_id=partner_id, product_code=product_code, at=at
    )
    if chosen is None:
        return None
    return {
        "code": chosen.code,
        "version": chosen.version,
        "rate_bp": chosen.rate_bp,
        "product_code": chosen.product_code,
        "scope": "partner" if chosen.partner_id is not None else "programme_default",
        "effective_from": chosen.effective_from.isoformat(),
    }


def effective_rates(
    partner_id: UUID, product_codes: list[str], *, at: datetime
) -> dict[str, dict[str, Any] | None]:
    """``{product_code: {rate_bp, recurring} | None}``: what a sale of each
    product by this partner would earn at ``at`` (ADR-055's Explore), chosen
    by the same pure function accrual uses. The rules are read once."""
    from workeasy360.commercial.commissions import domain

    views = rule_views()
    out: dict[str, dict[str, Any] | None] = {}
    for code in product_codes:
        chosen = domain.select_rule(
            views, partner_id=partner_id, product_code=code, at=at
        )
        out[code] = (
            {"rate_bp": chosen.rate_bp, "recurring": bool(chosen.recurring)}
            if chosen is not None
            else None
        )
    return out


def effective_margins(
    partner_id: UUID, product_codes: list[str], *, at: datetime
) -> dict[str, int | None]:
    """``{product_code: discount_bp | None}``: the margin (a discount off the
    list price) a sale of each product by this partner would earn at ``at``
    (ADR-055's reseller Explore) - the accrual's own choice, the margin rules
    read once. ``None``: no margin rule, the partner earns commission."""
    from workeasy360.commercial.commissions import accrual, domain

    views = margin_views()
    experience = accrual._experience_at(partner_id, at)
    out: dict[str, int | None] = {}
    for code in product_codes:
        chosen = domain.select_margin(
            views, partner_id=partner_id, experience=experience, product_code=code, at=at
        )
        out[code] = chosen.discount_bp if chosen is not None else None
    return out


def _rules_in_force(now: datetime) -> Any:
    from workeasy360.commercial.commissions.models import CommissionRule

    return CommissionRule.objects.filter(effective_from__lte=now).filter(
        Q(retired_at__isnull=True) | Q(retired_at__gt=now)
    )


def rule_applies_exists(now: datetime, *, outer: str = "pk") -> Any:
    """An ``Exists`` over an outer *partner* query: some commission rule is in
    force for it at ``now`` - its own (for any product) or the programme
    default. The profile-completion filter composes it (``controlplane``); the
    partner's own rows are never opened here."""
    from django.db.models import Exists, OuterRef

    return Exists(
        _rules_in_force(now).filter(
            Q(partner_id=OuterRef(outer)) | Q(partner__isnull=True)
        )
    )


def partners_with_applicable_rule(partner_ids: list[UUID], now: datetime) -> set[UUID]:
    """Which of these partners some commission rule is in force for at ``now``
    (the same question as :func:`rule_applies_exists`). Two queries at most."""
    if not partner_ids:
        return set()
    rules = _rules_in_force(now)
    if rules.filter(partner__isnull=True).exists():
        return set(partner_ids)
    return set(
        rules.filter(partner_id__in=partner_ids).values_list("partner_id", flat=True)
    )


def active_referral_code_exists(*, outer: str = "pk") -> Any:
    """An ``Exists`` over an outer partner query: it holds an active referral code."""
    from django.db.models import Exists, OuterRef

    from workeasy360.commercial.commissions.models import ReferralCode
    from workeasy360.contracts.enums import GrantStatus

    return Exists(
        ReferralCode.objects.filter(
            partner_id=OuterRef(outer), status=GrantStatus.ACTIVE.value
        )
    )


def rule_views() -> list[Any]:
    """Every rule, as pure-domain views, for selection. Rules are few."""
    from workeasy360.commercial.commissions.domain import RuleView
    from workeasy360.commercial.commissions.models import CommissionRule

    return [
        RuleView(
            id=r.id,
            code=r.code,
            version=r.version,
            partner_id=r.partner_id,
            product_code=r.product_code,
            rate_bp=r.rate_bp,
            effective_from=r.effective_from,
            retired_at=r.retired_at,
            recurring=r.recurring,
        )
        for r in CommissionRule.objects.all()
    ]


def margin_views() -> list[Any]:
    """Every margin rule version, as pure-domain views (ADR-053)."""
    from workeasy360.commercial.commissions.domain import MarginView
    from workeasy360.commercial.commissions.models import MarginRule

    return [
        MarginView(
            id=m.id,
            code=m.code,
            version=m.version,
            experience=m.experience,
            partner_id=m.partner_id,
            product_code=m.product_code,
            discount_bp=m.discount_bp,
            effective_from=m.effective_from,
            retired_at=m.retired_at,
            recurring=m.recurring,
        )
        for m in MarginRule.objects.all()
    ]


# --- payouts --------------------------------------------------------------


def payout_dict(row: Any, *, for_partner: bool) -> dict[str, Any]:
    """A payout. A partner sees period, currency and the three totals plus
    status - never who prepared or approved it, never internal reasons, and
    no bank details because none exist."""
    payload = {
        "id": str(row.id),
        "status": row.status,
        "currency": row.currency,
        "period_end": row.period_end.isoformat(),
        "gross_minor": row.gross_minor,
        "adjustments_minor": row.adjustments_minor,
        "payable_minor": row.payable_minor,
        "line_count": row.line_count,
        "created_at": row.created_at.isoformat(),
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
    }
    if not for_partner:
        payload.update(
            {
                "partner_id": str(row.partner_id),
                "version": row.version,
                "created_by": str(row.created_by_id),
                "approved_by": str(row.approved_by_id) if row.approved_by_id else None,
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                "approval_reason": row.approval_reason,
                "paid_reference": row.paid_reference,
                "cancel_reason": row.cancel_reason,
            }
        )
    return payload


def payouts_page(
    *,
    partner_id: UUID | None,
    for_partner: bool,
    status: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> Page:
    from workeasy360.commercial.commissions.models import Payout

    limit = _limit(limit)
    queryset = Payout.objects.order_by("-created_at", "-id")
    if partner_id is not None:
        queryset = queryset.filter(partner_id=partner_id)
    if for_partner:
        # A partner does not see a payout still being prepared or one that was
        # cancelled before it meant anything.
        queryset = queryset.exclude(status=PayoutStatus.DRAFT.value)
    if status == OPEN_PAYOUTS:
        # "Still awaiting a decision or a payment" - the Partners overview's
        # "pending payouts" card. Not a status a payout can have.
        queryset = queryset.filter(
            status__in=[
                PayoutStatus.DRAFT.value,
                PayoutStatus.READY.value,
                PayoutStatus.APPROVED.value,
            ]
        )
    elif status:
        queryset = queryset.filter(status=status)
    decoded = decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(created_at__lt=stamp) | Q(created_at=stamp, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page(
        items=[payout_dict(r, for_partner=for_partner) for r in rows],
        next_cursor=encode_cursor(rows[-1].created_at, rows[-1].id) if has_more else None,
        has_more=has_more,
    )


def pending_payout_summary() -> list[dict[str, Any]]:
    """Payouts awaiting a decision or a payment, by status and currency."""
    from workeasy360.commercial.commissions.models import Payout

    return [
        {
            "status": r["status"],
            "currency": r["currency"],
            "count": r["n"],
            "payable_minor": r["total"] or 0,
        }
        for r in Payout.objects.filter(
            status__in=[
                PayoutStatus.DRAFT.value,
                PayoutStatus.READY.value,
                PayoutStatus.APPROVED.value,
            ]
        )
        .values("status", "currency")
        .annotate(n=Count("id"), total=Sum("payable_minor"))
        .order_by("status", "currency")
    ]


# --- referrals ------------------------------------------------------------


def referral_summary(partner_id: UUID) -> dict[str, Any]:
    """Counts by status for one partner's referrals. No prospect is identifiable."""
    from workeasy360.commercial.commissions.models import Referral

    counts = {
        r["status"]: r["n"]
        for r in Referral.objects.filter(partner_id=partner_id)
        .values("status")
        .annotate(n=Count("id"))
    }
    return {s.value: counts.get(s.value, 0) for s in ReferralStatus}


def referral_activity(partner_id: UUID, start: datetime, end: datetime) -> dict[str, Any]:
    """The funnel of one partner's referrals followed inside ``[start, end)``: how
    many visitors followed a link (a *click*: the referral row is made when the
    code is presented) and how many of those have since become customers (a
    *conversion*), per code and per day. Two grouped queries.

    A cohort, not two clocks: every figure counts referrals **captured** in the
    window, so a conversion belongs to the day its visitor first followed the
    link and the rate can never exceed one. Nothing here identifies a visitor.
    """
    from django.db.models.functions import TruncDate

    from workeasy360.commercial.commissions.models import Referral

    window = Referral.objects.filter(
        partner_id=partner_id, captured_at__gte=start, captured_at__lt=end
    )
    converted = Q(status=ReferralStatus.CONVERTED.value)
    # ADR-053: unique visitors (distinct cookie hashes) and registrations (the
    # visitor created an account) between the click and the conversion.
    counts = {
        "clicks": Count("id"),
        "visitors": Count("visitor_hash", distinct=True, filter=~Q(visitor_hash="")),
        "registrations": Count("id", filter=Q(registered_at__isnull=False) | converted),
        # ADR-054: a converted customer that has started a product trial.
        "trials": Count("id", filter=Q(trial_started_at__isnull=False)),
        "converted": Count("id", filter=converted),
    }
    by_code = {
        str(r["code_id"]): {
            "clicks": r["clicks"],
            "visitors": r["visitors"],
            "registrations": r["registrations"],
            "trials": r["trials"],
            "conversions": r["converted"],
        }
        for r in window.values("code_id").annotate(**counts)
    }
    by_day = [
        {
            "date": r["day"].isoformat(),
            "clicks": r["clicks"],
            "visitors": r["visitors"],
            "registrations": r["registrations"],
            "trials": r["trials"],
            "conversions": r["converted"],
        }
        for r in window.annotate(day=TruncDate("captured_at"))
        .values("day")
        .annotate(**counts)
        .order_by("day")
    ]
    totals = window.aggregate(**counts)
    return {
        "by_code": by_code,
        "by_day": by_day,
        "totals": {
            "clicks": totals["clicks"] or 0,
            "visitors": totals["visitors"] or 0,
            "registrations": totals["registrations"] or 0,
            "trials": totals["trials"] or 0,
            "conversions": totals["converted"] or 0,
        },
    }


def referral_earnings(
    partner_id: UUID, start: datetime | None = None, end: datetime | None = None
) -> dict[str, Any]:
    """What a partner's referrals sold and earned, from the ledger (ADR-053):
    per link, per product and in total, by currency. ``sales_minor`` is the
    sale amount the commission was computed on (first sales and renewals);
    ``commission_minor`` nets every accrual, reversal and adjustment. Two
    queries: the ledger rows, and the referrals they name."""
    from workeasy360.commercial.commissions.models import CommissionAccrual, Referral

    rows = CommissionAccrual.objects.filter(
        partner_id=partner_id, attribution_kind="referral"
    )
    if start is not None:
        rows = rows.filter(event_at__gte=start)
    if end is not None:
        rows = rows.filter(event_at__lt=end)
    entries = list(
        rows.values(
            "kind",
            "amount_minor",
            "currency",
            "basis_minor",
            "product_code",
            "attribution_ref",
        )
    )
    refs = [e["attribution_ref"] for e in entries if _is_uuid(e["attribution_ref"])]
    code_of = {
        str(r["id"]): str(r["code_id"])
        for r in Referral.objects.filter(partner_id=partner_id, id__in=refs).values(
            "id", "code_id"
        )
    }
    by_code: dict[str, dict[str, dict[str, int]]] = {}
    by_product: dict[str, dict[str, dict[str, int]]] = {}
    totals: dict[str, dict[str, dict[str, int]]] = {}
    for e in entries:
        _bucket(totals, "all", e)
        if e["product_code"]:
            _bucket(by_product, e["product_code"], e)
        code = code_of.get(e["attribution_ref"])
        if code:
            _bucket(by_code, code, e)
    return {
        "by_code": by_code,
        "by_product": by_product,
        "totals": totals.get("all", {}),
    }


def _bucket(
    table: dict[str, dict[str, dict[str, int]]], key: str, entry: dict[str, Any]
) -> None:
    cell = table.setdefault(key, {}).setdefault(
        entry["currency"], {"sales_minor": 0, "commission_minor": 0, "sales": 0}
    )
    cell["commission_minor"] += entry["amount_minor"]
    if entry["kind"] == AccrualKind.ACCRUAL.value:
        cell["sales_minor"] += entry["basis_minor"] or 0
        cell["sales"] += 1


def _is_uuid(value: Any) -> bool:
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True


def campaigns(partner_id: UUID) -> list[dict[str, Any]]:
    """A partner's campaigns (ADR-053), newest first, with their link counts."""
    from workeasy360.commercial.commissions.models import ReferralCampaign

    return [
        {
            "id": str(c.id),
            "name": c.name,
            "status": c.status,
            "links": c.n,
            "created_at": c.created_at.isoformat(),
        }
        for c in ReferralCampaign.objects.filter(partner_id=partner_id)
        .annotate(n=Count("codes"))
        .order_by("-created_at")
    ]


def referral_codes(partner_id: UUID) -> list[dict[str, Any]]:
    from workeasy360.commercial.commissions.models import ReferralCode

    return [
        {
            "id": str(c.id),
            "code": c.code,
            "label": c.label,
            "status": c.status,
            "product_code": c.product_code,
            "plan_code": c.plan_code,
            "link_kind": c.link_kind,
            "update_id": str(c.update_id) if c.update_id else None,
            "update_title": c.update.title if c.update_id else None,
            "campaign_id": str(c.campaign_id) if c.campaign_id else None,
            "campaign_name": c.campaign.name if c.campaign_id else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in ReferralCode.objects.filter(partner_id=partner_id)
        .select_related("campaign", "update")
        # ADR-054: as many as a partner may hold active, plus recent history.
        .order_by("-created_at")[:200]
    ]


def referrals_page(
    partner_id: UUID,
    *,
    status: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> Page:
    """A partner's referrals. The row identifies nothing about the prospect."""
    from workeasy360.commercial.commissions.models import Referral

    limit = _limit(limit)
    queryset = Referral.objects.filter(partner_id=partner_id).select_related("code")
    if status:
        queryset = queryset.filter(status=status)
    queryset = queryset.order_by("-captured_at", "-id")
    decoded = decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(captured_at__lt=stamp) | Q(captured_at=stamp, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page(
        items=[
            {
                "id": str(r.id),
                "code": r.code.code,
                "label": r.code.label,
                "product_code": r.code.product_code,
                "status": r.status,
                "captured_at": r.captured_at.isoformat(),
                "registered_at": (
                    r.registered_at.isoformat() if r.registered_at else None
                ),
                "converted_at": r.converted_at.isoformat() if r.converted_at else None,
            }
            for r in rows
        ],
        next_cursor=encode_cursor(rows[-1].captured_at, rows[-1].id)
        if has_more
        else None,
        has_more=has_more,
    )


def referral_trail(partner_id: UUID, referral_id: UUID) -> dict[str, Any] | None:
    """How one referral became earnings, step by step (ADR-054): the link
    followed (its kind, product, update and campaign), when the visitor
    clicked, registered, became a customer and started a trial, then each
    paid sale and what it earned - and what became of the earning. Nothing
    identifies the customer: it is shown as a short reference derived from
    the referral, never its name, email or organization id. Three queries."""
    import hashlib

    from workeasy360.commercial.commissions.models import CommissionAccrual, Referral

    r = (
        Referral.objects.filter(id=referral_id, partner_id=partner_id)
        .select_related("code", "code__campaign", "code__update")
        .first()
    )
    if r is None:
        return None
    code = r.code
    entries = (
        list(
            CommissionAccrual.objects.filter(
                partner_id=partner_id, organization_id=r.organization_id
            ).order_by("event_at", "id")
        )
        if r.organization_id
        else []
    )
    # Only earnings this referral produced, and their reversals.
    own = {
        e.id
        for e in entries
        if e.attribution_kind == "referral" and e.attribution_ref == str(r.id)
    }
    statuses = entry_statuses([e for e in entries if e.id in own])
    sales = [
        {
            "id": str(e.id),
            "product_code": e.product_code,
            "plan_code": e.plan_code,
            "renewal": e.source_ref.startswith("payments.invoice_line:"),
            "paid_at": e.event_at.isoformat(),
            "payment_verified": bool(e.payment_ref),
            "sale_minor": e.basis_minor or 0,
            "commission_minor": e.amount_minor,
            "currency": e.currency,
            "rate_bp": e.rate_bp,
            "rule_version": e.rule_version,
            "clears_at": e.clears_at.isoformat(),
            **statuses[e.id],
        }
        for e in entries
        if e.id in own and e.kind == AccrualKind.ACCRUAL.value
    ]
    reversals = [
        {
            "reverses": str(e.reverses_id),
            "amount_minor": e.amount_minor,
            "currency": e.currency,
            "reason_code": e.reason_code,
            "at": e.event_at.isoformat(),
        }
        for e in entries
        if e.reverses_id in own
    ]
    return {
        "id": str(r.id),
        "customer_ref": (
            "C-" + hashlib.sha256(str(r.id).encode()).hexdigest()[:8].upper()
            if r.organization_id
            else None
        ),
        "attribution_rule": r.attribution_rule,
        "link": {
            "id": str(code.id),
            "code": code.code,
            "label": code.label,
            "link_kind": code.link_kind,
            "product_code": code.product_code,
            "plan_code": code.plan_code,
            "update_title": code.update.title if code.update_id else None,
            "campaign_name": code.campaign.name if code.campaign_id else None,
        },
        "steps": [
            {"step": "clicked", "at": r.captured_at.isoformat()},
            {"step": "registered", "at": _iso(r.registered_at)},
            {"step": "customer", "at": _iso(r.converted_at)},
            {"step": "trial", "at": _iso(r.trial_started_at)},
            {"step": "purchased", "at": sales[0]["paid_at"] if sales else None},
        ],
        "expires_at": r.expires_at.isoformat(),
        "status": r.status,
        "sales": sales,
        "reversals": reversals,
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def converted_organization_ids() -> list[UUID]:
    """Customers that came in through a referral - candidates for accrual."""
    from workeasy360.commercial.commissions.models import Referral

    return list(
        Referral.objects.filter(status=ReferralStatus.CONVERTED.value).values_list(
            "organization_id", flat=True
        )
    )


def referral_conversion_for(organization_id: UUID, moment: datetime) -> Any | None:
    """The converted referral attributing this customer, if it was in force."""
    from workeasy360.commercial.commissions.models import Referral

    return (
        Referral.objects.filter(
            organization_id=organization_id,
            status=ReferralStatus.CONVERTED.value,
            converted_at__lte=moment,
        )
        .select_related("code")
        .first()
    )


# --- what the partner consoles show (Phase 10) ------------------------------
#
# Staff read the ledger and the payouts of many partners at once. These answer
# for exactly the partners on a page - one grouped query each, never one per
# partner - and hand back plain dicts, so ``controlplane`` composes them with
# the partner-owned reads without touching this module's tables (F-6).

_OPEN_PAYOUT_STATES = (
    PayoutStatus.DRAFT.value,
    PayoutStatus.READY.value,
    PayoutStatus.APPROVED.value,
)


def _unpaid_entries() -> Any:
    """Ledger rows not in any live payout: not yet claimed by a payout, or
    released from a cancelled one. A paid payout's lines stay live, so what has
    been paid is not counted as pending."""
    from django.db.models import Exists, OuterRef

    from workeasy360.commercial.commissions.models import CommissionAccrual, PayoutLine

    return CommissionAccrual.objects.filter(
        ~Exists(
            PayoutLine.objects.filter(accrual=OuterRef("pk"), released_at__isnull=True)
        )
    )


def unpaid_by_currency() -> list[dict[str, Any]]:
    """Commission earned and not yet in a live payout, per currency. One query."""
    return [
        {"currency": r["currency"], "amount_minor": r["total"] or 0, "entries": r["n"]}
        for r in _unpaid_entries()
        .values("currency")
        .annotate(n=Count("id"), total=Sum("amount_minor"))
        .order_by("currency")
    ]


def open_payouts_by_currency() -> list[dict[str, Any]]:
    """Payouts still to be decided or paid (draft, ready, approved), per currency."""
    from workeasy360.commercial.commissions.models import Payout

    return [
        {"currency": r["currency"], "amount_minor": r["total"] or 0, "entries": r["n"]}
        for r in Payout.objects.filter(status__in=_OPEN_PAYOUT_STATES)
        .values("currency")
        .annotate(n=Count("id"), total=Sum("payable_minor"))
        .order_by("currency")
    ]


def ledger_facts(partner_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    """Per partner: the ledger balance and the still-unpaid part of it in each
    currency, and when the ledger last moved. Two queries for any number of
    partners; a partner with no entries is absent."""
    from django.db.models import Max

    from workeasy360.commercial.commissions.models import CommissionAccrual

    if not partner_ids:
        return {}
    ledger = {
        (row["partner_id"], row["currency"]): row
        for row in CommissionAccrual.objects.filter(partner_id__in=partner_ids)
        .values("partner_id", "currency")
        .annotate(balance=Sum("amount_minor"), entries=Count("id"), last=Max("event_at"))
    }
    unpaid = {
        (row["partner_id"], row["currency"]): row["total"] or 0
        for row in _unpaid_entries()
        .filter(partner_id__in=partner_ids)
        .values("partner_id", "currency")
        .annotate(total=Sum("amount_minor"))
    }
    out: dict[UUID, dict[str, Any]] = {}
    for (partner_id, currency), row in sorted(ledger.items(), key=lambda kv: str(kv[0])):
        entry = out.setdefault(partner_id, {"commission": [], "last_at": None})
        entry["commission"].append(
            {
                "currency": currency,
                "balance_minor": row["balance"] or 0,
                "unpaid_minor": unpaid.get((partner_id, currency), 0),
                "entries": row["entries"],
            }
        )
        if row["last"] is not None and (
            entry["last_at"] is None or row["last"] > entry["last_at"]
        ):
            entry["last_at"] = row["last"]
    return out


def payout_facts(partner_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    """Per partner: how many payouts are open and its most recent payout. Two
    queries for any number of partners; a partner with no payout is absent."""
    from workeasy360.commercial.commissions.models import Payout

    if not partner_ids:
        return {}
    out: dict[UUID, dict[str, Any]] = {}
    for row in (
        Payout.objects.filter(partner_id__in=partner_ids, status__in=_OPEN_PAYOUT_STATES)
        .values("partner_id")
        .annotate(n=Count("id"))
    ):
        out[row["partner_id"]] = {"open": row["n"], "latest": None}
    for payout in (
        Payout.objects.filter(partner_id__in=partner_ids)
        .order_by("partner_id", "-created_at", "-id")
        .distinct("partner_id")
    ):
        entry = out.setdefault(payout.partner_id, {"open": 0, "latest": None})
        entry["latest"] = {
            "id": str(payout.id),
            "status": payout.status,
            "currency": payout.currency,
            "period_end": payout.period_end.isoformat(),
            "payable_minor": payout.payable_minor,
        }
    return out


def payout_status_counts(partner_id: UUID) -> dict[str, int]:
    """One partner's payouts by status. One query."""
    from workeasy360.commercial.commissions.models import Payout

    return {
        r["status"]: r["n"]
        for r in Payout.objects.filter(partner_id=partner_id)
        .values("status")
        .annotate(n=Count("id"))
    }


# --- what the affiliate list shows (Partner Control Center) ------------------


def referral_facts(partner_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    """Per partner: its active referral codes (newest first, at most three),
    and how many referrals were captured and converted. Two queries for any
    number of partners; a partner with no code and no referral is absent.

    Counts only - a referral row identifies nothing about the prospect."""
    from workeasy360.commercial.commissions.models import Referral, ReferralCode
    from workeasy360.contracts.enums import GrantStatus

    if not partner_ids:
        return {}
    out: dict[UUID, dict[str, Any]] = {}

    def entry(partner_id: UUID) -> dict[str, Any]:
        return out.setdefault(
            partner_id, {"codes": [], "active_codes": 0, "captured": 0, "converted": 0}
        )

    for code in ReferralCode.objects.filter(
        partner_id__in=partner_ids, status=GrantStatus.ACTIVE.value
    ).order_by("partner_id", "-created_at", "-id"):
        found = entry(code.partner_id)
        found["active_codes"] += 1
        if len(found["codes"]) < 3:
            found["codes"].append(code.code)
    for row in (
        Referral.objects.filter(partner_id__in=partner_ids)
        .values("partner_id", "status")
        .annotate(n=Count("id"))
    ):
        found = entry(row["partner_id"])
        found["captured"] += row["n"]
        if row["status"] == ReferralStatus.CONVERTED.value:
            found["converted"] += row["n"]
    return out


def paid_facts(partner_ids: list[UUID]) -> dict[UUID, list[dict[str, Any]]]:
    """Per partner and currency: what payouts confirmed as paid amounted to.
    One query; never added across currencies."""
    from workeasy360.commercial.commissions.models import Payout

    if not partner_ids:
        return {}
    out: dict[UUID, list[dict[str, Any]]] = {}
    for row in (
        Payout.objects.filter(partner_id__in=partner_ids, status=PayoutStatus.PAID.value)
        .values("partner_id", "currency")
        .annotate(total=Sum("payable_minor"), n=Count("id"))
        .order_by("partner_id", "currency")
    ):
        out.setdefault(row["partner_id"], []).append(
            {
                "currency": row["currency"],
                "amount_minor": row["total"] or 0,
                "payouts": row["n"],
            }
        )
    return out


# --- the partner's own money picture (ADR-045) -------------------------------


def partner_money(partner_id: UUID, now: datetime) -> list[dict[str, Any]]:
    """One partner's ledger broken down by what can happen to it next, per
    currency (never added across currencies). Four queries at most.

    - ``balance``: the sum of every ledger row - the ledger *is* the balance;
    - ``pending``: still inside the holdback (``clears_at`` in the future);
    - ``available``: cleared and in no live payout - what a payout can claim;
    - ``in_payouts``: in a payout still to be decided or paid (draft, ready,
      approved);
    - ``paid``: in a payout confirmed as paid.

    Reading only: nothing here writes to the ledger, and the browser never sends
    a figure back. ``balance`` equals ``pending + available + in_payouts + paid``
    except for rows in a cancelled payout (released), which return to the pool.
    """
    from workeasy360.commercial.commissions.models import (
        CommissionAccrual,
        Payout,
        PayoutLine,
    )

    totals: dict[str, dict[str, Any]] = {}

    def row(currency: str) -> dict[str, Any]:
        return totals.setdefault(
            currency,
            {
                "currency": currency,
                "balance_minor": 0,
                "pending_minor": 0,
                "available_minor": 0,
                "in_payouts_minor": 0,
                "paid_minor": 0,
                "entries": 0,
            },
        )

    for r in (
        CommissionAccrual.objects.filter(partner_id=partner_id)
        .values("currency")
        .annotate(total=Sum("amount_minor"), n=Count("id"))
    ):
        entry = row(r["currency"])
        entry["balance_minor"] = r["total"] or 0
        entry["entries"] = r["n"]
    for r in (
        CommissionAccrual.objects.filter(partner_id=partner_id, clears_at__gt=now)
        .values("currency")
        .annotate(total=Sum("amount_minor"))
    ):
        row(r["currency"])["pending_minor"] = r["total"] or 0
    for r in (
        _unpaid_entries()
        .filter(partner_id=partner_id, clears_at__lte=now)
        .values("currency")
        .annotate(total=Sum("amount_minor"))
    ):
        row(r["currency"])["available_minor"] = r["total"] or 0
    for r in (
        PayoutLine.objects.filter(
            payout__partner_id=partner_id,
            released_at__isnull=True,
            payout__status__in=_OPEN_PAYOUT_STATES,
        )
        .values("payout__currency")
        .annotate(total=Sum("amount_minor"))
    ):
        row(r["payout__currency"])["in_payouts_minor"] = r["total"] or 0
    for r in (
        Payout.objects.filter(partner_id=partner_id, status=PayoutStatus.PAID.value)
        .values("currency")
        .annotate(total=Sum("payable_minor"))
    ):
        row(r["currency"])["paid_minor"] = r["total"] or 0
    return [totals[key] for key in sorted(totals)]


def partner_sales_by_day(
    partner_id: UUID, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Sales and commission per day and currency inside ``[start, end)``, from
    the ledger. One query.

    A *sale* is an ``accrual`` row; its amount is the snapshotted basis. The
    commission figure is net of reversals and adjustments in the same day. The
    ledger only records a sale on which this partner earns, so a sale that earns
    nothing is not here - the screens say so rather than imply it is complete.
    """
    from django.db.models.functions import TruncDate

    from workeasy360.commercial.commissions.models import CommissionAccrual

    return [
        {
            "date": r["day"].isoformat(),
            "currency": r["currency"],
            "basis_currency": r["basis_currency"],
            "sales": r["sales"] or 0,
            "gross_minor": r["gross"] or 0,
            "commission_minor": r["commission"] or 0,
        }
        for r in CommissionAccrual.objects.filter(
            partner_id=partner_id, event_at__gte=start, event_at__lt=end
        )
        .annotate(day=TruncDate("event_at"))
        .values("day", "currency", "basis_currency")
        .annotate(
            sales=Count("id", filter=Q(kind=AccrualKind.ACCRUAL.value)),
            gross=Sum("basis_minor", filter=Q(kind=AccrualKind.ACCRUAL.value)),
            commission=Sum("amount_minor"),
        )
        .order_by("day", "currency")
    ]


def partner_conversions_page(
    partner_id: UUID, *, limit: int = 25, cursor: str | None = None
) -> Page:
    """Converted referrals of one partner, newest first: when the visitor became
    a customer, the product the customer holds and the commission it earned -
    and nothing that identifies the customer (the referral row carries none)."""
    from workeasy360.commercial.commissions.models import (
        CommissionAccrual,
        Referral,
    )

    limit = _limit(limit)
    queryset = Referral.objects.filter(
        partner_id=partner_id, status=ReferralStatus.CONVERTED.value
    ).select_related("code")
    decoded = decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(converted_at__lt=stamp) | Q(converted_at=stamp, id__lt=last_id)
        )
    rows = list(queryset.order_by("-converted_at", "-id")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    earned = {
        r["organization_id"]: (r["total"] or 0, r["currency"])
        for r in CommissionAccrual.objects.filter(
            partner_id=partner_id,
            organization_id__in=[r.organization_id for r in rows],
        )
        .values("organization_id", "currency")
        .annotate(total=Sum("amount_minor"))
    }
    # ADR-053: what each converted customer bought through the referral - the
    # products and plans on its ledger rows and the sale amounts, never who it is.
    sold: dict[Any, dict[str, Any]] = {}
    for a in CommissionAccrual.objects.filter(
        partner_id=partner_id,
        kind=AccrualKind.ACCRUAL.value,
        attribution_kind="referral",
        organization_id__in=[r.organization_id for r in rows],
    ).order_by("event_at"):
        entry = sold.setdefault(a.organization_id, {"products": [], "sales_minor": 0})
        if a.product_code and a.product_code not in [
            p["product_code"] for p in entry["products"]
        ]:
            entry["products"].append(
                {"product_code": a.product_code, "plan_code": a.plan_code}
            )
        entry["sales_minor"] += a.basis_minor or 0
    items = []
    for r in rows:
        amount, currency = earned.get(r.organization_id, (0, ""))
        sale = sold.get(r.organization_id, {"products": [], "sales_minor": 0})
        items.append(
            {
                "id": str(r.id),
                "code": r.code.code,
                "code_label": r.code.label,
                "captured_at": r.captured_at.isoformat(),
                "registered_at": (
                    r.registered_at.isoformat() if r.registered_at else None
                ),
                "converted_at": r.converted_at.isoformat() if r.converted_at else None,
                "products": sale["products"],
                "sales_minor": sale["sales_minor"],
                "status": "purchased" if sale["products"] else "signed_up",
                "commission_minor": amount,
                "currency": currency,
            }
        )
    return Page(
        items=items,
        next_cursor=(
            encode_cursor(rows[-1].converted_at, rows[-1].id) if has_more else None
        ),
        has_more=has_more,
    )


def earnings_by_item(partner_id: UUID, item_ids: list[str]) -> dict[str, dict[str, Any]]:
    """What each subscription line has earned this partner on the ledger
    (ADR-053): net amount (accruals less reversals), the partner's cost for a
    margin sale, and which earning model applied - by subscription line id."""
    from workeasy360.commercial.commissions.models import CommissionAccrual

    if not item_ids:
        return {}
    refs = {f"subscriptions.item:{i}": i for i in item_ids}
    rows = list(
        CommissionAccrual.objects.filter(
            partner_id=partner_id, kind=AccrualKind.ACCRUAL.value, source_ref__in=refs
        ).values("id", "source_ref", "amount_minor", "currency", "earning", "cost_minor")
    )
    reversed_by = {
        r["reverses_id"]: r["total"]
        for r in CommissionAccrual.objects.filter(reverses_id__in=[r["id"] for r in rows])
        .values("reverses_id")
        .annotate(total=Sum("amount_minor"))
    }
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        item = refs[r["source_ref"]]
        out[item] = {
            "earning": r["earning"],
            "amount_minor": r["amount_minor"] + (reversed_by.get(r["id"]) or 0),
            "cost_minor": r["cost_minor"],
            "currency": r["currency"],
        }
    return out
