"""The AI gateway's tables.

Two kinds, and the split is the security model:

**Control plane** (no ``organization``, no RLS - like ``catalog.Product``):
the provider and model registry, versioned prices, and the skill registry.
They describe *the platform*, are written only by staff services and by a
product's authenticated registration, and hold no tenant data and no secret:
a provider row carries a *reference* to its credential in the secret manager,
never the key (Task 6).

**Tenant data** (``TenantScopedModel``, RLS, composite keys): an
organization's AI policy, its spend per month, and one execution record per
AI request with its provider calls. None of these holds a prompt, a model
response or a retrieved document - metering and audit need identifiers,
counts and costs, not content (Tasks 8, 46, 47). The single exception is
``AIExecutionContent``: an async job's request until it runs and a result
until it is read or expires, on a short, enforced retention.
"""

from __future__ import annotations

import uuid

from django.db import models

from workeasy360.contracts.enums import StrEnum
from workeasy360.intelligence.ai.domain import (
    ExecutionStatus,
    ModelTier,
    Operation,
    RegistryStatus,
    SkillStatus,
)
from workeasy360.platform.tenancy.models import TenantScopedModel


def _choices(enum_cls: type[StrEnum]) -> list[tuple[str, str]]:
    return [(m.value, m.name.replace("_", " ").title()) for m in enum_cls]


# --- control plane: the registry ------------------------------------------------


