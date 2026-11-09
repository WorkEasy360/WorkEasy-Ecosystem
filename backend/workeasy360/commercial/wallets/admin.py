"""The Admin console's commands and reads on wallets.

``platform.staff_wallet.view`` to read. ``platform.staff_wallet.manage`` (dangerous) for
anything that moves a balance or changes what is charged, each audited with who and why.
Rates and packs are read behind ``platform.staff_catalog.view`` and written behind
``platform.staff_pricing.manage``, because they are prices.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from workeasy360.commercial.wallets import ledger, selectors, usage
from workeasy360.commercial.wallets.models import (
    CreditPack,
    UsageEvent,
    UsageRate,
    Wallet,
    WalletEntry,
)
from workeasy360.contracts.errors import CatalogValidationError, CommercialRecordNotFound
from workeasy360.contracts.values.cursor import decode_cursor, encode_cursor
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW = "platform.staff_wallet.view"
MANAGE = "platform.staff_wallet.manage"
PRICES_VIEW = "platform.staff_catalog.view"
PRICES_MANAGE = "platform.staff_pricing.manage"
MAX_PAGE = 100
MAX_ADJUSTMENT = 100_000_000_00


def _limit(raw: Any) -> int:
    try:
        return max(1, min(int(raw or 25), MAX_PAGE))
    except (TypeError, ValueError):
        return 25


def _reason(raw: Any) -> str:
    why = str(raw or "").strip()
    if len(why) < 10 or len(why) > 255:
        raise CatalogValidationError("Say why, in 10 to 255 characters.", field="reason")
    return why


def _key(raw: Any) -> str:
    key = str(raw or "").strip()
    if not key or len(key) > 100:
        raise CatalogValidationError("A command id is required.", field="idempotency_key")
    return key


def _whole(raw: Any, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise CatalogValidationError("Enter a whole number.", field=field)
    return raw


# --- wallets ------------------------------------------------------------------------


def wallet_row(row: Wallet) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "kind_label": row.get_kind_display(),
        "owner_type": row.owner_type,
        "owner_id": str(row.owner_id),
        "currency": row.currency,
        "balance": row.balance,
        "low_balance_threshold": row.low_balance_threshold,
        "is_low": row.is_low,
        "updated_at": row.updated_at.isoformat(),
    }


def wallets(
    ctx: TenantContext,
    *,
    kind: Any = None,
    owner_type: Any = None,
    owner_ids: Any = None,
    low: bool = False,
    limit: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    rows = selectors.low_balance_rows() if low else Wallet.objects.all()
    if kind:
        if kind not in Wallet.Kind.values:
            raise CatalogValidationError("Unknown wallet.", field="kind")
        rows = rows.filter(kind=kind)
    if owner_type:
        if owner_type not in Wallet.OwnerType.values:
            raise CatalogValidationError("Unknown owner.", field="owner_type")
        rows = rows.filter(owner_type=owner_type)
    if owner_ids is not None:
        rows = rows.filter(owner_id__in=owner_ids)
    size = _limit(limit)
    after = decode_cursor(cursor)
    if after is not None:
        rows = rows.filter(
            Q(created_at__lt=after[0]) | Q(created_at=after[0], id__lt=after[1])
        )
    page = list(rows.order_by("-created_at", "-id")[: size + 1])
    more = len(page) > size
    page = page[:size]
    return {
        "results": [wallet_row(r) for r in page],
        "next_cursor": encode_cursor(page[-1].created_at, page[-1].id)
        if more and page
        else None,
        "has_more": more,
        "totals": selectors.platform_totals(None),
    }


def wallet(ctx: TenantContext, wallet_id: UUID) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    row = Wallet.objects.filter(id=wallet_id).first()
    if row is None:
        raise CommercialRecordNotFound("No such wallet.")
    return wallet_row(row)


@in_scope
def open_wallet(
    ctx: TenantContext, *, owner_type: Any, owner_id: UUID, kind: Any, currency: Any
) -> dict[str, Any]:
    """Open a wallet for an owner a caller has checked exists (at nothing)."""
    authz.require(ctx, MANAGE)
    if kind not in Wallet.Kind.values or owner_type not in Wallet.OwnerType.values:
        raise CatalogValidationError("Choose an owner and a wallet.", field="kind")
    if owner_type == Wallet.OwnerType.PARTNER and kind != Wallet.Kind.PLATFORM:
        raise CatalogValidationError(
            "A partner holds a Platform Wallet only: commissions are earnings, not a balance.",
            field="kind",
        )
    code = str(currency or "").strip().upper()
    uses_money = kind != Wallet.Kind.AI_CREDITS
    if uses_money and (len(code) != 3 or not code.isalpha()):
        raise CatalogValidationError("Choose a currency.", field="currency")
    row = ledger.ensure(
        owner_type=owner_type,
        owner_id=owner_id,
        kind=kind,
        currency=code if uses_money else "",
    )
    return wallet_row(row)


@in_scope
def set_threshold(ctx: TenantContext, wallet_id: UUID, threshold: Any) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    value = (
        None if threshold in (None, "") else _whole(threshold, "low_balance_threshold")
    )
    if value is not None and value < 0:
        raise CatalogValidationError(
            "A threshold cannot be negative.", field="low_balance_threshold"
        )
    with transaction.atomic():
        row = Wallet.objects.select_for_update().filter(id=wallet_id).first()
        if row is None:
            raise CommercialRecordNotFound("No such wallet.")
        row.low_balance_threshold = value
        row.save(update_fields=["low_balance_threshold", "updated_at"])
        audit.record(
            ctx,
            "wallet.threshold_set",
            resource_type="wallets.wallet",
            resource_id=row.id,
            changes={"low_balance_threshold": value},
        )
    return wallet_row(row)


@in_scope
def record_offline_top_up(
    ctx: TenantContext,
    wallet_id: UUID,
    *,
    amount: Any,
    reference: Any,
    reason: Any,
    idempotency_key: Any,
) -> dict[str, Any]:
    """Money that arrived outside the platform (a bank transfer) into a wallet, with
    its reference. A partner's top-up comes this way: a partner has no order."""
    authz.require(ctx, MANAGE)
    value = _whole(amount, "amount")
    if not 0 < value <= MAX_ADJUSTMENT:
        raise CatalogValidationError("Enter the amount received.", field="amount")
    note = str(reference or "").strip()[:64]
    if not note:
        raise CatalogValidationError("Give the payment's reference.", field="reference")
    why = _reason(reason)
    with transaction.atomic():
        row = Wallet.objects.filter(id=wallet_id).first()
        if row is None:
            raise CommercialRecordNotFound("No such wallet.")
        if row.kind == Wallet.Kind.AI_CREDITS:
            raise CatalogValidationError(
                "AI Credits come from credit packs.", field="wallet"
            )
        entry = ledger.post(
            row.id,
            amount=value,
            entry_type=WalletEntry.Type.TOP_UP,
            reason=why,
            idempotency_key=f"offline:{_key(idempotency_key)}",
            actor_user_id=ctx.user_id,
            source_type="offline_payment",
            source_id=note,
        )
        audit.record(
            ctx,
            "wallet.offline_top_up",
            resource_type="wallets.wallet",
            resource_id=row.id,
            changes={"amount": value, "reference": note, "entry": str(entry.id)},
            reason=why,
        )
    return entry_row(entry)


