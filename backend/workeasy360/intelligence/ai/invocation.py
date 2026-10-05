"""Calling models: retries, the breaker, fallback, and metering of every call.

The two primitives the rest of the platform uses (``invoke_model`` is the
brief's name for the first):

- ``generate`` - one generation over an ordered candidate list
  (``registry.Selection``);
- ``embed`` - one batch of embeddings with the platform's embedding model.

Neither touches the database except through the breaker, and neither holds a
transaction: they run between a reservation and its settlement (E2).

**Retries (Task 50).** Only transient failures - 429, 5xx/529, and connection
failures where nothing reached the vendor - are retried, at most
``AI_MAX_ATTEMPTS`` times per model with equal jitter (a ``Retry-After`` is
honoured but clamped), and never past the operation's single deadline. An
invalid request, an authentication failure, a context-length error and a
read timeout are not retried: the first three cannot succeed on retry, and a
read timeout may already have been processed and billed.

**Fallback (Task 52)** is the next candidate - only candidates the selection
permitted, i.e. the same vendor, or another vendor the organization allowed.
It is recorded (``fallback_used``) and counted (``ai_fallbacks_total``), never
silent. A request error (``bad_request``, ``context_too_large``) ends the
attempt outright: another model will not fix the request.

**Every call is a usage line**, successful or not, costed at the price version
pinned to the model at selection time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from workeasy360.contracts.values.backoff import equal_jitter
from workeasy360.intelligence.ai import credentials, registry, transport
from workeasy360.intelligence.ai.budget import UsageLine
from workeasy360.intelligence.ai.domain import Capability
from workeasy360.intelligence.ai.providers import adapter_for
from workeasy360.intelligence.ai.providers.contract import (
    EmbeddingRequest,
    GenerationRequest,
    GenerationResult,
    ProviderFailure,
)
from workeasy360.intelligence.ai.schema import vendor_schema
from workeasy360.intelligence.ai.tokens import cost_micros
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Failures that describe the request, not the model: no fallback fixes them.
_REQUEST_FAILURES = frozenset({"bad_request", "context_too_large"})


@dataclass
class GenerationOutcome:
    result: GenerationResult | None
    model: registry.ModelDTO | None
    lines: list[UsageLine] = field(default_factory=list)
    fallback_used: bool = False
    #: The last failure category when ``result`` is ``None``.
    failure: str = ""


@dataclass
class EmbeddingOutcome:
    vectors: tuple[tuple[float, ...], ...] | None
    lines: list[UsageLine] = field(default_factory=list)
    failure: str = ""


def generate(
    selection: registry.Selection,
    *,
    system: str,
    user: str,
    max_output_tokens: int,
    output_schema: dict[str, Any] | None,
    deadline: transport.Deadline,
    request_id: Any,
) -> GenerationOutcome:
    outcome = GenerationOutcome(result=None, model=None, failure="unavailable")
    for index, model in enumerate(selection.candidates):
        if deadline.expired:
            outcome.failure = "timeout"
            break
        if index:
            metrics.increment("ai_fallbacks_total", provider=model.provider_key)
            logger.warning(
                "ai.fallback",
                request_id=str(request_id),
                to_model=model.key,
                reason=outcome.failure,
            )
        adapter = adapter_for(model.adapter)
        if adapter is None or "generation" not in adapter.operations:
            continue
        schema = (
            vendor_schema(output_schema)
            if output_schema is not None
            and Capability.STRUCTURED_OUTPUT.value in model.capabilities
            else None
        )
        request = GenerationRequest(
            model_id=model.model_id,
            system=system,
            user=user,
            max_output_tokens=min(max_output_tokens, model.max_output_tokens),
            output_schema=schema,
        )

        def call(
            api_key: str, adapter: Any = adapter, request: GenerationRequest = request
        ):
            response = transport.send(
                adapter,
                adapter.generation_call(request, api_key),
                deadline=deadline,
                max_response_bytes=int(settings.AI_MAX_RESPONSE_BYTES),
            )
            return (
                adapter.parse_generation(
                    response.status, response.headers, response.body
                ),
                response.duration_ms,
            )

        result, failure = _with_retries(
            model, "generation", call, outcome.lines, deadline, request_id
        )
        if result is not None:
            outcome.result = result
            outcome.model = model
            outcome.fallback_used = index > 0
            outcome.failure = ""
            return outcome
        outcome.failure = failure
        if failure in _REQUEST_FAILURES:
            break
    return outcome


def embed(
    model: registry.ModelDTO,
    texts: list[str],
    *,
    deadline: transport.Deadline,
    request_id: Any,
) -> EmbeddingOutcome:
    """Embed ``texts`` in batches of ``RAG_EMBED_BATCH``. All-or-nothing: an
    index built from some vectors of one model and none of another is not an
    index."""
    outcome = EmbeddingOutcome(vectors=None)
    adapter = adapter_for(model.adapter)
    if adapter is None or "embedding" not in adapter.operations:
        outcome.failure = "unavailable"
        return outcome
    batch = max(1, int(settings.RAG_EMBED_BATCH))
    vectors: list[tuple[float, ...]] = []
    for start in range(0, len(texts), batch):
        chunk = tuple(texts[start : start + batch])
        request = EmbeddingRequest(model_id=model.model_id, texts=chunk)

        def call(
            api_key: str, request: EmbeddingRequest = request, expected: int = len(chunk)
        ):
            response = transport.send(
                adapter,
                adapter.embedding_call(request, api_key),
                deadline=deadline,
                max_response_bytes=int(settings.AI_EMBEDDING_MAX_RESPONSE_BYTES),
            )
            return (
                adapter.parse_embedding(
                    response.status, response.headers, response.body, expected
                ),
                response.duration_ms,
            )

        result, failure = _with_retries(
            model, "embedding", call, outcome.lines, deadline, request_id
        )
        if result is None:
            outcome.failure = failure
            return outcome
        if (
            model.embedding_dimensions is not None
            and len(result.vectors[0]) != model.embedding_dimensions
        ):
            # The registry says what the model produces; a different shape is
            # a misconfiguration the index must not absorb.
            outcome.failure = "invalid_response"
            return outcome
        vectors.extend(result.vectors)
    outcome.vectors = tuple(vectors)
    return outcome


def _with_retries(
    model: registry.ModelDTO,
    kind: str,
    call: Any,
    lines: list[UsageLine],
    deadline: transport.Deadline,
    request_id: Any,
) -> tuple[Any, str]:
    """Run ``call`` against one model with bounded retries. Appends a usage
    line per attempt; returns (result, "") or (None, failure category)."""
    price = model.price
    try:
        api_key = credentials.api_key(model.credential_ref)
    except credentials.CredentialUnavailable:
        lines.append(_line(kind, model, len(lines) + 1, "unavailable"))
        metrics.increment(
            "ai_provider_failures_total",
            provider=model.provider_key,
            category="unavailable",
        )
        return None, "unavailable"

    max_attempts = max(1, int(settings.AI_MAX_ATTEMPTS))
    failure = "unavailable"
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            result, duration_ms = call(api_key)
        except ProviderFailure as exc:
            failure = exc.category
            elapsed = int((time.monotonic() - started) * 1000)
            lines.append(_line(kind, model, len(lines) + 1, failure, latency_ms=elapsed))
            metrics.increment(
                "ai_provider_failures_total",
                provider=model.provider_key,
                category=failure,
            )
            logger.warning(
                "ai.provider.failed",
                request_id=str(request_id),
                model=model.key,
                kind=kind,
                attempt=attempt,
                category=failure,
            )
            if not exc.retryable or attempt == max_attempts:
                break
            delay = equal_jitter(
                attempt - 1,
                base=float(settings.AI_RETRY_BASE_S),
                cap=float(settings.AI_RETRY_CAP_S),
            )
            if exc.retry_after is not None:
                delay = min(max(delay, exc.retry_after), float(settings.AI_RETRY_CAP_S))
            if delay >= deadline.remaining() - 0.1:
                break
            time.sleep(delay)
            continue

        input_tokens = result.input_tokens
        output_tokens = getattr(result, "output_tokens", 0)
        cost = (
            cost_micros(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_micros_per_mtok=price.input_micros_per_mtok,
                output_micros_per_mtok=price.output_micros_per_mtok,
            )
            if price is not None
            else 0
        )
        lines.append(
            _line(
                kind,
                model,
                len(lines) + 1,
                "ok",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
                latency_ms=duration_ms,
            )
        )
        metrics.observe(
            "ai_provider_latency_seconds",
            duration_ms / 1000.0,
            provider=model.provider_key,
            kind=kind,
        )
        registry.record_success(model.key)
        return result, ""

    if ProviderFailure(failure).trips_breaker:
        registry.record_failure(model.key)
    return None, failure


def _line(
    kind: str,
    model: registry.ModelDTO,
    attempt: int,
    outcome: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: int = 0,
    latency_ms: int = 0,
) -> UsageLine:
    return UsageLine(
        kind=kind,
        model_key=model.key,
        provider_key=model.provider_key,
        price_id=model.price.id if model.price is not None else None,
        attempt=attempt,
        outcome=outcome,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micros=cost,
        latency_ms=latency_ms,
    )
