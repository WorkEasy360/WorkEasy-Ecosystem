"""Spend reservation, settlement and recovery - the AI quota (Tasks 10-12, 58, 59).

::

    reserve   (tx, org scope)  lock this month's budget rows (organization,
                               then product) -> in-flight count under the
                               organization row's lock -> spent + reserved +
                               estimate within every limit -> reserved +=
                               estimate -> execution row -> lease timer
    call      (no tx)          provider(s) through egress
    settle    (tx, org scope)  lock the execution (fenced on its status) ->
                               lock the same budget rows -> reserved -=
                               reservation, spent += actual -> usage lines ->
                               result -> timer removed

**Race-free (Task 11).** The quota decision and the increment happen under
the same row lock, so eight concurrent requests against one remaining
request's budget produce exactly one reservation; the in-flight count is
taken under that lock too, so the concurrency cap cannot be overshot by
requests arriving together (Task 58). Lock order is always organization row,
then product row, so two reservations never deadlock.

**Crash-safe (Task 59).** A reservation is a row plus a due-work timer at its
lease expiry, written in the same transaction. If the caller dies after the
provider call and before settling, the timer fires, and the reservation is
settled ``expired`` at its **full reserved amount** - the provider may have
billed, so the platform assumes it did. A crash can therefore over-count by
at most one reservation; it can never spend without counting. A late settle
from a worker that outlived its lease finds the execution already final and
is discarded (counted), so nothing is counted twice.

**Fail closed (Task 12).** If the reservation cannot be recorded - the
database is unavailable - the request is refused (503). There is no emergency
allowance: the accounting database is Tier 0, and when it is down the
platform is down with it, so an allowance would buy nothing but unmetered
spend. Redis plays no part in any of this: the rate limits in front of the
gateway degrade with the cache, the quota never does (Task 100).

**Estimated cost is not billing.** Everything here is estimated AI
infrastructure cost, in integer micro-units of ``AI_ACCOUNTING_CURRENCY``,
priced at a pinned, immutable price version. It controls spend; it is never
an invoice, and ``commercial`` never reads it (Task 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.errors import (
    AIConcurrencyLimited,
    AIQuotaExceeded,
    AIRequestConflict,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.intelligence.ai.domain import IN_FLIGHT_STATUSES, ExecutionStatus
from workeasy360.intelligence.ai.models import (
    AIBudgetPeriod,
    AIExecution,
    AIExecutionContent,
    AIUsageLine,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: The due-work kind behind every execution: a sync reservation's lease, an
#: async job's run. One timer per execution, removed when it is settled.
TIMER_KIND: Final = "ai.execution"

#: The organization-wide budget row's scope.
ORGANIZATION_SCOPE: Final = ""


@dataclass(frozen=True, slots=True)
class UsageLine:
    """One provider call, costed (``invocation`` builds these)."""

    kind: str
    model_key: str
    provider_key: str
    price_id: Any
    attempt: int
    outcome: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class ExecutionDTO:
    id: UUID
    organization_id: UUID
    product_code: str
    operation: str
    mode: str
    status: str
    outcome: str
    skill_key: str
    skill_version: int | None
    currency: str
    reserved_micros: int
    cost_micros: int
    input_tokens: int
    output_tokens: int
    embedding_tokens: int
    model_key: str
    provider_key: str
    fallback_used: bool
    retrieved_chunks: int
    request_fingerprint: str
    created_at: datetime
    completed_at: datetime | None

    @property
    def is_terminal(self) -> bool:
        return ExecutionStatus(self.status).is_terminal

    def usage(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "embedding_tokens": self.embedding_tokens,
            "estimated_cost_micros": self.cost_micros,
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class Reserved:
    execution: ExecutionDTO
    #: False when an idempotent retry found an existing execution.
    created: bool


def period_start(moment: datetime) -> date:
    return moment.date().replace(day=1)


def period_end(start: date) -> datetime:
    """The first instant of the next period (when limits reset), UTC."""
    from datetime import timezone as dt_timezone

    year, month = (
        (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    )
    return datetime(year, month, 1, tzinfo=dt_timezone.utc)


def reserve(
    grant: Any,
    *,
    client_id: str,
    operation: str,
    mode: str,
    estimate_micros: int,
    skill_key: str = "",
    skill_version: int | None = None,
    idempotency_key: str = "",
    fingerprint: str = "",
    correlation_id: UUID | None = None,
    actor_ref: str = "",
    request_content: dict[str, Any] | None = None,
) -> Reserved:
    """Reserve ``estimate_micros`` for one execution, or refuse.

    ``grant`` is ``policy.AccessGrant``. Raises ``AIQuotaExceeded``,
    ``AIConcurrencyLimited`` or ``AIRequestConflict``; an idempotent retry
    returns the existing execution with ``created=False`` and reserves
    nothing.
    """
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    now = timezone.now()
    is_async = mode == "async"
    lease_s = int(settings.AI_ASYNC_LEASE_S if is_async else settings.AI_SYNC_LEASE_S)
    estimate = max(0, int(estimate_micros))

    with tenant_context(grant.ctx), organization_db_scope(grant.organization_id):
        if idempotency_key:
            existing = _by_idempotency_key(grant.product_code, idempotency_key)
            if existing is not None:
                return Reserved(_checked_replay(existing, fingerprint), created=False)
        try:
            with transaction.atomic():
                org_row, product_row = _locked_periods(
                    grant.product_code, period_start(now)
                )
                _check_concurrency(now)
                _check_limit(
                    org_row, grant.organization_limit_micros, estimate, "organization"
                )
                if grant.product_limit_micros is not None:
                    _check_limit(
                        product_row, grant.product_limit_micros, estimate, "product"
                    )
                for row in (org_row, product_row):
                    row.reserved_micros += estimate
                    row.save(update_fields=["reserved_micros", "updated_at"])
                execution = AIExecution.objects.create(
                    product_code=grant.product_code,
                    client_id=client_id,
                    operation=operation,
                    mode=mode,
                    skill_key=skill_key,
                    skill_version=skill_version,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    status=(
                        ExecutionStatus.QUEUED.value
                        if is_async
                        else ExecutionStatus.RESERVED.value
                    ),
                    currency=settings.AI_ACCOUNTING_CURRENCY,
                    reserved_micros=estimate,
                    correlation_id=correlation_id,
                    actor_ref=actor_ref[:128],
                    lease_expires_at=now + timezone.timedelta(seconds=lease_s),
                )
                if request_content is not None:
                    AIExecutionContent.objects.create(
                        execution=execution,
                        request=request_content,
                        expires_at=now
                        + timezone.timedelta(
                            seconds=int(settings.AI_JOB_RESULT_RETENTION_S)
                        ),
                    )
                scheduling.schedule(
                    TIMER_KIND,
                    organization_id=grant.organization_id,
                    target_id=execution.id,
                    due_at=now if is_async else execution.lease_expires_at,
                )
        except IntegrityError:
            # Two concurrent requests under one idempotency key: the unique
            # constraint chose the winner, whose execution this one returns.
            existing = _by_idempotency_key(grant.product_code, idempotency_key)
            if existing is None:
                raise
            return Reserved(_checked_replay(existing, fingerprint), created=False)

    metrics.increment("ai_reservations_total", operation=operation, result="reserved")
    return Reserved(to_dto(execution), created=True)


def claim_job(ctx: TenantContext, execution_id: UUID) -> tuple[ExecutionDTO, Any] | str:
    """Move a queued async job to ``running`` with a fresh lease; return it
    and its stored request. Fenced: a job already running or finished is
    reported, not run twice."""
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    now = timezone.now()
    with (
        tenant_context(ctx),
        organization_db_scope(ctx.organization_id),
        transaction.atomic(),
    ):
        row = AIExecution.objects.select_for_update().filter(id=execution_id).first()
        if row is None:
            scheduling.complete(TIMER_KIND, execution_id)
            return "missing"
        if row.status != ExecutionStatus.QUEUED.value:
            return row.status
        row.status = ExecutionStatus.RUNNING.value
        row.started_at = now
        row.lease_expires_at = now + timezone.timedelta(
            seconds=int(settings.AI_ASYNC_LEASE_S)
        )
        row.save(update_fields=["status", "started_at", "lease_expires_at", "updated_at"])
        content = AIExecutionContent.objects.filter(execution_id=row.id).first()
        request = content.request if content is not None else None
        if content is not None:
            # Minimisation: the request lives only until the job starts.
            content.request = None
            content.save(update_fields=["request", "updated_at"])
        scheduling.schedule(
            TIMER_KIND,
            organization_id=ctx.organization_id,
            target_id=row.id,
            due_at=row.lease_expires_at,
        )
    return to_dto(row), request


def mark_running(ctx: TenantContext, execution_id: UUID) -> None:
    """A sync reservation is about to call a provider."""
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with tenant_context(ctx), organization_db_scope(ctx.organization_id):
        AIExecution.objects.filter(
            id=execution_id, status=ExecutionStatus.RESERVED.value
        ).update(status=ExecutionStatus.RUNNING.value, started_at=timezone.now())


def settle(
    ctx: TenantContext,
    execution_id: UUID,
    *,
    succeeded: bool,
    outcome: str,
    lines: list[UsageLine],
    model_key: str = "",
    provider_key: str = "",
    fallback_used: bool = False,
    retrieved_chunks: int = 0,
    redactions: int = 0,
    latency_ms: int | None = None,
    result: dict[str, Any] | None = None,
    result_ttl_s: int | None = None,
) -> ExecutionDTO | None:
    """Record what an execution actually cost and how it ended.

    Returns ``None`` when the execution was already final - a late worker
    after its lease expired - and changes nothing (fenced)."""
    return _finish(
        ctx,
        execution_id,
        status=ExecutionStatus.SUCCEEDED if succeeded else ExecutionStatus.FAILED,
        outcome=outcome,
        lines=lines,
        charge_reserved=False,
        fields={
            "model_key": model_key,
            "provider_key": provider_key,
            "fallback_used": fallback_used,
            "retrieved_chunks": retrieved_chunks,
            "redactions": redactions,
            "latency_ms": latency_ms,
        },
        result=result,
        result_ttl_s=result_ttl_s,
    )


def expire(ctx: TenantContext, execution_id: UUID) -> str:
    """The lease timer fired. A reservation whose caller vanished is settled
    at its full reserved amount; a job that never started is released at
    nothing (no provider was called); anything live or final is left."""
    now = timezone.now()
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with tenant_context(ctx), organization_db_scope(ctx.organization_id):
        row = AIExecution.objects.filter(id=execution_id).first()
    if row is None:
        return "missing"
    status = row.status_enum
    if status.is_terminal:
        return status.value
    if row.lease_expires_at > now:
        return "live"
    never_started = status is ExecutionStatus.QUEUED
    finished = _finish(
        ctx,
        execution_id,
        status=ExecutionStatus.EXPIRED,
        outcome="never_started" if never_started else "abandoned",
        lines=[],
        charge_reserved=not never_started,
        fields={},
        result=None,
        result_ttl_s=None,
        expected=(status.value,),
    )
    if finished is None:
        return "raced"
    metrics.increment(
        "ai_reservations_expired_total", charged=str(not never_started).lower()
    )
    logger.warning(
        "ai.execution.expired",
        request_id=str(execution_id),
        charged_micros=finished.cost_micros,
    )
    return "expired"


def execution(ctx: TenantContext, execution_id: UUID) -> ExecutionDTO | None:
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with tenant_context(ctx), organization_db_scope(ctx.organization_id):
        row = AIExecution.objects.filter(id=execution_id).first()
    return to_dto(row) if row is not None else None


def stored_result(ctx: TenantContext, execution_id: UUID) -> dict[str, Any] | None:
    """A validated result that has not yet expired, or ``None``."""
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with tenant_context(ctx), organization_db_scope(ctx.organization_id):
        content = (
            AIExecutionContent.objects.filter(
                execution_id=execution_id, expires_at__gt=timezone.now()
            )
            .values("result")
            .first()
        )
    return content["result"] if content is not None else None


def to_dto(row: AIExecution) -> ExecutionDTO:
    return ExecutionDTO(
        id=row.id,
        organization_id=row.organization_id,
        product_code=row.product_code,
        operation=row.operation,
        mode=row.mode,
        status=row.status,
        outcome=row.outcome,
        skill_key=row.skill_key,
        skill_version=row.skill_version,
        currency=row.currency,
        reserved_micros=row.reserved_micros,
        cost_micros=row.cost_micros,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        embedding_tokens=row.embedding_tokens,
        model_key=row.model_key,
        provider_key=row.provider_key,
        fallback_used=row.fallback_used,
        retrieved_chunks=row.retrieved_chunks,
        request_fingerprint=row.request_fingerprint,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


# --- internals -------------------------------------------------------------------------


def _finish(
    ctx: TenantContext,
    execution_id: UUID,
    *,
    status: ExecutionStatus,
    outcome: str,
    lines: list[UsageLine],
    charge_reserved: bool,
    fields: dict[str, Any],
    result: dict[str, Any] | None,
    result_ttl_s: int | None,
    expected: tuple[str, ...] = (
        ExecutionStatus.RESERVED.value,
        ExecutionStatus.RUNNING.value,
    ),
) -> ExecutionDTO | None:
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    now = timezone.now()
    with (
        tenant_context(ctx),
        organization_db_scope(ctx.organization_id),
        transaction.atomic(),
    ):
        row = AIExecution.objects.select_for_update().filter(id=execution_id).first()
        if row is None or row.status not in expected:
            metrics.increment("ai_late_completions_total")
            return None

        generation = [line for line in lines if line.kind == "generation"]
        embedding = [line for line in lines if line.kind == "embedding"]
        cost = (
            row.reserved_micros
            if charge_reserved
            else sum(li.cost_micros for li in lines)
        )
        input_tokens = sum(li.input_tokens for li in generation)
        output_tokens = sum(li.output_tokens for li in generation)
        embedding_tokens = sum(li.input_tokens for li in embedding)

        org_row, product_row = _locked_periods(
            row.product_code, period_start(row.created_at)
        )
        for period in (org_row, product_row):
            period.reserved_micros = max(0, period.reserved_micros - row.reserved_micros)
            period.spent_micros += cost
            period.requests += 1
            period.input_tokens += input_tokens
            period.output_tokens += output_tokens
            period.embedding_tokens += embedding_tokens
            period.save()

        row.status = status.value
        row.outcome = outcome
        row.cost_micros = cost
        row.input_tokens = input_tokens
        row.output_tokens = output_tokens
        row.embedding_tokens = embedding_tokens
        row.provider_calls = len(lines)
        row.completed_at = now
        for key, value in fields.items():
            setattr(row, key, value)
        row.save()

        if lines:
            AIUsageLine.objects.bulk_create(
                [
                    AIUsageLine(
                        execution_id=row.id,
                        kind=line.kind,
                        model_key=line.model_key,
                        provider_key=line.provider_key,
                        price_id=line.price_id,
                        attempt=line.attempt,
                        outcome=line.outcome,
                        input_tokens=line.input_tokens,
                        output_tokens=line.output_tokens,
                        cost_micros=line.cost_micros,
                        latency_ms=line.latency_ms,
                    )
                    for line in lines
                ]
            )
        if result is not None:
            ttl = int(result_ttl_s or settings.AI_RESULT_RETENTION_S)
            # One upsert: a sync call has no row yet, an async job's row holds
            # its input, which the result replaces.
            AIExecutionContent.objects.bulk_create(
                [
                    AIExecutionContent(
                        execution_id=row.id,
                        request=None,
                        result=result,
                        expires_at=now + timezone.timedelta(seconds=ttl),
                    )
                ],
                update_conflicts=True,
                unique_fields=["execution"],
                update_fields=["request", "result", "expires_at", "updated_at"],
            )
        else:
            AIExecutionContent.objects.filter(execution_id=row.id).delete()
        scheduling.complete(TIMER_KIND, row.id)

    metrics.increment(
        "ai_requests_total",
        operation=row.operation,
        outcome=outcome,
        provider=row.provider_key,
    )
    if cost:
        metrics.increment(
            "ai_estimated_cost_micros_total",
            float(cost),
            provider=row.provider_key or "none",
        )
    for line in lines:
        metrics.increment(
            "ai_tokens_total",
            float(line.input_tokens),
            provider=line.provider_key,
            kind=f"{line.kind}_input",
        )
        if line.output_tokens:
            metrics.increment(
                "ai_tokens_total",
                float(line.output_tokens),
                provider=line.provider_key,
                kind="generation_output",
            )
    logger.info(
        "ai.execution.settled",
        request_id=str(row.id),
        operation=row.operation,
        status=row.status,
        outcome=outcome,
        model=row.model_key,
        provider=row.provider_key,
        cost_micros=cost,
        provider_calls=len(lines),
    )
    return to_dto(row)


def _locked_periods(
    product_code: str, start: date
) -> tuple[AIBudgetPeriod, AIBudgetPeriod]:
    """This period's organization and product rows, created if missing and
    locked in a fixed order (organization first).

    Locked first, created only when missing - once a month per scope - so the
    usual reservation is one statement. Every caller takes the organization
    row's lock before it inserts anything, so two callers can never hold
    each other's rows."""

    def lock() -> dict[str, AIBudgetPeriod]:
        rows = (
            AIBudgetPeriod.objects.select_for_update()
            .filter(period_start=start, scope__in=[ORGANIZATION_SCOPE, product_code])
            .order_by("scope")
        )
        return {row.scope: row for row in rows}

    by_scope = lock()
    if len(by_scope) < 2:
        AIBudgetPeriod.objects.bulk_create(
            [
                AIBudgetPeriod(period_start=start, scope=ORGANIZATION_SCOPE),
                AIBudgetPeriod(period_start=start, scope=product_code),
            ],
            ignore_conflicts=True,
        )
        by_scope = lock()
    return by_scope[ORGANIZATION_SCOPE], by_scope[product_code]