@in_scope
def adjust(
    ctx: TenantContext, wallet_id: UUID, *, amount: Any, reason: Any, idempotency_key: Any
) -> dict[str, Any]:
    """An audited manual correction, up or down. Never below zero."""
    authz.require(ctx, MANAGE)
    value = _whole(amount, "amount")
    if value == 0 or abs(value) > MAX_ADJUSTMENT:
        raise CatalogValidationError("Enter the amount to adjust by.", field="amount")
    why = _reason(reason)
    with transaction.atomic():
        entry = ledger.post(
            wallet_id,
            amount=value,
            entry_type=WalletEntry.Type.ADJUSTMENT,
            reason=why,
            idempotency_key=f"adjust:{_key(idempotency_key)}",
            actor_user_id=ctx.user_id,
            source_type="manual",
        )
        audit.record(
            ctx,
            "wallet.adjusted",
            resource_type="wallets.wallet",
            resource_id=wallet_id,
            changes={"amount": value, "entry": str(entry.id)},
            reason=why,
        )
    return entry_row(entry)


@in_scope
def reverse_entry(
    ctx: TenantContext,
    entry_id: UUID,
    *,
    amount: Any = None,
    reason: Any,
    idempotency_key: Any,
) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    why = _reason(reason)
    value = None if amount in (None, "") else _whole(amount, "amount")
    with transaction.atomic():
        entry = ledger.reverse(
            entry_id,
            reason=why,
            idempotency_key=f"reverse:{_key(idempotency_key)}",
            amount=value,
            actor_user_id=ctx.user_id,
            source_type="manual",
        )
        audit.record(
            ctx,
            "wallet.reversed",
            resource_type="wallets.entry",
            resource_id=entry_id,
            changes={"amount": entry.amount, "reversal": str(entry.id)},
            reason=why,
        )
    return entry_row(entry)