class AIProvider(models.Model):
    """One AI vendor account the platform may call.

    ``adapter`` names code (``intelligence/ai/providers``) - the only place a
    request shape and an endpoint live. There is no URL column: a provider's
    endpoint comes from its adapter, and production refuses any override, so
    a compromised staff account cannot redirect traffic (Tasks 103, 104).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=32, unique=True)
    adapter = models.CharField(max_length=32)
    display_name = models.CharField(max_length=80)
    status = models.CharField(
        max_length=16,
        choices=_choices(RegistryStatus),
        default=RegistryStatus.ACTIVE.value,
    )
    #: The secret manager reference (``W360_AI_CREDENTIAL_<REF>``). A name,
    #: never the key; resolved per call by ``credentials.api_key``.
    credential_ref = models.CharField(max_length=64)
    # What the platform discloses about the vendor's data handling (Task 45).
    # Descriptive metadata the organization's policy is decided against - the
    # platform claims no residency or retention it does not enforce itself.
    data_region = models.CharField(max_length=64, blank=True, default="")
    retains_inputs_days = models.PositiveIntegerField(null=True, blank=True)
    trains_on_inputs = models.BooleanField(default=False)
    data_policy_note = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_provider"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.key


class AIModel(models.Model):
    """One model of one provider, as the platform approves it (Task 4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: The platform's stable name, e.g. ``anthropic.claude-opus-5``. Skills
    #: may pin it; executions record it.
    key = models.CharField(max_length=64, unique=True)
    provider = models.ForeignKey(AIProvider, on_delete=models.PROTECT, related_name="+")
    #: What the provider's API calls it. Sent verbatim; never shown to a model.
    model_id = models.CharField(max_length=128)
    display_name = models.CharField(max_length=80)
    status = models.CharField(
        max_length=16,
        choices=_choices(RegistryStatus),
        default=RegistryStatus.ACTIVE.value,
    )
    tier = models.CharField(
        max_length=16, choices=_choices(ModelTier), default=ModelTier.STANDARD.value
    )
    #: ``Capability`` values, sorted.
    capabilities = models.JSONField(default=list)
    context_window_tokens = models.PositiveIntegerField()
    max_output_tokens = models.PositiveIntegerField()
    embedding_dimensions = models.PositiveIntegerField(null=True, blank=True)
    #: Selection order among eligible models: lower first.
    priority = models.PositiveSmallIntegerField(default=100)
    # Circuit breaker (Task 51). Written only on failure, and on the first
    # success after one - never on every call.
    consecutive_failures = models.PositiveIntegerField(default=0)
    circuit_open_until = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_model"
        indexes = [models.Index(fields=["status", "priority"], name="ix_ai_model_select")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.key


class AIModelPrice(models.Model):
    """What one model costs, as a numbered, immutable version (Task 9).

    Integer micro-units of the accounting currency per million tokens, so cost
    is exact integer arithmetic. A price change is a new version; every usage
    line points at the version it was costed with, so history is never
    re-priced (a trigger refuses ``UPDATE``).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(AIModel, on_delete=models.PROTECT, related_name="+")
    version = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)
    input_micros_per_mtok = models.BigIntegerField()
    output_micros_per_mtok = models.BigIntegerField()
    effective_from = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_model_price"
        constraints = [
            models.UniqueConstraint(
                fields=["model", "version"], name="uq_ai_price_version"
            ),
            models.CheckConstraint(
                condition=models.Q(input_micros_per_mtok__gte=0)
                & models.Q(output_micros_per_mtok__gte=0),
                name="ck_ai_price_non_negative",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.model_id} v{self.version}"


class AISkill(models.Model):
    """A named AI contract: ``product.<code>.<name>`` or ``platform.<name>``.

    The owner is derived from the key, and the key from the registering
    product's authenticated code - so Product A cannot hold a row in Product
    B's namespace (Task 14).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=128, unique=True)
    owner = models.CharField(max_length=32, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_skill"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.key


class AISkillVersion(models.Model):
    """One immutable version of a skill: its prompt and its contract.

    The prompt registry and the skill registry are this one record (Tasks
    13, 17): a prompt without its input/output contract is not reproducible,
    and two tables for one artefact is how they drift. Registration is
    activation; after that only ``status`` may change (a trigger refuses
    anything else), so an execution that names ``key`` v``n`` names exactly
    one prompt forever.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(AISkill, on_delete=models.PROTECT, related_name="+")
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=_choices(SkillStatus), default=SkillStatus.ACTIVE.value
    )
    description = models.CharField(max_length=300, blank=True, default="")
    #: The skill's trusted instructions. Static text: the input is never
    #: interpolated into it (Task 55).
    instructions = models.TextField()
    input_schema = models.JSONField()
    #: ``None`` means free text. When present the output is validated against
    #: it and nothing else is ever returned as structured (Task 19).
    output_schema = models.JSONField(null=True, blank=True)
    capabilities = models.JSONField(default=list)
    tier = models.CharField(
        max_length=16, choices=_choices(ModelTier), default=ModelTier.STANDARD.value
    )
    pinned_model_key = models.CharField(max_length=64, blank=True, default="")
    max_output_tokens = models.PositiveIntegerField()
    retrieval_enabled = models.BooleanField(default=False)
    retrieval_top_k = models.PositiveSmallIntegerField(default=5)
    #: Product codes the owner lets invoke this skill. Empty = owner only
    #: (deny by default, Task 18). Platform skills are shared by definition.
    shared_with = models.JSONField(default=list)
    #: SHA-256 of the canonical definition: re-registering the identical
    #: version is idempotent, a different one under the same number is 409.
    definition_hash = models.CharField(max_length=64)
    registered_by = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_skill_version"
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "version"], name="uq_ai_skill_version"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.skill_id} v{self.version}"


# --- tenant data: policy, spend, executions ---------------------------------------


class OrganizationAIPolicy(TenantScopedModel):
    """One organization's AI settings (Tasks 45, 88).

    Two owners on one row, separated by the services that write them: the
    organization's administrator sets the first group, Workeasy360 staff the
    second. Neither service can write the other's fields.
    """

    # --- the organization's administrator ---
    ai_enabled = models.BooleanField(default=True)
    #: A self-imposed monthly cap, at most the platform cap. ``None`` = the
    #: platform cap alone.
    monthly_budget_micros = models.BigIntegerField(null=True, blank=True)
    #: Provider keys the organization permits. Empty = every approved one.
    allowed_providers = models.JSONField(default=list)
    #: May a request fall back to *another vendor* when the first is down?
    #: Off by default: data never moves to a second vendor silently (Task 52).
    allow_provider_fallback = models.BooleanField(default=False)
    # --- Workeasy360 staff ---
    platform_suspended = models.BooleanField(default=False)
    platform_suspension_reason = models.CharField(max_length=200, blank=True, default="")
    #: ``None`` = ``AI_DEFAULT_ORG_MONTHLY_CAP_MICROS``.
    platform_cap_micros = models.BigIntegerField(null=True, blank=True)
    premium_models_allowed = models.BooleanField(default=False)
    #: Optimistic-concurrency token for the administrator's edits.
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "ai_org_policy"
        constraints = [
            models.UniqueConstraint(fields=["organization"], name="uq_ai_policy_org"),
            models.CheckConstraint(
                condition=models.Q(monthly_budget_micros__isnull=True)
                | models.Q(monthly_budget_micros__gte=0),
                name="ck_ai_policy_budget_non_negative",
            ),
        ]


class AIBudgetPeriod(TenantScopedModel):
    """Spend for one organization, one calendar month (UTC), one scope.

    ``scope`` is ``""`` for the whole organization and a product code for
    that product's share. The row lock on these rows is what makes the quota
    race-free (Task 11): a reservation reads and increments ``reserved``
    under ``SELECT ... FOR UPDATE``, so eight concurrent requests against one
    remaining request's budget produce exactly one reservation.
    """

    period_start = models.DateField()
    scope = models.CharField(max_length=32, blank=True, default="")
    reserved_micros = models.BigIntegerField(default=0)
    spent_micros = models.BigIntegerField(default=0)
    requests = models.BigIntegerField(default=0)
    input_tokens = models.BigIntegerField(default=0)
    output_tokens = models.BigIntegerField(default=0)
    embedding_tokens = models.BigIntegerField(default=0)

    #: Exempt from the R-10 suspension write block (ADR-030): metering records
    #: spend that has *already happened* at a provider. New AI work for a
    #: suspended organization is refused before anything is reserved
    #: (``policy.authorize``); refusing to record a call already made would
    #: under-count spend, the opposite of what suspension is for.
    tenancy_write_block_exempt = True

    class Meta:
        db_table = "ai_budget_period"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "period_start", "scope"],
                name="uq_ai_budget_period",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_micros__gte=0)
                & models.Q(spent_micros__gte=0),
                name="ck_ai_budget_non_negative",
            ),
        ]


class AIExecution(TenantScopedModel):
    """One AI request: its reservation, its outcome, its usage and cost.

    The AI audit trail (Task 47) and the metering record (Task 8) in one row:
    who (product, credential, product-asserted actor), what (operation,
    skill, model, provider), how much (tokens, estimated cost at a pinned
    price version) and how it ended - with a stable id that is also the
    request id every log line, provider call and response carries (Task 48).
    """

    product_code = models.CharField(max_length=32)
    client_id = models.CharField(max_length=64, blank=True, default="")
    operation = models.CharField(max_length=16, choices=_choices(Operation))
    mode = models.CharField(max_length=8, default="sync")
    skill_key = models.CharField(max_length=128, blank=True, default="")
    skill_version = models.PositiveIntegerField(null=True, blank=True)
    #: The product's retry key. Unique per (organization, product) when set.
    idempotency_key = models.CharField(max_length=64, blank=True, default="")
    #: SHA-256 of the canonical request, so a reused key with a different
    #: request is a conflict rather than a silent replay.
    request_fingerprint = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=_choices(ExecutionStatus))
    #: A bounded code: ``ok``, ``provider_unavailable``, ``invalid_output``...
    outcome = models.CharField(max_length=48, blank=True, default="")
    currency = models.CharField(max_length=3)
    reserved_micros = models.BigIntegerField(default=0)
    cost_micros = models.BigIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    embedding_tokens = models.PositiveIntegerField(default=0)
    provider_calls = models.PositiveSmallIntegerField(default=0)
    model_key = models.CharField(max_length=64, blank=True, default="")
    provider_key = models.CharField(max_length=32, blank=True, default="")
    fallback_used = models.BooleanField(default=False)
    retrieved_chunks = models.PositiveSmallIntegerField(default=0)
    #: Forbidden-secret redactions applied before anything left the platform
    #: (a count; the values are never stored).
    redactions = models.PositiveSmallIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    #: The end user the product says it is acting for - product-asserted,
    #: recorded for audit, never used to authorize anything.
    actor_ref = models.CharField(max_length=128, blank=True, default="")
    lease_expires_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    #: Exempt from the R-10 suspension write block (ADR-030): metering records
    #: spend that has *already happened* at a provider. New AI work for a
    #: suspended organization is refused before anything is reserved
    #: (``policy.authorize``); refusing to record a call already made would
    #: under-count spend, the opposite of what suspension is for.
    tenancy_write_block_exempt = True

    class Meta:
        db_table = "ai_execution"
        constraints = [
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_ai_execution_id_org"
            ),
            models.UniqueConstraint(
                fields=["organization", "product_code", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uq_ai_execution_idempotency",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_micros__gte=0) & models.Q(cost_micros__gte=0),
                name="ck_ai_execution_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="ix_ai_exec_org_status"),
            models.Index(
                fields=["organization", "-created_at"], name="ix_ai_exec_org_created"
            ),
        ]

    @property
    def status_enum(self) -> ExecutionStatus:
        return ExecutionStatus(self.status)


class AIUsageLine(TenantScopedModel):
    """One provider call, costed at the price version current when made.

    Immutable once written (a trigger refuses ``UPDATE``). Failed calls are
    recorded too - with zero tokens - because "the provider returned 429
    three times" is exactly what a cost or latency investigation needs.
    """

    execution = models.ForeignKey(AIExecution, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=16)
    model_key = models.CharField(max_length=64)
    provider_key = models.CharField(max_length=32)
    price = models.ForeignKey(
        AIModelPrice, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    attempt = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=32)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_micros = models.BigIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)

    #: Exempt from the R-10 suspension write block (ADR-030): metering records
    #: spend that has *already happened* at a provider. New AI work for a
    #: suspended organization is refused before anything is reserved
    #: (``policy.authorize``); refusing to record a call already made would
    #: under-count spend, the opposite of what suspension is for.
    tenancy_write_block_exempt = True

    class Meta:
        db_table = "ai_usage_line"
        indexes = [
            models.Index(
                fields=["organization", "execution"], name="ix_ai_usage_org_exec"
            )
        ]


class AIExecutionContent(TenantScopedModel):
    """The only AI table that holds content (Task 46), for as short as it can.

    - ``request``: an async job's validated input, deleted when the job runs.
    - ``result``: a *validated* output and its citations, kept until
      ``expires_at`` so an idempotent retry or a poll can read it. Malformed
      model output is never stored here (Task 19).
    """

    execution = models.OneToOneField(
        AIExecution, on_delete=models.CASCADE, related_name="+"
    )
    request = models.JSONField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    expires_at = models.DateTimeField()

    #: Exempt from the R-10 suspension write block (ADR-030): metering records
    #: spend that has *already happened* at a provider. New AI work for a
    #: suspended organization is refused before anything is reserved
    #: (``policy.authorize``); refusing to record a call already made would
    #: under-count spend, the opposite of what suspension is for.
    tenancy_write_block_exempt = True

    class Meta:
        db_table = "ai_execution_content"
        indexes = [
            models.Index(
                fields=["organization", "expires_at"], name="ix_ai_content_expiry"
            )
        ]
