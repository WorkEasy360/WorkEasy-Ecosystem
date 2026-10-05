"""The AI gateway: how a product invokes a skill (Tasks 2, 5, 7, 10, 18, 19, 48).

::

    invoke(client, organization, request)
      rate limits: product, product+organization, product+organization+skill
      skill      resolved for *this caller* - own, shared with it, or platform
      input      validated against the skill's input schema
      authorize  organization active + product entitled for writes + AI in its
                 plan + AI not switched off (policy.authorize)
      select     approved models with the skill's capabilities, within the
                 organization's provider and tier policy (registry)
      fit        trusted instructions + input must fit the model, or 422
      reserve    worst-case cost of every permitted candidate, under the
                 month's budget row locks (budget.reserve) - or 429
      [async]    202; the job runs on the heavy queue from here
      retrieve   the caller's own authorized passages, if the skill retrieves
      assemble   preamble -> instructions -> <context> -> <input>
      generate   primary model, bounded retries, permitted fallback
      validate   the skill's output schema, bounded re-asks, or 502
      settle     actual usage at pinned prices; result kept briefly

Everything is decided **before** anything is retrieved or sent (Task 15): a
refusal at any step costs nothing and leaks nothing. Model output is returned
to the product that asked and never read back as state by the platform - it
cannot grant, change or decide anything (Task 20).

The request id - the execution's id - is returned on every answer and
carried by every log line, usage line and provider call of the request
(Task 48). A retried request under the same ``idempotency_key`` gets the same
execution back: its stored result if it succeeded, its state if it is still
running - never a second charge (acceptance step 10).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from django.conf import settings

from workeasy360.contracts.errors import (
    AIContextTooLarge,
    AIInvalidOutput,
    AIInvalidRequest,
    AIProviderUnavailable,
    AIRequestConflict,
    AIRequestNotFound,
    IndexNotReady,
    IntelligenceError,
    KnowledgeNotFound,
)
from workeasy360.intelligence.ai import budget, invocation, policy, registry, skills
from workeasy360.intelligence.ai.domain import ExecutionStatus, Operation
from workeasy360.intelligence.ai.safety import redact_value
from workeasy360.intelligence.ai.schema import validate
from workeasy360.intelligence.ai.tokens import cost_micros, estimate_tokens
from workeasy360.intelligence.ai.transport import Deadline
from workeasy360.intelligence.gateway import context
from workeasy360.intelligence.rag import retrieval
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

_FIELDS: Final[frozenset[str]] = frozenset(
    {"skill", "version", "input", "retrieval", "idempotency_key", "mode", "actor"}
)
MAX_REQUEST_BYTES_OVERHEAD: Final = 8_192


@dataclass(frozen=True, slots=True)
class InvokeRequest:
    skill: str
    version: int | None
    input: Any
    retrieval: Any
    idempotency_key: str
    mode: str
    actor: str


@dataclass(frozen=True, slots=True)
class Answer:
    """What the HTTP adapter renders: a status code and a body."""

    status: int
    body: dict[str, Any]


# --- invoke -------------------------------------------------------------------------------


def invoke(client: Any, *, organization_id: Any, raw: Any) -> Answer:
    from workeasy360.platform.ecosystem import ratelimit
    from workeasy360.platform.observability.correlation import current_correlation_id

    ratelimit.consume("ai_invoke", client.client_id, endpoint="ai_invoke")
    organization = _uuid(organization_id)
    request = parse_request(raw)
    ratelimit.consume(
        "ai_invoke_org", f"{client.client_id}:{organization}", endpoint="ai_invoke"
    )
    ratelimit.consume(
        "ai_invoke_skill",
        f"{client.client_id}:{organization}:{request.skill}",
        endpoint="ai_invoke",
    )

    skill = skills.resolve(client.product_code, request.skill, request.version)
    errors = validate(skill.input_schema, request.input)
    if errors:
        raise AIInvalidRequest(
            "The input does not match the skill's input schema.",
            field="input",
            errors=errors,
        )
    query = _retrieval_query(skill, request.retrieval)

    grant = policy.authorize(organization, client.product_code)
    selection = _select(skill, grant)
    cleaned, found = redact_value(request.input)
    if context.fixed_tokens(skill, cleaned) > context.budget_tokens(
        selection.primary, skill.max_output_tokens
    ):
        raise AIContextTooLarge(
            "The skill's instructions and input exceed what the model accepts."
        )

    fingerprint = _fingerprint(skill, request)
    reserved = budget.reserve(
        grant,
        client_id=client.client_id,
        operation=Operation.INVOKE.value,
        mode=request.mode,
        estimate_micros=_estimate(skill, cleaned, selection, query, grant),
        skill_key=skill.key,
        skill_version=skill.version,
        idempotency_key=request.idempotency_key,
        fingerprint=fingerprint,
        correlation_id=current_correlation_id(),
        actor_ref=request.actor,
        request_content=(
            {
                "input": cleaned,
                "retrieval": request.retrieval,
                "redactions": sum(found.values()),
            }
            if request.mode == "async"
            else None
        ),
    )
    execution = reserved.execution
    if not reserved.created:
        return _replay(grant.ctx, execution)
    if found:
        metrics.increment(
            "ai_redactions_total", float(sum(found.values())), where="input"
        )
    if request.mode == "async":
        from workeasy360.integration.scheduling import services as scheduling
        from workeasy360.intelligence.gateway import tasks

        # The fast path; the execution's due-work timer (due now) is the
        # durable one if the broker is down (D11).
        scheduling.enqueue_after_commit(
            tasks.run_execution, [execution.id], ctx=grant.ctx
        )
        return Answer(
            202, {"request_id": str(execution.id), "status": ExecutionStatus.QUEUED.value}
        )

    body = run(
        grant,
        skill,
        execution_id=execution.id,
        value=cleaned,
        query=query,
        selection=selection,
        redactions=sum(found.values()),
        deadline_s=float(settings.AI_SYNC_DEADLINE_S),
        result_ttl_s=int(settings.AI_RESULT_RETENTION_S),
    )
    return Answer(200, body)


def run(
    grant: Any,
    skill: skills.SkillDTO,
    *,
    execution_id: UUID,
    value: Any,
    query: retrieval.RetrievalQuery | None,
    selection: registry.Selection,
    redactions: int,
    deadline_s: float,
    result_ttl_s: int,
) -> dict[str, Any]:
    """Execute a reserved invocation to completion and settle it. Raises the
    error the product sees; the execution is always settled first."""
    started = time.monotonic()
    deadline = Deadline(deadline_s)
    lines: list[budget.UsageLine] = []
    budget.mark_running(grant.ctx, execution_id)
    passages: list[Any] = []
    try:
        if query is not None:
            found = retrieval.search(
                grant, query, deadline=deadline, request_id=execution_id
            )
            lines.extend(found.lines)
            redactions += found.redactions
            if found.failure:
                raise _Failed(
                    "provider_unavailable",
                    AIProviderUnavailable(
                        "Retrieval is unavailable right now. Retry later."
                    ),
                )
            passages = found.chunks

        assembled = context.assemble(
            skill,
            value,
            passages,
            model=selection.primary,
            max_output_tokens=skill.max_output_tokens,
        )
        output, generated, attempts_json = _generate_valid(
            skill, assembled, selection, deadline, execution_id, lines
        )
    except _Failed as failure:
        _settle_failed(grant, execution_id, failure.outcome, lines, redactions, started)
        raise failure.error from None
    except (KnowledgeNotFound, IndexNotReady, AIContextTooLarge) as exc:
        _settle_failed(grant, execution_id, exc.code, lines, redactions, started)
        raise
    except Exception:
        _settle_failed(grant, execution_id, "internal_error", lines, redactions, started)
        raise

    result = {
        "skill": {"key": skill.key, "version": skill.version},
        "output": output,
        "output_format": "json" if skill.output_schema is not None else "text",
        "truncated": generated.result.stop == "max_tokens",
        "citations": context.citations(output, assembled.included),
        "context": [p.citation() for p in assembled.included],
        "model": {
            "provider": generated.model.provider_key,
            "model": generated.model.key,
        },
        "fallback_used": generated.fallback_used,
        "structured_output_attempts": attempts_json,
    }
    settled = budget.settle(
        grant.ctx,
        execution_id,
        succeeded=True,
        outcome="ok",
        lines=lines,
        model_key=generated.model.key,
        provider_key=generated.model.provider_key,
        fallback_used=generated.fallback_used,
        retrieved_chunks=len(assembled.included),
        redactions=redactions,
        latency_ms=int((time.monotonic() - started) * 1000),
        result=result,
        result_ttl_s=result_ttl_s,
    )
    if settled is None:
        # The lease lapsed while this ran and the reservation was settled at
        # its full amount; the answer is still the answer.
        metrics.increment("ai_late_completions_total")
        settled = budget.execution(grant.ctx, execution_id)
    return _body(settled, result)


def job_status(client: Any, *, organization_id: Any, request_id: Any) -> Answer:
    """A product polls its own request. Another product's request, or another
    organization's, is not found (Task 18)."""
    from workeasy360.integration.scheduling.services import routing_context

    organization = _uuid(organization_id)
    try:
        key = UUID(str(request_id))
    except ValueError:
        raise AIRequestNotFound("No such request.") from None
    ctx = routing_context(organization)
    execution = budget.execution(ctx, key) if ctx is not None else None
    if execution is None or execution.product_code != client.product_code:
        raise AIRequestNotFound("No such request.")
    return _replay(ctx, execution, polling=True)


