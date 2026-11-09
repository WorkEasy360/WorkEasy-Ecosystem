"""Charging for what products report: AI tokens, messages, API calls, webhooks.

One door, ``record``: an event arrives once (a repeat of its key charges nothing and is
counted for review), the live rate decides what it costs, the free monthly allowance
comes off first, and the rest is taken from the wallet that meter belongs to. Every
outcome is kept: charged, within the allowance, not rated, or failed.

**No rate means no charge.** Every rate is what an Admin entered; until then an event is
stored as ``not_rated`` and costs nothing. Nothing here assumes a price.

**Refused, not overdrawn.** With too little in the wallet the charge fails and the event
stays ``failed: insufficient_balance``. The brief allows no overdraft, so none exists.
Whether the product then stops is the product's decision, from the answer it receives.
"""

from __future__ import annotations

import math
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from workeasy360.commercial.wallets import ledger
from workeasy360.commercial.wallets.models import (
    UsageEvent,
    UsageRate,
    Wallet,
    WalletEntry,
)
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.platform.observability.metrics import metrics

OPERATIONS = "wallet_usage_events_total"

#: Which balance pays for which meter. AI tokens spend credits; everything else
#: spends the Consumption Wallet. The three spendable balances are exactly these.
WALLET_FOR_METER = {
    UsageRate.Meter.AI_TOKENS.value: Wallet.Kind.AI_CREDITS.value,
    UsageRate.Meter.WHATSAPP.value: Wallet.Kind.CONSUMPTION.value,
    UsageRate.Meter.SMS.value: Wallet.Kind.CONSUMPTION.value,
    UsageRate.Meter.EMAIL.value: Wallet.Kind.CONSUMPTION.value,
    UsageRate.Meter.API_TRIGGER.value: Wallet.Kind.CONSUMPTION.value,
    UsageRate.Meter.WEBHOOK.value: Wallet.Kind.CONSUMPTION.value,
}


def _clean_key(raw: Any) -> str:
    key = str(raw or "").strip()
    if not key or len(key) > 128:
        raise CatalogValidationError("Send an idempotency key.", field="idempotency_key")
    return key