def _check_concurrency(now: datetime) -> None:
    """Called under the organization row's lock, so concurrent reservations
    count each other."""
    from django.db.models import Q

    in_flight = AIExecution.objects.filter(
        Q(status=ExecutionStatus.QUEUED.value)
        | Q(status__in=IN_FLIGHT_STATUSES, lease_expires_at__gt=now)
    ).count()
    if in_flight >= int(settings.AI_MAX_CONCURRENT_PER_ORG):
        metrics.increment("ai_reservations_total", operation="any", result="concurrency")
        raise AIConcurrencyLimited(
            "The organization has the maximum number of AI requests in flight. "
            "Retry shortly.",
            limit=int(settings.AI_MAX_CONCURRENT_PER_ORG),
        )


def _check_limit(row: AIBudgetPeriod, limit: int, estimate: int, scope: str) -> None:
    if row.spent_micros + row.reserved_micros + estimate <= limit:
        return
    metrics.increment("ai_reservations_total", operation="any", result=f"quota_{scope}")
    raise AIQuotaExceeded(
        "This request would exceed the AI spend limit for this period.",
        scope=scope,
        resets_at=period_end(row.period_start).isoformat(),
    )


def _by_idempotency_key(product_code: str, key: str) -> AIExecution | None:
    return AIExecution.objects.filter(
        product_code=product_code, idempotency_key=key
    ).first()


def _checked_replay(row: AIExecution, fingerprint: str) -> ExecutionDTO:
    if row.request_fingerprint != fingerprint:
        raise AIRequestConflict("This idempotency key was used for a different request.")
    metrics.increment("ai_reservations_total", operation=row.operation, result="replayed")
    return to_dto(row)
