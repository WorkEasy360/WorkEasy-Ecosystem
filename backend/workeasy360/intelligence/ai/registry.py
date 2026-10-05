"""The model registry as the gateway reads it: DTOs, selection, and the breaker.

**Selection (Task 5).** A skill declares what it needs - capabilities, a tier,
optionally a pinned model - and never a vendor. The platform picks among
*active* models of *active* providers that have a current price, have those
capabilities, are permitted by the organization's provider policy and tier
allowance, and whose circuit is closed; lowest ``priority`` first. The result
is an ordered candidate list: the primary, then the fallbacks the policy
permits (Task 52):

- another model of the **same vendor** with the same capabilities - the data
  goes where the organization already agreed it may;
- another vendor **only** when the organization has allowed provider
  fallback and that vendor is on its allowed list.

A pinned model is exactly that model: no fallback, because a pin is a
contract that a specific model answers (Task 5).

**The breaker (Task 51)** is per model and lives on the model row. Phase 6's
per-connection breaker is the precedent, with one change for the hot path: a
success writes only when the model had failures to clear, so a healthy model
costs no write at all. After ``AI_BREAKER_THRESHOLD`` consecutive transient
failures the model is skipped for ``AI_BREAKER_BASE_S``, doubling to
``AI_BREAKER_CAP_S``; when that lapses the next request probes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.intelligence.ai.domain import Capability, ModelTier, RegistryStatus
from workeasy360.intelligence.ai.models import AIModel, AIModelPrice, AIProvider
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PriceDTO:
    id: Any
    version: int
    currency: str
    input_micros_per_mtok: int
    output_micros_per_mtok: int


@dataclass(frozen=True, slots=True)
class ModelDTO:
    key: str
    model_id: str
    provider_key: str
    adapter: str
    credential_ref: str
    provider_active: bool
    status: str
    tier: str
    capabilities: frozenset[str]
    context_window_tokens: int
    max_output_tokens: int
    embedding_dimensions: int | None
    priority: int
    circuit_open_until: datetime | None
    price: PriceDTO | None

    def circuit_open(self, now: datetime) -> bool:
        return self.circuit_open_until is not None and self.circuit_open_until > now


@dataclass(frozen=True, slots=True)
class Selection:
    """What a request may use: ordered candidates, primary first."""

    candidates: tuple[ModelDTO, ...]

    @property
    def primary(self) -> ModelDTO:
        return self.candidates[0]


def all_models() -> list[ModelDTO]:
    """Every registered model with its provider and current price - two
    queries whatever the registry's size."""
    now = timezone.now()
    rows = list(AIModel.objects.select_related("provider").order_by("priority", "key"))
    prices: dict[Any, PriceDTO] = {}
    for price in (
        AIModelPrice.objects.filter(
            model_id__in=[r.id for r in rows], effective_from__lte=now
        )
        .order_by("model_id", "-version")
        .distinct("model_id")
    ):
        prices[price.model_id] = PriceDTO(
            id=price.id,
            version=price.version,
            currency=price.currency,
            input_micros_per_mtok=price.input_micros_per_mtok,
            output_micros_per_mtok=price.output_micros_per_mtok,
        )
    return [_dto(row, prices.get(row.id)) for row in rows]


def model(key: str) -> ModelDTO | None:
    return next((m for m in all_models() if m.key == key), None)


def providers() -> list[dict[str, Any]]:
    """Every provider, for policy screens: identity and data-handling notes,
    never the credential reference's value (there is none to show)."""
    return [
        {
            "key": p.key,
            "display_name": p.display_name,
            "status": p.status,
            "data_region": p.data_region,
            "retains_inputs_days": p.retains_inputs_days,
            "trains_on_inputs": p.trains_on_inputs,
            "data_policy_note": p.data_policy_note,
        }
        for p in AIProvider.objects.order_by("key")
    ]