# --- direct retrieval -------------------------------------------------------------------


def retrieve(client: Any, *, organization_id: Any, raw: Any) -> Answer:
    """Nearest authorized passages for a query, without generation (Task 34).
    One query embedding, metered like any other AI call."""
    from workeasy360.platform.ecosystem import ratelimit

    organization = _uuid(organization_id)
    ratelimit.consume(
        "rag_retrieve_org", f"{client.client_id}:{organization}", endpoint="rag_retrieve"
    )
    query = retrieval.parse_query(_json(raw, "query"))
    grant = policy.authorize(organization, client.product_code)
    model = registry.select_embedding(allowed_providers=grant.allowed_providers)
    if model is None or model.price is None:
        raise AIProviderUnavailable("Retrieval is unavailable right now.")
    reserved = budget.reserve(
        grant,
        client_id=client.client_id,
        operation=Operation.RETRIEVE.value,
        mode="sync",
        estimate_micros=cost_micros(
            input_tokens=estimate_tokens(query.text),
            output_tokens=0,
            input_micros_per_mtok=model.price.input_micros_per_mtok,
            output_micros_per_mtok=0,
        ),
    )
    execution_id = reserved.execution.id
    started = time.monotonic()
    budget.mark_running(grant.ctx, execution_id)
    try:
        found = retrieval.search(
            grant,
            query,
            deadline=Deadline(float(settings.AI_SYNC_DEADLINE_S)),
            request_id=execution_id,
        )
    except (KnowledgeNotFound, IndexNotReady) as exc:
        _settle_failed(grant, execution_id, exc.code, [], 0, started)
        raise
    except Exception:
        _settle_failed(grant, execution_id, "internal_error", [], 0, started)
        raise
    if found.failure:
        _settle_failed(
            grant, execution_id, found.failure, found.lines, found.redactions, started
        )
        raise AIProviderUnavailable("Retrieval is unavailable right now. Retry later.")
    settled = budget.settle(
        grant.ctx,
        execution_id,
        succeeded=True,
        outcome="ok",
        lines=found.lines,
        model_key=found.model_key,
        provider_key=found.provider_key,
        retrieved_chunks=len(found.chunks),
        redactions=found.redactions,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    execution = settled or budget.execution(grant.ctx, execution_id)
    return Answer(
        200,
        {
            "request_id": str(execution_id),
            "results": [chunk.public() for chunk in found.chunks],
            "usage": execution.usage() if execution else {},
        },
    )


# --- async jobs -------------------------------------------------------------------------


def run_job(ctx: Any, execution_id: UUID) -> str:
    """The due-work consumer for an execution timer: run a queued async job,
    or settle an expired reservation."""
    execution = budget.execution(ctx, execution_id)
    if execution is None:
        from workeasy360.integration.scheduling import services as scheduling

        scheduling.complete(
            budget.TIMER_KIND, execution_id, organization_id=ctx.organization_id
        )
        return "missing"
    if execution.status != ExecutionStatus.QUEUED.value:
        return budget.expire(ctx, execution_id)

    claimed = budget.claim_job(ctx, execution_id)
    if isinstance(claimed, str):
        return claimed
    execution, request = claimed
    started = time.monotonic()
    if not isinstance(request, dict):
        _settle_quietly(ctx, execution_id, "request_unavailable", started)
        return "failed"
    try:
        grant = policy.authorize(ctx.organization_id, execution.product_code)
        skill = skills.resolve(
            execution.product_code, execution.skill_key, execution.skill_version
        )
        query = _retrieval_query(skill, request.get("retrieval"))
        selection = _select(skill, grant)
    except IntelligenceError as exc:
        # Entitlement lost, AI switched off or the skill retired while the
        # job waited: settle it failed, having spent nothing.
        _settle_quietly(ctx, execution_id, exc.code, started)
        return "failed"
    try:
        run(
            grant,
            skill,
            execution_id=execution_id,
            value=request.get("input"),
            query=query,
            selection=selection,
            redactions=int(request.get("redactions") or 0),
            deadline_s=float(settings.AI_ASYNC_DEADLINE_S),
            result_ttl_s=int(settings.AI_JOB_RESULT_RETENTION_S),
        )
    except IntelligenceError as exc:
        return exc.code
    return "succeeded"


# --- parsing ------------------------------------------------------------------------------


def parse_request(raw: Any) -> InvokeRequest:
    body = _json(raw, "request")
    unknown = sorted(set(body) - _FIELDS)
    if unknown:
        raise AIInvalidRequest(
            f"Unknown field(s): {', '.join(unknown)}.", field=unknown[0]
        )
    skill = body.get("skill")
    if not isinstance(skill, str) or not 0 < len(skill) <= 128:
        raise AIInvalidRequest("skill must be a skill key.", field="skill")
    version = body.get("version")
    if version is not None and (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 1 <= version <= 999
    ):
        raise AIInvalidRequest(
            "version must be an integer from 1 to 999.", field="version"
        )
    if "input" not in body:
        raise AIInvalidRequest("input is required.", field="input")
    encoded = json.dumps(body["input"], ensure_ascii=False)
    if len(encoded.encode("utf-8")) > int(settings.AI_MAX_INPUT_BYTES):
        raise AIInvalidRequest(
            f"input exceeds {settings.AI_MAX_INPUT_BYTES} bytes.", field="input"
        )
    key = body.get("idempotency_key", "") or ""
    if not isinstance(key, str) or len(key) > 64 or (key and not key.isprintable()):
        raise AIInvalidRequest(
            "idempotency_key must be at most 64 printable characters.",
            field="idempotency_key",
        )
    mode = body.get("mode", "sync")
    if mode not in ("sync", "async"):
        raise AIInvalidRequest("mode must be 'sync' or 'async'.", field="mode")
    actor = body.get("actor", "") or ""
    if (
        not isinstance(actor, str)
        or len(actor) > 128
        or (actor and not actor.isprintable())
    ):
        raise AIInvalidRequest(
            "actor must be at most 128 printable characters.", field="actor"
        )
    return InvokeRequest(
        skill=skill,
        version=version,
        input=body["input"],
        retrieval=body.get("retrieval"),
        idempotency_key=key,
        mode=mode,
        actor=actor,
    )


def _json(raw: Any, field: str) -> dict[str, Any]:
    limit = int(settings.AI_MAX_INPUT_BYTES) + MAX_REQUEST_BYTES_OVERHEAD
    if not isinstance(raw, str) or not raw:
        raise AIInvalidRequest(
            f"A '{field}' field holding JSON is required.", field=field
        )
    if len(raw.encode("utf-8")) > limit:
        raise AIInvalidRequest(f"The {field} is too large.", field=field)
    try:
        parsed = json.loads(raw, parse_constant=_reject)
    except (ValueError, RecursionError):
        raise AIInvalidRequest(f"The {field} is not valid JSON.", field=field) from None
    if not isinstance(parsed, dict):
        raise AIInvalidRequest(f"The {field} must be a JSON object.", field=field)
    return parsed


def _reject(token: str) -> Any:
    raise ValueError(token)


def _retrieval_query(skill: skills.SkillDTO, raw: Any) -> retrieval.RetrievalQuery | None:
    if not skill.retrieval_enabled:
        if raw not in (None, {}):
            raise AIInvalidRequest("This skill does not retrieve.", field="retrieval")
        return None
    if raw is None:
        raise AIInvalidRequest("This skill requires retrieval.query.", field="retrieval")
    return retrieval.parse_query(raw, default_top_k=skill.retrieval_top_k)


def _select(skill: skills.SkillDTO, grant: Any) -> registry.Selection:
    capabilities = set(skill.capabilities)
    selection = registry.select_generation(
        capabilities=frozenset(capabilities),
        tier=skill.tier,
        pinned_model_key=skill.pinned_model_key,
        allowed_providers=grant.allowed_providers,
        premium_allowed=grant.premium_allowed,
        allow_provider_fallback=grant.allow_provider_fallback,
    )
    if selection is None:
        metrics.increment(
            "ai_requests_total", operation="invoke", outcome="no_model", provider=""
        )
        raise AIProviderUnavailable(
            "No approved AI model can serve this skill right now."
        )
    return selection


def _estimate(
    skill: skills.SkillDTO,
    value: Any,
    selection: registry.Selection,
    query: retrieval.RetrievalQuery | None,
    grant: Any,
) -> int:
    """The worst case this request may cost: the most expensive permitted
    candidate at the full context it could assemble and its full output
    budget, re-asked as often as the structured-output policy allows, plus
    the query embedding."""
    tokens = context.fixed_tokens(skill, value)
    if query is not None:
        tokens += query.top_k * (-(-int(settings.RAG_CHUNK_CHARS) // 3) + 32)
    calls = 1 + (int(settings.AI_STRUCTURED_OUTPUT_RETRIES) if skill.output_schema else 0)
    worst = 0
    for model in selection.candidates:
        if model.price is None:
            continue
        output = min(skill.max_output_tokens, model.max_output_tokens)
        prompt = min(tokens, context.budget_tokens(model, output))
        worst = max(
            worst,
            calls
            * cost_micros(
                input_tokens=prompt,
                output_tokens=output,
                input_micros_per_mtok=model.price.input_micros_per_mtok,
                output_micros_per_mtok=model.price.output_micros_per_mtok,
            ),
        )
    if query is not None:
        embedding = registry.select_embedding(allowed_providers=grant.allowed_providers)
        if embedding is not None and embedding.price is not None:
            worst += cost_micros(
                input_tokens=estimate_tokens(query.text),
                output_tokens=0,
                input_micros_per_mtok=embedding.price.input_micros_per_mtok,
                output_micros_per_mtok=0,
            )
    return worst


def _fingerprint(skill: skills.SkillDTO, request: InvokeRequest) -> str:
    canonical = json.dumps(
        {
            "skill": skill.ref,
            "input": request.input,
            "retrieval": request.retrieval,
            "mode": request.mode,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- generation and output validation ----------------------------------------------------


class _Failed(Exception):
    def __init__(self, outcome: str, error: IntelligenceError) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.error = error


def _generate_valid(
    skill: skills.SkillDTO,
    assembled: context.Assembled,
    selection: registry.Selection,
    deadline: Deadline,
    execution_id: UUID,
    lines: list[budget.UsageLine],
) -> tuple[Any, invocation.GenerationOutcome, int]:
    """Generate, and for a structured skill re-ask at most
    ``AI_STRUCTURED_OUTPUT_RETRIES`` times until the output validates. Never
    returns output that failed its contract (Task 19)."""
    user = assembled.user
    attempts = 1 + (
        int(settings.AI_STRUCTURED_OUTPUT_RETRIES) if skill.output_schema else 0
    )
    for attempt in range(1, attempts + 1):
        generated = invocation.generate(
            selection,
            system=assembled.system,
            user=user,
            max_output_tokens=skill.max_output_tokens,
            output_schema=skill.output_schema,
            deadline=deadline,
            request_id=execution_id,
        )
        lines.extend(generated.lines)
        if generated.result is None:
            raise _failure_for(generated.failure)
        if generated.result.stop == "refusal":
            raise _Failed(
                "refused",
                AIInvalidOutput(
                    "The model declined to produce this output.", reason="refused"
                ),
            )
        if skill.output_schema is None:
            return generated.result.text, generated, attempt
        ok, parsed = context.parse_json_output(generated.result.text)
        errors = validate(skill.output_schema, parsed) if ok else ["$: not JSON."]
        if not errors:
            return parsed, generated, attempt
        metrics.increment(
            "ai_invalid_outputs_total", provider=generated.model.provider_key
        )
        logger.warning(
            "ai.output.invalid",
            request_id=str(execution_id),
            attempt=attempt,
            errors=len(errors),
        )
        user = (
            assembled.user
            + "\n\nYour previous response did not match the required JSON schema. "
            "Respond again with only the JSON value."
        )
    raise _Failed(
        "invalid_output",
        AIInvalidOutput("The model's output did not match the skill's output contract."),
    )


def _failure_for(category: str) -> _Failed:
    if category == "context_too_large":
        return _Failed(
            category,
            AIContextTooLarge("The assembled context exceeds what the model accepts."),
        )
    if category == "bad_request":
        return _Failed(
            category, AIProviderUnavailable("The AI provider refused the request.")
        )
    return _Failed(
        category if category else "provider_unavailable",
        AIProviderUnavailable("The AI provider is unavailable right now. Retry later."),
    )


def _settle_failed(
    grant: Any,
    execution_id: UUID,
    outcome: str,
    lines: list[budget.UsageLine],
    redactions: int,
    started: float,
) -> None:
    last = next((line for line in reversed(lines) if line.kind == "generation"), None)
    budget.settle(
        grant.ctx,
        execution_id,
        succeeded=False,
        outcome=outcome,
        lines=lines,
        model_key=last.model_key if last else "",
        provider_key=last.provider_key if last else "",
        redactions=redactions,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def _settle_quietly(ctx: Any, execution_id: UUID, outcome: str, started: float) -> None:
    budget.settle(
        ctx,
        execution_id,
        succeeded=False,
        outcome=outcome,
        lines=[],
        latency_ms=int((time.monotonic() - started) * 1000),
    )


# --- responses ----------------------------------------------------------------------------


def _replay(ctx: Any, execution: budget.ExecutionDTO, *, polling: bool = False) -> Answer:
    """The answer for an execution that already exists: a retry under the same
    idempotency key, or a poll."""
    status = ExecutionStatus(execution.status)
    if not status.is_terminal:
        if polling:
            return Answer(
                200, {"request_id": str(execution.id), "status": execution.status}
            )
        raise AIRequestConflict(
            "A request with this idempotency key is still running.",
            request_id=str(execution.id),
        )
    if status is ExecutionStatus.SUCCEEDED:
        stored = budget.stored_result(ctx, execution.id)
        if stored is None:
            if polling:
                return Answer(
                    200,
                    {
                        "request_id": str(execution.id),
                        "status": execution.status,
                        "result_expired": True,
                        "usage": execution.usage(),
                    },
                )
            raise AIRequestConflict(
                "This request already completed and its result is no longer held.",
                request_id=str(execution.id),
            )
        return Answer(200, {**_body(execution, stored), "replayed": not polling})
    return Answer(
        200,
        {
            "request_id": str(execution.id),
            "status": execution.status,
            "outcome": execution.outcome,
            "usage": execution.usage(),
        },
    )


def _body(
    execution: budget.ExecutionDTO | None, result: dict[str, Any]
) -> dict[str, Any]:
    if execution is None:
        return result
    return {
        "request_id": str(execution.id),
        "status": ExecutionStatus.SUCCEEDED.value,
        **result,
        "usage": execution.usage(),
    }


def _uuid(value: Any) -> UUID:
    try:
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            return UUID(value)
    except ValueError:
        pass
    raise AIInvalidRequest("organization_id must be a UUID.", field="organization_id")


# --- skill registration (the owning product, over its credential) ------------------------


def register_skill(client: Any, raw: Any) -> tuple[dict[str, Any], bool]:
    from workeasy360.platform.ecosystem import ratelimit

    ratelimit.consume("ai_registry", client.client_id, endpoint="ai_registry")
    skill, created = skills.register(
        client.product_code, client.client_id, _json(raw, "skill")
    )
    return skill.public(), created


def retire_skill(client: Any, raw: Any) -> dict[str, Any]:
    from workeasy360.platform.ecosystem import ratelimit

    ratelimit.consume("ai_registry", client.client_id, endpoint="ai_registry")
    body = _json(raw, "skill")
    return skills.retire(
        client.product_code, body.get("key"), body.get("version")
    ).public()


def product_skills(client: Any) -> list[dict[str, Any]]:
    return [skill.public() for skill in skills.owned(client.product_code)]
