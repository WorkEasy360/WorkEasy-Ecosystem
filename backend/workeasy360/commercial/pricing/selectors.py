"""Pricing read queries for listings (Phase 8 extension).

``resolution.resolve_price`` answers "what does *this* customer pay for *this*
line" - exactly, one target at a time, including contract and partner prices.
A plan builder or a marketplace page needs the other question: "what are the
published list prices of these versions" - for many targets at once. Resolving
each one would be a few queries per target per interval; these are one query
each, whatever the number of targets.

Listing prices are **informational**. What a customer is charged is always
resolved again, exactly, when they buy.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from workeasy360.contracts.enums import PriceBookChannel


def _live(queryset: Any, at: Any) -> Any:
    return queryset.filter(valid_from__lte=at).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gt=at)
    )


def price_books() -> list[dict[str, Any]]:
    from workeasy360.commercial.pricing.models import PriceBook

    return [
        {
            "id": str(b.id),
            "code": b.code,
            "name": b.name,
            "channel": b.channel,
            "partner_id": str(b.partner_id) if b.partner_id else None,
            "region": b.region,
            "currency": b.currency,
            "priority": b.priority,
            "is_active": b.is_active,
            "valid_from": b.valid_from.isoformat(),
            "valid_to": b.valid_to.isoformat() if b.valid_to else None,
        }
        for b in PriceBook.objects.order_by("channel", "currency", "priority", "code")[
            :500
        ]
    ]


def current_prices(
    *,
    plan_version_ids: list[UUID] | None = None,
    bundle_version_ids: list[UUID] | None = None,
    at: Any = None,
) -> list[dict[str, Any]]:
    """Every live, published price of these targets, in every book."""
    from workeasy360.commercial.pricing.models import PlanPrice

    at = at or timezone.now()
    targets = Q(pk__in=[])
    if plan_version_ids:
        targets |= Q(plan_version_id__in=plan_version_ids)
    if bundle_version_ids:
        targets |= Q(bundle_version_id__in=bundle_version_ids)
    rows = (
        _live(PlanPrice.objects.filter(targets, published_at__isnull=False), at)
        .select_related("price_book")
        .order_by("price_book__priority", "interval")
    )
    return [
        {
            "price_id": str(p.id),
            "plan_version_id": str(p.plan_version_id) if p.plan_version_id else None,
            "bundle_version_id": str(p.bundle_version_id)
            if p.bundle_version_id
            else None,
            "price_book_id": str(p.price_book_id),
            "price_book_code": p.price_book.code,
            "channel": p.price_book.channel,
            "partner_id": str(p.price_book.partner_id)
            if p.price_book.partner_id
            else None,
            "currency": p.price_book.currency,
            "interval": p.interval,
            "model": p.model,
            "amount_minor": p.amount_minor,
            "valid_from": p.valid_from.isoformat(),
        }
        for p in rows
    ]


def direct_list_prices(
    *,
    currency: str,
    region: str = "",
    plan_version_ids: list[UUID] | None = None,
    bundle_version_ids: list[UUID] | None = None,
    at: Any = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """``{(target_id, interval): price}`` from the *direct* books a customer in
    ``currency``/``region`` would buy from - the regional book first, then the
    default one, lowest priority number winning, exactly the order
    ``resolve_price`` uses for a direct purchase. Flat prices only carry an
    amount; tiered ones are flagged ``from_tiers``."""
    from workeasy360.commercial.pricing.models import PlanPrice, PriceBook

    at = at or timezone.now()
    regions = [r for r in (region, "") if r is not None]
    books = _live(
        PriceBook.objects.filter(
            channel=PriceBookChannel.DIRECT.value,
            partner_id__isnull=True,
            currency=currency,
            region__in=list(dict.fromkeys(regions)),
            is_active=True,
        ),
        at,
    )
    targets = Q(pk__in=[])
    if plan_version_ids:
        targets |= Q(plan_version_id__in=plan_version_ids)
    if bundle_version_ids:
        targets |= Q(bundle_version_id__in=bundle_version_ids)
    rows = (
        _live(
            PlanPrice.objects.filter(
                targets, price_book__in=books, published_at__isnull=False
            ),
            at,
        )
        .select_related("price_book")
        .order_by("price_book__priority", "-valid_from")
    )
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for p in rows:
        target = str(p.plan_version_id or p.bundle_version_id)
        # The regional book beats the default one; within a region, priority.
        rank = (
            0 if p.price_book.region == region and region else 1,
            p.price_book.priority,
        )
        key = (target, p.interval)
        current = chosen.get(key)
        if current is not None and current["_rank"] <= rank:
            continue
        chosen[key] = {
            "_rank": rank,
            "interval": p.interval,
            "currency": p.price_book.currency,
            "model": p.model,
            "amount_minor": p.amount_minor,
            "from_tiers": p.amount_minor is None,
        }
    for value in chosen.values():
        value.pop("_rank", None)
    return chosen


def direct_currencies(*, at: Any = None) -> list[str]:
    """The currencies a visitor can see list prices in (Phase 9): those with a
    live, active, direct price book for every region. One query."""
    from workeasy360.commercial.pricing.models import PriceBook

    at = at or timezone.now()
    rows = _live(
        PriceBook.objects.filter(
            channel=PriceBookChannel.DIRECT.value,
            partner_id__isnull=True,
            region="",
            is_active=True,
        ),
        at,
    )
    return sorted(set(rows.values_list("currency", flat=True)))


# --- what other modules ask about a partner's own prices (Phase 10) -----------
#
# Pricing owns ``PriceBook`` and ``PlanPrice``; nothing outside this module
# imports them. The partner console and the partner price command need to *ask*
# - whether a partner has a price, in which cycles, which book holds it - and
# these answer without handing out a model (F-6). None of them computes a
# price: that is ``resolution.resolve_price``, the one function that does.


def _partner_prices() -> Any:
    from workeasy360.commercial.pricing.models import PlanPrice

    return PlanPrice.objects.filter(
        price_book__channel=PriceBookChannel.PARTNER.value,
        price_book__is_active=True,
        published_at__isnull=False,
        valid_to__isnull=True,
    )


def has_partner_price(partner_id: UUID) -> bool:
    """Whether this partner has at least one published, current price of its own."""
    return _partner_prices().filter(price_book__partner_id=partner_id).exists()


def partner_price_cycles(partner_id: UUID) -> dict[UUID, set[str]]:
    """The billing cycles this partner has its own current price for, by plan
    version - one query."""
    cycles: dict[UUID, set[str]] = {}
    for plan_version_id, interval in (
        _partner_prices()
        .filter(price_book__partner_id=partner_id)
        .values_list("plan_version_id", "interval")
    ):
        cycles.setdefault(plan_version_id, set()).add(interval)
    return cycles


def partner_book_locked(partner_id: UUID, currency: str) -> Any | None:
    """The partner's active default price book in ``currency``, **row-locked**
    (call inside the transaction that will publish into it), or ``None``."""
    from workeasy360.commercial.pricing.models import PriceBook

    return (
        PriceBook.objects.select_for_update()
        .filter(
            channel=PriceBookChannel.PARTNER.value,
            partner_id=partner_id,
            currency=currency,
            region="",
            is_active=True,
        )
        .order_by("priority", "code")
        .first()
    )


def current_price_in_book(
    book_id: UUID, plan_version_id: UUID, interval: str
) -> Any | None:
    """The published, unclosed price of one plan version and cycle in one book."""
    from workeasy360.commercial.pricing.models import PlanPrice

    return PlanPrice.objects.filter(
        price_book_id=book_id,
        plan_version_id=plan_version_id,
        interval=interval,
        published_at__isnull=False,
        valid_to__isnull=True,
    ).first()