def entry_row(row: WalletEntry) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "wallet_id": str(row.wallet_id),
        "type": row.entry_type,
        "type_label": row.get_entry_type_display(),
        "amount": row.amount,
        "balance_after": row.balance_after,
        "reason": row.reason,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "reverses": str(row.reverses_id) if row.reverses_id else None,
        "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
        "created_at": row.created_at.isoformat(),
    }


def entries(
    ctx: TenantContext,
    *,
    wallet_id: UUID | None = None,
    kind: Any = None,
    entry_type: Any = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    rows = WalletEntry.objects.select_related("wallet")
    if wallet_id is not None:
        rows = rows.filter(wallet_id=wallet_id)
    if kind:
        if kind not in Wallet.Kind.values:
            raise CatalogValidationError("Unknown wallet.", field="kind")
        rows = rows.filter(wallet__kind=kind)
    if entry_type:
        if entry_type not in WalletEntry.Type.values:
            raise CatalogValidationError("Unknown type.", field="type")
        rows = rows.filter(entry_type=entry_type)
    if start is not None:
        rows = rows.filter(created_at__gte=start)
    if end is not None:
        rows = rows.filter(created_at__lt=end)
    size = _limit(limit)
    after = decode_cursor(cursor)
    if after is not None:
        rows = rows.filter(
            Q(created_at__lt=after[0]) | Q(created_at=after[0], id__lt=after[1])
        )
    page = list(rows.order_by("-created_at", "-id")[: size + 1])
    more = len(page) > size
    page = page[:size]
    return {
        "results": [
            {
                **entry_row(r),
                "wallet_kind": r.wallet.kind,
                "owner_type": r.wallet.owner_type,
                "owner_id": str(r.wallet.owner_id),
                "currency": r.wallet.currency,
            }
            for r in page
        ],
        "next_cursor": encode_cursor(page[-1].created_at, page[-1].id)
        if more and page
        else None,
        "has_more": more,
    }


def reconciliation(ctx: TenantContext) -> dict[str, Any]:
    """Whether every balance equals the sum of its entries. It always should."""
    authz.require(ctx, VIEW)
    mismatches = ledger.reconcile()
    return {"balanced": not mismatches, "mismatches": mismatches}


# --- usage events -----------------------------------------------------------------------


def event_row(row: UsageEvent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id),
        "product_code": row.product_code,
        "meter": row.meter,
        "meter_label": row.get_meter_display(),
        "quantity": row.quantity,
        "occurred_at": row.occurred_at.isoformat(),
        "received_at": row.received_at.isoformat(),
        "outcome": row.outcome,
        "failure_code": row.failure_code,
        "charged_units": row.charged_units,
        "amount": row.amount,
        "currency": row.rate.currency if row.rate_id else "",
        "entry_id": str(row.entry_id) if row.entry_id else None,
        "duplicate_count": row.duplicate_count,
        "last_duplicate_at": row.last_duplicate_at.isoformat()
        if row.last_duplicate_at
        else None,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
    }