def _clean_quantity(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise CatalogValidationError(
            "Quantity must be a whole number above zero.", field="quantity"
        )
    return raw


def _month_start(moment: datetime) -> datetime:
    return datetime(moment.year, moment.month, 1, tzinfo=dt_timezone.utc)


def live_rate(meter: str, currency: str) -> UsageRate | None:
    rows = UsageRate.objects.filter(meter=meter, effective_to__isnull=True)
    if meter == UsageRate.Meter.AI_TOKENS.value:
        return rows.first()
    return rows.filter(currency=currency).first()


def cost_of(units: int, rate: UsageRate) -> int:
    """Rounded up: part of a billing unit costs the whole unit, always in whole
    minor units or credits."""
    return math.ceil(units * rate.unit_amount / rate.per_units)


def _units_used_this_month(organization_id: UUID, meter: str, moment: datetime) -> int:
    total = UsageEvent.objects.filter(
        organization_id=organization_id,
        meter=meter,
        occurred_at__gte=_month_start(moment),
        outcome__in=(UsageEvent.Outcome.CHARGED, UsageEvent.Outcome.INCLUDED),
    ).aggregate(total=Sum("quantity"))["total"]
    return int(total or 0)


def _ledger_key(event: Any) -> str:
    return f"usage:{event.organization_id}:{event.product_code}:{event.idempotency_key}"[:128]


def record(
    *,
    organization_id: UUID,
    product_code: str,
    meter: Any,
    quantity: Any,
    idempotency_key: Any,
    currency: str,
    occurred_at: datetime | None = None,
) -> UsageEvent:
    """Take one event, once. Returns the stored event, whichever way it went."""
    if meter not in WALLET_FOR_METER:
        raise CatalogValidationError("Unknown meter.", field="meter")
    key = _clean_key(idempotency_key)
    units = _clean_quantity(quantity)
    moment = occurred_at or timezone.now()
    with transaction.atomic():
        wallet = ledger.ensure(
            owner_type=Wallet.OwnerType.ORGANIZATION,
            owner_id=organization_id,
            kind=WALLET_FOR_METER[meter],
            currency=currency,
        )
        # One charge at a time per wallet, so the allowance and the balance agree.
        Wallet.objects.select_for_update().filter(id=wallet.id).first()
        existing = UsageEvent.objects.filter(
            organization_id=organization_id, product_code=product_code, idempotency_key=key
        ).first()
        if existing is not None:
            return _note_duplicate(existing)
        event = _decide(wallet, organization_id, product_code, meter, units, key, moment, currency)
    metrics.increment(OPERATIONS, meter=str(meter), outcome=event.outcome)
    return event


def _note_duplicate(event: UsageEvent) -> UsageEvent:
    event.duplicate_count += 1
    event.last_duplicate_at = timezone.now()
    event.save(update_fields=["duplicate_count", "last_duplicate_at"])
    metrics.increment(OPERATIONS, meter=str(event.meter), outcome="duplicate")
    return event


def _decide(
    wallet: Wallet,
    organization_id: UUID,
    product_code: str,
    meter: str,
    units: int,
    key: str,
    moment: datetime,
    currency: str,
) -> UsageEvent:
    event = UsageEvent(
        organization_id=organization_id,
        product_code=product_code,
        meter=meter,
        quantity=units,
        idempotency_key=key,
        occurred_at=moment,
    )
    rate = live_rate(meter, currency)
    event.rate = rate
    if rate is None:
        event.outcome = UsageEvent.Outcome.NOT_RATED
        event.failure_code = "no_rate"
    else:
        used = _units_used_this_month(organization_id, meter, moment)
        free_left = max(0, rate.included_units - used)
        chargeable = max(0, units - free_left)
        event.charged_units = chargeable
        if chargeable == 0:
            event.outcome = UsageEvent.Outcome.INCLUDED
        else:
            event.amount = cost_of(chargeable, rate)
            try:
                event.entry = ledger.post(
                    wallet.id,
                    amount=-event.amount,
                    entry_type=WalletEntry.Type.USAGE,
                    reason=f"{UsageRate.Meter(meter).label}: {chargeable} units",
                    idempotency_key=_ledger_key(event),
                    source_type="usage_event",
                    source_id=key[:64],
                )
                event.outcome = UsageEvent.Outcome.CHARGED
            except ledger.InsufficientBalance:
                event.outcome = UsageEvent.Outcome.FAILED
                event.failure_code = "insufficient_balance"
    try:
        with transaction.atomic():
            event.save()
    except IntegrityError:
        # The same key arrived for another currency's wallet at the same moment.
        # The first one's answer stands.
        return UsageEvent.objects.get(
            organization_id=organization_id, product_code=product_code, idempotency_key=key
        )
    return event


def retry(event_id: UUID) -> UsageEvent:
    """Charge a failed event again, keeping its key. A failed charge moved no money,
    and a charged one replays: this can never charge twice."""
    event = UsageEvent.objects.filter(id=event_id).first()
    if event is None or event.outcome != UsageEvent.Outcome.FAILED or event.rate is None:
        raise CatalogValidationError("Only a failed event can be retried.", field="event")
    with transaction.atomic():
        wallet = ledger.ensure(
            owner_type=Wallet.OwnerType.ORGANIZATION,
            owner_id=event.organization_id,
            kind=WALLET_FOR_METER[event.meter],
            currency=event.rate.currency,
        )
        Wallet.objects.select_for_update().filter(id=wallet.id).first()
        locked = UsageEvent.objects.select_for_update().get(id=event.id)
        if locked.outcome != UsageEvent.Outcome.FAILED:
            return locked
        locked.entry = ledger.post(
            wallet.id,
            amount=-locked.amount,
            entry_type=WalletEntry.Type.USAGE,
            reason=f"{UsageRate.Meter(locked.meter).label}: {locked.charged_units} units (retried)",
            idempotency_key=_ledger_key(locked),
            source_type="usage_event",
            source_id=locked.idempotency_key[:64],
        )
        locked.outcome = UsageEvent.Outcome.CHARGED
        locked.failure_code = ""
        locked.save(update_fields=["entry", "outcome", "failure_code"])
    return locked


def credits_block_ai(organization_id: UUID) -> bool:
    """Whether this organization should be refused an AI call now: a rate is set and
    it holds no credits. With no rate AI is not being charged, so nothing is refused."""
    if live_rate(UsageRate.Meter.AI_TOKENS.value, "") is None:
        return False
    wallet = Wallet.objects.filter(
        owner_type=Wallet.OwnerType.ORGANIZATION,
        owner_id=organization_id,
        kind=Wallet.Kind.AI_CREDITS,
    ).first()
    return wallet is None or wallet.balance <= 0
