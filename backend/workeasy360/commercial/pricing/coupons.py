"""Coupons (ADR-058): discount codes the Super Admin issues, applied by the
server to an order's first payment.

- **Staff** (``platform.staff_pricing.manage`` to write, ``.view`` of the
  catalog to read) create a code - percent or fixed amount off, optional
  products, start and end, a use limit, once per customer - and can disable it.
- **The Store's product page** asks ``preview`` what a code would take off the
  amount it shows. A preview, nothing more: it cannot see who is buying.
- **Checkout** passes the typed code with the order request; ``evaluate``
  prices it inside ``payments.orders._quote`` (the browser never sends an
  amount), and ``reserve`` records the use in the order's own transaction with
  the coupon row locked, so two buyers cannot both take its last use.
  ``release`` gives the use back when the order is cancelled, expires, is
  voided or fails.

A coupon lowers the plan's price for the first period only - never a set-up
fee, never a renewal - and never by more than ``MAX_SHARE_BP`` (99%) of it, so
a coupon cannot turn a paid purchase into one that skips payment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from workeasy360.contracts.errors import (
    CatalogValidationError,
    CommercialRecordNotFound,
    OrderNotAllowed,
)
from workeasy360.contracts.values.money import CurrencyCode, Money
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz

VIEW: Final = "platform.staff_catalog.view"
MANAGE: Final = "platform.staff_pricing.manage"

#: The most a coupon may take off the amount it applies to: 99%.
MAX_SHARE_BP: Final = 9900
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,31}$")
_MAX_PRODUCTS = 50


class CouponRejected(OrderNotAllowed):
    """A code that cannot be used for this order. ``coupon_reason`` is one of
    ``unknown``, ``disabled``, ``not_started``, ``expired``, ``used_up``,
    ``already_used``, ``wrong_currency``, ``not_for_these_products``."""


@dataclass(frozen=True)
class AppliedCoupon:
    """What a code takes off one order, per product (minor units)."""

    coupon_id: UUID
    code: str
    label: str
    discount_minor: int
    per_product: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "discount_minor": self.discount_minor,
        }


def normalise_code(raw: Any) -> str:
    code = str(raw or "").strip().upper()
    if not _CODE.match(code):
        raise CatalogValidationError(
            "A coupon code is 3 to 32 letters, digits, '-' or '_'.", field="code"
        )
    return code


def _label(row: Any) -> str:
    if row.kind == row.Kind.PERCENT:
        pct = Decimal(row.percent_bp) / 100
        return f"{pct.normalize():f}% off"
    whole = Money(int(row.amount_minor), CurrencyCode(row.currency))
    return f"{whole.minor_units / 100:g} {row.currency} off"


def _reject(reason: str, message: str) -> CouponRejected:
    return CouponRejected(message, reason="coupon_invalid", coupon_reason=reason)


def _check_usable(row: Any, *, at: datetime) -> None:
    if row.status != row.Status.ACTIVE:
        raise _reject("disabled", f"The coupon {row.code} is no longer available.")
    if row.starts_at and row.starts_at > at:
        raise _reject("not_started", f"The coupon {row.code} is not valid yet.")
    if row.ends_at and row.ends_at <= at:
        raise _reject("expired", f"The coupon {row.code} has expired.")


def _check_uses(row: Any, organization_id: UUID | None) -> None:
    from workeasy360.commercial.pricing.models import CouponRedemption

    used = CouponRedemption.objects.filter(coupon=row)
    if row.max_redemptions is not None and used.count() >= row.max_redemptions:
        raise _reject("used_up", f"The coupon {row.code} has been fully used.")
    if (
        organization_id is not None
        and row.once_per_organization
        and used.filter(organization_id=organization_id).exists()
    ):
        raise _reject(
            "already_used", f"Your organization has already used the coupon {row.code}."
        )


def _discounts(
    row: Any,
    *,
    currency: str,
    amounts: dict[str, int],
    products: dict[str, frozenset[str]] | None = None,
) -> dict[str, int]:
    """Per charge: what the coupon takes off ``amounts`` (each charge's
    first-period price, keyed by product code - or, through ``products``, by
    a key naming several products, like a bundle's). Raises when nothing in
    the order qualifies."""
    if row.kind == row.Kind.FIXED and row.currency != currency:
        raise _reject(
            "wrong_currency",
            f"The coupon {row.code} applies to orders in {row.currency} only.",
        )
    allowed = set(row.product_codes or [])
    eligible = {
        code: amount
        for code, amount in amounts.items()
        if amount > 0
        and (not allowed or allowed & (products or {}).get(code, frozenset({code})))
    }
    if not eligible:
        raise _reject(
            "not_for_these_products",
            f"The coupon {row.code} does not apply to this product.",
        )
    caps = {code: amount * MAX_SHARE_BP // 10_000 for code, amount in eligible.items()}
    if row.kind == row.Kind.PERCENT:
        rate = Decimal(int(row.percent_bp)) / Decimal(10_000)
        return {
            code: min(
                caps[code],
                int(
                    (Decimal(amount) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                ),
            )
            for code, amount in eligible.items()
        }
    off = min(int(row.amount_minor), sum(caps.values()))
    codes = sorted(eligible)
    parts = Money(off, CurrencyCode(currency)).allocate([eligible[c] for c in codes])
    return {
        code: min(caps[code], part.minor_units)
        for code, part in zip(codes, parts, strict=True)
    }


def evaluate(
    raw_code: Any,
    *,
    currency: str,
    amounts: dict[str, int],
    organization_id: UUID | None,
    at: datetime,
    products: dict[str, frozenset[str]] | None = None,
) -> AppliedCoupon:
    """What ``raw_code`` takes off an order whose products cost ``amounts``
    for their first period. Raises ``CouponRejected`` with the reason."""
    from workeasy360.commercial.pricing.models import Coupon

    try:
        code = normalise_code(raw_code)
    except CatalogValidationError as exc:
        raise _reject("unknown", "That coupon code is not valid.") from exc
    row = Coupon.objects.filter(code=code).first()
    if row is None:
        raise _reject("unknown", "That coupon code is not valid.")
    _check_usable(row, at=at)
    _check_uses(row, organization_id)
    per_product = {
        k: v
        for k, v in _discounts(
            row, currency=currency, amounts=amounts, products=products
        ).items()
        if v > 0
    }
    if not per_product:
        raise _reject(
            "not_for_these_products",
            f"The coupon {row.code} does not apply to this product.",
        )
    return AppliedCoupon(
        coupon_id=row.id,
        code=row.code,
        label=_label(row),
        discount_minor=sum(per_product.values()),
        per_product=per_product,
    )


def preview(
    raw_code: Any, *, product_code: Any, currency: Any, amount_minor: Any
) -> dict[str, Any]:
    """The Store's "Apply": what the code would take off ``amount_minor`` of
    ``product_code``. ``{"valid": False, "message"}`` for a code that does not
    apply. The checkout prices the order again, for this customer."""
    try:
        amount = int(amount_minor)
        money = CurrencyCode(str(currency or "").upper())
    except (TypeError, ValueError):
        return {"valid": False, "message": "Choose a plan first."}
    if amount <= 0 or amount > 10**12:
        return {"valid": False, "message": "Choose a plan first."}
    try:
        applied = evaluate(
            raw_code,
            currency=money.value,
            amounts={str(product_code or ""): amount},
            organization_id=None,
            at=timezone.now(),
        )
    except CouponRejected as exc:
        return {
            "valid": False,
            "reason": exc.context.get("coupon_reason"),
            "message": str(exc),
        }
    return {"valid": True, **applied.as_dict()}


def reserve(
    applied: AppliedCoupon, *, organization_id: UUID, order_id: UUID, currency: str
) -> None:
    """Record the order's use of the coupon - inside the order's transaction.
    Re-checks the coupon under its row lock: a use taken since the quote, or a
    coupon disabled since, refuses the order rather than over-redeeming."""
    from workeasy360.commercial.pricing.models import Coupon, CouponRedemption

    row = Coupon.objects.select_for_update().filter(id=applied.coupon_id).first()
    if row is None:
        raise _reject("unknown", "That coupon code is not valid.")
    _check_usable(row, at=timezone.now())
    _check_uses(row, organization_id)
    CouponRedemption.objects.create(
        coupon=row,
        organization_id=organization_id,
        order_id=order_id,
        discount_minor=applied.discount_minor,
        currency=currency,
    )


def release(order_id: UUID) -> None:
    """Give back the use an order held (it will never be paid)."""
    from workeasy360.commercial.pricing.models import CouponRedemption

    CouponRedemption.objects.filter(order_id=order_id).delete()


# --- the Super Admin -----------------------------------------------------------------


_EDITABLE = (
    "description",
    "product_codes",
    "starts_at",
    "ends_at",
    "max_redemptions",
    "once_per_organization",
    "status",
)


def coupon_view(row: Any, *, used: int) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "description": row.description,
        "kind": row.kind,
        "percent_bp": row.percent_bp,
        "amount_minor": row.amount_minor,
        "currency": row.currency or None,
        "label": _label(row),
        "product_codes": list(row.product_codes or []),
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "ends_at": row.ends_at.isoformat() if row.ends_at else None,
        "max_redemptions": row.max_redemptions,
        "once_per_organization": row.once_per_organization,
        "status": row.status,
        "redemptions": used,
        "created_at": row.created_at.isoformat(),
    }


def coupons(ctx: TenantContext) -> dict[str, Any]:
    """Every coupon, newest first, with how many orders hold a use. One query."""
    from workeasy360.commercial.pricing.models import Coupon

    authz.require(ctx, VIEW)
    rows = Coupon.objects.annotate(used=Count("redemptions")).order_by("-created_at")[
        :500
    ]
    return {"results": [coupon_view(r, used=r.used) for r in rows]}


def _moment(raw: Any, field_name: str) -> datetime | None:
    if raw in (None, ""):
        return None
    moment = parse_datetime(str(raw))
    if moment is None:
        raise CatalogValidationError(
            f"{field_name} is not a date and time.", field=field_name
        )
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


def _clean(changes: dict[str, Any]) -> dict[str, Any]:
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.pricing.models import Coupon

    clean: dict[str, Any] = {}
    if "description" in changes:
        clean["description"] = str(changes["description"] or "").strip()[:200]
    if "product_codes" in changes:
        raw = changes["product_codes"] or []
        if (
            not isinstance(raw, list)
            or len(raw) > _MAX_PRODUCTS
            or any(not isinstance(c, str) for c in raw)
        ):
            raise CatalogValidationError(
                "product_codes is a list of product codes.", field="product_codes"
            )
        codes = sorted({c.strip() for c in raw if c.strip()})
        known = set(catalog.product_names(codes))
        unknown = [c for c in codes if c not in known]
        if unknown:
            raise CatalogValidationError(
                f"Unknown product: {', '.join(unknown)}.", field="product_codes"
            )
        clean["product_codes"] = codes
    for key in ("starts_at", "ends_at"):
        if key in changes:
            clean[key] = _moment(changes[key], key)
    if "max_redemptions" in changes:
        raw = changes["max_redemptions"]
        if raw in (None, ""):
            clean["max_redemptions"] = None
        elif (
            isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 1_000_000
        ):
            raise CatalogValidationError(
                "max_redemptions is a whole number from 1 to 1,000,000, or empty.",
                field="max_redemptions",
            )
        else:
            clean["max_redemptions"] = raw
    if "once_per_organization" in changes:
        if not isinstance(changes["once_per_organization"], bool):
            raise CatalogValidationError(
                "once_per_organization must be true or false.",
                field="once_per_organization",
            )
        clean["once_per_organization"] = changes["once_per_organization"]
    if "status" in changes:
        if changes["status"] not in Coupon.Status.values:
            raise CatalogValidationError("status is active or disabled.", field="status")
        clean["status"] = changes["status"]
    return clean


def _check_window(starts: datetime | None, ends: datetime | None) -> None:
    if starts and ends and ends <= starts:
        raise CatalogValidationError("The end must be after the start.", field="ends_at")


def create_coupon(ctx: TenantContext, body: dict[str, Any]) -> dict[str, Any]:
    from workeasy360.commercial.pricing.models import Coupon

    authz.require(ctx, MANAGE)
    code = normalise_code(body.get("code"))
    kind = body.get("kind")
    row = Coupon(code=code, created_by_id=ctx.user_id)
    if kind == Coupon.Kind.PERCENT:
        raw = body.get("percent_bp")
        if (
            isinstance(raw, bool)
            or not isinstance(raw, int)
            or not 1 <= raw <= MAX_SHARE_BP
        ):
            raise CatalogValidationError(
                "A percent coupon takes 0.01% to 99% off (percent_bp 1 to 9900).",
                field="percent_bp",
            )
        row.kind, row.percent_bp = kind, raw
    elif kind == Coupon.Kind.FIXED:
        raw = body.get("amount_minor")
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 10**11:
            raise CatalogValidationError(
                "A fixed coupon takes a positive amount off.", field="amount_minor"
            )
        try:
            currency = CurrencyCode(str(body.get("currency") or "").upper())
        except ValueError as exc:
            raise CatalogValidationError(
                "currency is a 3-letter code.", field="currency"
            ) from exc
        row.kind, row.amount_minor, row.currency = kind, raw, currency.value
    else:
        raise CatalogValidationError("kind is percent or fixed.", field="kind")
    for key, value in _clean({k: body[k] for k in _EDITABLE if k in body}).items():
        setattr(row, key, value)
    _check_window(row.starts_at, row.ends_at)
    with transaction.atomic():
        if Coupon.objects.filter(code=code).exists():
            raise CatalogValidationError(
                f"The code {code} is already in use.", field="code"
            )
        row.save()
        audit.record(
            ctx,
            "commercial.coupon.created",
            resource_type="pricing.coupon",
            resource_id=row.id,
            changes={"code": code, "terms": _label(row)},
        )
    return coupon_view(row, used=0)


def update_coupon(
    ctx: TenantContext, coupon_id: UUID, body: dict[str, Any]
) -> dict[str, Any]:
    """Change what may change: description, products, window, limit, once per
    customer, active/disabled. The code and the discount itself never change -
    a different discount is a new code."""
    from workeasy360.commercial.pricing.models import Coupon

    authz.require(ctx, MANAGE)
    changes = _clean({k: body[k] for k in _EDITABLE if k in body})
    with transaction.atomic():
        row = Coupon.objects.select_for_update().filter(id=coupon_id).first()
        if row is None:
            raise CommercialRecordNotFound("No such coupon.")
        previous = {k: getattr(row, k) for k in changes}
        for key, value in changes.items():
            setattr(row, key, value)
        _check_window(row.starts_at, row.ends_at)
        row.save()
        audit.record(
            ctx,
            "commercial.coupon.changed",
            resource_type="pricing.coupon",
            resource_id=row.id,
            changes={
                "code": row.code,
                "previous": {k: _plain(v) for k, v in previous.items()},
                "new": {k: _plain(v) for k, v in changes.items()},
            },
        )
        used = row.redemptions.count()
    return coupon_view(row, used=used)


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value