def events(
    ctx: TenantContext,
    *,
    outcome: Any = None,
    meter: Any = None,
    duplicates: bool = False,
    unreviewed: bool = False,
    limit: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    rows = UsageEvent.objects.select_related("rate")
    if outcome:
        if outcome not in UsageEvent.Outcome.values:
            raise CatalogValidationError("Unknown outcome.", field="outcome")
        rows = rows.filter(outcome=outcome)
    if meter:
        if meter not in UsageRate.Meter.values:
            raise CatalogValidationError("Unknown meter.", field="meter")
        rows = rows.filter(meter=meter)
    if duplicates:
        rows = rows.filter(duplicate_count__gt=0)
    if unreviewed:
        rows = rows.filter(reviewed_at__isnull=True)
    size = _limit(limit)
    after = decode_cursor(cursor)
    if after is not None:
        rows = rows.filter(
            Q(received_at__lt=after[0]) | Q(received_at=after[0], id__lt=after[1])
        )
    page = list(rows.order_by("-received_at", "-id")[: size + 1])
    more = len(page) > size
    page = page[:size]
    return {
        "results": [event_row(r) for r in page],
        "next_cursor": encode_cursor(page[-1].received_at, page[-1].id)
        if more and page
        else None,
        "has_more": more,
    }


@in_scope
def retry_event(ctx: TenantContext, event_id: UUID) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    row = usage.retry(event_id)
    audit.record(
        ctx,
        "wallet.usage_retried",
        resource_type="wallets.usage_event",
        resource_id=row.id,
        changes={"outcome": row.outcome, "amount": row.amount},
    )
    return event_row(row)


@in_scope
def review_event(ctx: TenantContext, event_id: UUID, *, note: Any) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    text = str(note or "").strip()[:255]
    if len(text) < 3:
        raise CatalogValidationError("Say what you found.", field="note")
    with transaction.atomic():
        row = UsageEvent.objects.select_for_update().filter(id=event_id).first()
        if row is None:
            raise CommercialRecordNotFound("No such event.")
        row.reviewed_at = timezone.now()
        row.reviewed_by_user_id = ctx.user_id
        row.review_note = text
        row.save(update_fields=["reviewed_at", "reviewed_by_user_id", "review_note"])
        audit.record(
            ctx,
            "wallet.usage_reviewed",
            resource_type="wallets.usage_event",
            resource_id=row.id,
            changes={"outcome": row.outcome},
            reason=text,
        )
    return event_row(row)


def usage_by_product(
    start: datetime | None, end: datetime | None
) -> list[dict[str, Any]]:
    """For the Usage report, whose caller has authorized."""
    rows = UsageEvent.objects.all()
    if start is not None:
        rows = rows.filter(occurred_at__gte=start)
    if end is not None:
        rows = rows.filter(occurred_at__lt=end)
    grouped = (
        rows.values("product_code", "meter", "outcome", "rate__currency")
        .annotate(quantity=Sum("quantity"), amount=Sum("amount"))
        .order_by("product_code", "meter", "outcome", "rate__currency")
    )
    return [
        {
            "product_code": row["product_code"],
            "meter": row["meter"],
            "outcome": row["outcome"],
            "quantity": int(row["quantity"] or 0),
            "amount": int(row["amount"] or 0),
            # Minor units of this currency. Empty for AI Credits, which are whole credits.
            "currency": row["rate__currency"] or "",
        }
        for row in grouped
    ]


# --- what things cost -------------------------------------------------------------------


def rate_row(row: UsageRate) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "meter": row.meter,
        "meter_label": row.get_meter_display(),
        "currency": row.currency,
        "unit_amount": row.unit_amount,
        "per_units": row.per_units,
        "included_units": row.included_units,
        "effective_from": row.effective_from.isoformat(),
        "effective_to": row.effective_to.isoformat() if row.effective_to else None,
        "reason": row.reason,
    }


def rates(ctx: TenantContext, *, history: bool = False) -> dict[str, Any]:
    """Every meter with its live rate, including those with none: a meter with no
    rate is shown as not charged rather than hidden."""
    authz.require(ctx, PRICES_VIEW)
    rows = UsageRate.objects.order_by("meter", "currency", "-effective_from")
    if not history:
        rows = rows.filter(effective_to__isnull=True)
    return {
        "meters": [{"meter": m.value, "label": m.label} for m in UsageRate.Meter],
        "results": [rate_row(r) for r in rows],
    }