def select_generation(
    *,
    capabilities: frozenset[str],
    tier: str,
    pinned_model_key: str,
    allowed_providers: frozenset[str],
    premium_allowed: bool,
    allow_provider_fallback: bool,
) -> Selection | None:
    """Ordered candidates for a generation, or ``None`` when no approved
    model may serve it."""
    now = timezone.now()
    required = capabilities | {Capability.TEXT.value}
    eligible = [
        m
        for m in all_models()
        if _usable(m, required, allowed_providers, premium_allowed, pinned_model_key)
    ]
    if pinned_model_key:
        pinned = [
            m for m in eligible if m.key == pinned_model_key and not m.circuit_open(now)
        ]
        return Selection(tuple(pinned)) if pinned else None

    eligible = [
        m
        for m in eligible
        if m.status == RegistryStatus.ACTIVE.value and not m.circuit_open(now)
    ]
    if tier == ModelTier.PREMIUM.value and premium_allowed:
        # A premium skill prefers premium models, and may still be served by
        # a standard one rather than not at all.
        eligible.sort(
            key=lambda m: (m.tier != ModelTier.PREMIUM.value, m.priority, m.key)
        )
    else:
        eligible = [m for m in eligible if m.tier == ModelTier.STANDARD.value]
    if not eligible:
        return None
    primary = eligible[0]
    fallbacks = [
        m
        for m in eligible[1:]
        if m.provider_key == primary.provider_key or allow_provider_fallback
    ]
    return Selection((primary, *fallbacks))


def select_embedding(*, allowed_providers: frozenset[str]) -> ModelDTO | None:
    """The platform's embedding model - one at a time, because a vector index
    is only searchable with the model that built it. ``None`` when the
    organization's provider policy excludes it or its circuit is open."""
    now = timezone.now()
    for m in all_models():
        if m.status != RegistryStatus.ACTIVE.value or not m.provider_active:
            continue
        if Capability.EMBEDDINGS.value not in m.capabilities or m.price is None:
            continue
        if allowed_providers and m.provider_key not in allowed_providers:
            return None
        return None if m.circuit_open(now) else m
    return None


def current_embedding_model_key() -> str:
    """The embedding model new indexes are built with, whatever any one
    organization's policy is - staleness is measured against it."""
    for m in all_models():
        if (
            m.status == RegistryStatus.ACTIVE.value
            and m.provider_active
            and Capability.EMBEDDINGS.value in m.capabilities
            and m.price is not None
        ):
            return m.key
    return ""


# --- the breaker --------------------------------------------------------------------


def record_success(model_key: str) -> None:
    """Close the circuit - a write only when there is something to clear."""
    cleared = AIModel.objects.filter(key=model_key, consecutive_failures__gt=0).update(
        consecutive_failures=0, circuit_open_until=None, last_success_at=timezone.now()
    )
    if cleared:
        logger.info("ai.breaker.closed", model=model_key)


def record_failure(model_key: str) -> None:
    now = timezone.now()
    threshold = int(settings.AI_BREAKER_THRESHOLD)
    with transaction.atomic():
        row = AIModel.objects.select_for_update().filter(key=model_key).first()
        if row is None:
            return
        row.consecutive_failures += 1
        row.last_failure_at = now
        fields = ["consecutive_failures", "last_failure_at", "updated_at"]
        if row.consecutive_failures >= threshold:
            exponent = min(row.consecutive_failures - threshold, 16)
            seconds = min(
                float(settings.AI_BREAKER_BASE_S) * (2**exponent),
                float(settings.AI_BREAKER_CAP_S),
            )
            row.circuit_open_until = now + timezone.timedelta(seconds=seconds)
            fields.append("circuit_open_until")
            metrics.increment("ai_breaker_opened_total", provider=row.provider.key)
            logger.warning(
                "ai.breaker.opened",
                model=model_key,
                failures=row.consecutive_failures,
                open_seconds=seconds,
            )
        row.save(update_fields=fields)


# --- internals -----------------------------------------------------------------------


def _usable(
    m: ModelDTO,
    required: frozenset[str],
    allowed_providers: frozenset[str],
    premium_allowed: bool,
    pinned_model_key: str,
) -> bool:
    if not m.provider_active or m.price is None:
        return False
    if m.status == RegistryStatus.DISABLED.value:
        return False
    if m.status == RegistryStatus.DEPRECATED.value and m.key != pinned_model_key:
        return False
    if not required <= m.capabilities:
        return False
    if allowed_providers and m.provider_key not in allowed_providers:
        return False
    return m.tier == ModelTier.STANDARD.value or premium_allowed


def _dto(row: AIModel, price: PriceDTO | None) -> ModelDTO:
    return ModelDTO(
        key=row.key,
        model_id=row.model_id,
        provider_key=row.provider.key,
        adapter=row.provider.adapter,
        credential_ref=row.provider.credential_ref,
        provider_active=row.provider.status == RegistryStatus.ACTIVE.value,
        status=row.status,
        tier=row.tier,
        capabilities=frozenset(str(c) for c in (row.capabilities or [])),
        context_window_tokens=row.context_window_tokens,
        max_output_tokens=row.max_output_tokens,
        embedding_dimensions=row.embedding_dimensions,
        priority=row.priority,
        circuit_open_until=row.circuit_open_until,
        price=price,
    )