@in_scope
def set_rate(
    ctx: TenantContext,
    *,
    meter: Any,
    currency: Any,
    unit_amount: Any,
    per_units: Any,
    included_units: Any,
    reason: Any,
) -> dict[str, Any]:
    """Close the live rate for this meter and open this one. Future charges use it;
    past charges keep naming the one they used."""
    authz.require(ctx, PRICES_MANAGE)
    if meter not in UsageRate.Meter.values:
        raise CatalogValidationError("Choose a meter.", field="meter")
    is_ai = meter == UsageRate.Meter.AI_TOKENS
    code = "" if is_ai else str(currency or "").strip().upper()
    if not is_ai and (len(code) != 3 or not code.isalpha()):
        raise CatalogValidationError("Choose a currency.", field="currency")
    amount = _whole(unit_amount, "unit_amount")
    per = _whole(per_units, "per_units")
    free = _whole(included_units if included_units is not None else 0, "included_units")
    if amount <= 0 or per <= 0 or free < 0:
        raise CatalogValidationError("Rates must be above zero.", field="unit_amount")
    why = _reason(reason)
    now = timezone.now()
    with transaction.atomic():
        UsageRate.objects.select_for_update().filter(
            meter=meter, currency=code, effective_to__isnull=True
        ).update(effective_to=now)
        row = UsageRate.objects.create(
            meter=meter,
            currency=code,
            unit_amount=amount,
            per_units=per,
            included_units=free,
            effective_from=now,
            created_by_user_id=ctx.user_id,
            reason=why,
        )
        audit.record(
            ctx,
            "wallet.rate_set",
            resource_type="wallets.usage_rate",
            resource_id=row.id,
            changes={
                "meter": meter,
                "currency": code,
                "unit_amount": amount,
                "per_units": per,
                "included_units": free,
            },
            reason=why,
        )
    return rate_row(row)


@in_scope
def remove_rate(ctx: TenantContext, rate_id: UUID, *, reason: Any) -> None:
    """Stop charging this meter: no live rate means no charge."""
    authz.require(ctx, PRICES_MANAGE)
    why = _reason(reason)
    with transaction.atomic():
        changed = UsageRate.objects.filter(id=rate_id, effective_to__isnull=True).update(
            effective_to=timezone.now()
        )
        if not changed:
            raise CommercialRecordNotFound("No such live rate.")
        audit.record(
            ctx,
            "wallet.rate_removed",
            resource_type="wallets.usage_rate",
            resource_id=rate_id,
            changes={},
            reason=why,
        )


def pack_row(row: CreditPack) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "credits": row.credits,
        "price_minor": row.price_minor,
        "currency": row.currency,
        "status": row.status,
    }


def packs(ctx: TenantContext) -> dict[str, Any]:
    authz.require(ctx, PRICES_VIEW)
    return {
        "results": [pack_row(p) for p in CreditPack.objects.order_by("status", "credits")]
    }


def _clean_pack(body: dict[str, Any]) -> dict[str, Any]:
    name = " ".join(str(body.get("name") or "").split())[:120]
    if not name:
        raise CatalogValidationError("Name the pack.", field="name")
    credits = _whole(body.get("credits"), "credits")
    price = _whole(body.get("price_minor"), "price_minor")
    if credits <= 0 or price <= 0:
        raise CatalogValidationError(
            "Credits and price must be above zero.", field="credits"
        )
    code = str(body.get("currency") or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise CatalogValidationError("Choose a currency.", field="currency")
    return {"name": name, "credits": credits, "price_minor": price, "currency": code}


@in_scope
def create_pack(ctx: TenantContext, body: dict[str, Any]) -> dict[str, Any]:
    import re

    authz.require(ctx, PRICES_MANAGE)
    fields = _clean_pack(body)
    base = re.sub(r"[^a-z0-9]+", "-", fields["name"].lower()).strip("-")[:40] or "pack"
    taken = set(
        CreditPack.objects.filter(code__startswith=base).values_list("code", flat=True)
    )
    code, n = base, 1
    while code in taken:
        n += 1
        code = f"{base}-{n}"
    with transaction.atomic():
        row = CreditPack.objects.create(code=code, **fields)
        audit.record(
            ctx,
            "wallet.pack_created",
            resource_type="wallets.credit_pack",
            resource_id=row.id,
            changes={**fields},
        )
    return pack_row(row)


@in_scope
def update_pack(
    ctx: TenantContext, pack_id: UUID, body: dict[str, Any]
) -> dict[str, Any]:
    authz.require(ctx, PRICES_MANAGE)
    fields = _clean_pack(body)
    status = body.get("status", "active")
    if status not in CreditPack.Status.values:
        raise CatalogValidationError("Unknown status.", field="status")
    with transaction.atomic():
        row = CreditPack.objects.select_for_update().filter(id=pack_id).first()
        if row is None:
            raise CommercialRecordNotFound("No such pack.")
        for name, value in {**fields, "status": status}.items():
            setattr(row, name, value)
        row.save()
        audit.record(
            ctx,
            "wallet.pack_updated",
            resource_type="wallets.credit_pack",
            resource_id=row.id,
            changes={**fields, "status": status},
        )
    return pack_row(row)
