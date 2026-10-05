"""Super Admin control of the AI platform (Task 87).

``platform.staff_ai.view``: the model registry, provider status, an
organization's AI policy and its usage - read-only diagnostics that return no
credential (none is stored; a provider shows only *whether* its secret
reference resolves), no prompt and no output.

``platform.staff_ai.manage`` (dangerous: StaffGrant + AAL 2, one
``authz.check``): switch a provider or model on or off, reorder models, add a
model, publish a new price version, and set an organization's platform-side
AI policy (suspend its AI, set its platform cap, allow premium models). Every
change is audited - on the platform's own log, and for an organization's
policy on the customer's log too, where its administrators read it.

What staff cannot do here, by construction: name a provider endpoint (the
adapter owns it, production pins overrides empty), read or set an API key
(the secret manager owns it), or change a published price (prices are
versioned and immutable).
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from workeasy360.contracts.errors import AIInvalidRequest, IntelligenceError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.intelligence.ai import credentials, registry
from workeasy360.intelligence.ai.domain import Capability, ModelTier, RegistryStatus
from workeasy360.intelligence.ai.models import (
    AIBudgetPeriod,
    AIExecution,
    AIModel,
    AIModelPrice,
    AIProvider,
    OrganizationAIPolicy,
)
from workeasy360.intelligence.ai.policy import MAX_BUDGET_MICROS, policy_for
from workeasy360.intelligence.ai.providers import adapter_for
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope, organization_scope

VIEW: Final = "platform.staff_ai.view"
MANAGE: Final = "platform.staff_ai.manage"

MIN_REASON: Final = 10
MAX_REASON: Final = 500
_KEY_CHARS: Final = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-._")


class RegistryNotFound(IntelligenceError):
    code = "ai_registry_not_found"
    http_status = 404


@in_scope
def registry_view(ctx: TenantContext) -> dict[str, Any]:
    _require(ctx, VIEW)
    models = registry.all_models()
    return {
        "providers": [
            {
                "key": row.key,
                "display_name": row.display_name,
                "adapter": row.adapter,
                "status": row.status,
                "data_region": row.data_region,
                "retains_inputs_days": row.retains_inputs_days,
                "trains_on_inputs": row.trains_on_inputs,
                "data_policy_note": row.data_policy_note,
                # Whether the secret reference resolves - never its value.
                "credential_configured": credentials.is_configured(row.credential_ref),
            }
            for row in AIProvider.objects.order_by("key")
        ],
        "models": [
            {
                "key": m.key,
                "provider": m.provider_key,
                "model_id": m.model_id,
                "status": m.status,
                "tier": m.tier,
                "capabilities": sorted(m.capabilities),
                "context_window_tokens": m.context_window_tokens,
                "max_output_tokens": m.max_output_tokens,
                "embedding_dimensions": m.embedding_dimensions,
                "priority": m.priority,
                "circuit_open_until": (
                    m.circuit_open_until.isoformat() if m.circuit_open_until else None
                ),
                "price": (
                    {
                        "version": m.price.version,
                        "currency": m.price.currency,
                        "input_micros_per_mtok": m.price.input_micros_per_mtok,
                        "output_micros_per_mtok": m.price.output_micros_per_mtok,
                    }
                    if m.price is not None
                    else None
                ),
            }
            for m in models
        ],
    }


@in_scope
def set_provider_status(
    ctx: TenantContext, provider_key: Any, *, status: Any, reason: Any
) -> dict[str, Any]:
    """The kill switch for a vendor. Disabling takes effect on the next
    selection; in-flight calls finish."""
    from workeasy360.platform.audit import services as audit

    _require(ctx, MANAGE)
    target = _status(status)
    why = _reason(reason)
    with transaction.atomic():
        row = AIProvider.objects.select_for_update().filter(key=str(provider_key)).first()
        if row is None:
            raise RegistryNotFound("No such provider.")
        before = row.status
        row.status = target
        row.save(update_fields=["status", "updated_at"])
        audit.record(
            ctx,
            "ai.provider.status_changed",
            resource_type="ai.provider",
            resource_id=row.id,
            changes={"provider": row.key, "from": before, "to": target},
            reason=why,
        )
    return registry_view(ctx)


@in_scope
def update_model(
    ctx: TenantContext,
    model_key: Any,
    *,
    reason: Any,
    status: Any = None,
    priority: Any = None,
) -> dict[str, Any]:
    from workeasy360.platform.audit import services as audit

    _require(ctx, MANAGE)
    why = _reason(reason)
    changes: dict[str, Any] = {}
    if status is not None:
        changes["status"] = _status(status)
    if priority is not None:
        changes["priority"] = _int(priority, "priority", 0, 1000)
    if not changes:
        raise AIInvalidRequest("Nothing to change.", field="status")
    with transaction.atomic():
        row = AIModel.objects.select_for_update().filter(key=str(model_key)).first()
        if row is None:
            raise RegistryNotFound("No such model.")
        before = {k: getattr(row, k) for k in changes}
        for k, v in changes.items():
            setattr(row, k, v)
        row.save(update_fields=[*changes, "updated_at"])
        audit.record(
            ctx,
            "ai.model.updated",
            resource_type="ai.model",
            resource_id=row.id,
            changes={"model": row.key, "before": before, "after": changes},
            reason=why,
        )
    return registry_view(ctx)


@in_scope
def create_model(ctx: TenantContext, *, reason: Any, **fields: Any) -> dict[str, Any]:
    """Approve a new model of an existing provider, with its first price.
    Created ``disabled``: switching it on is a separate, audited decision."""
    from workeasy360.platform.audit import services as audit

    _require(ctx, MANAGE)
    why = _reason(reason)
    key = _slug(fields.get("key"), "key")
    model_id = _slug(fields.get("model_id"), "model_id")
    display_name = _text(fields.get("display_name"), "display_name", 80)
    tier = fields.get("tier", ModelTier.STANDARD.value)
    if tier not in {t.value for t in ModelTier}:
        raise AIInvalidRequest("tier must be standard or premium.", field="tier")
    capabilities = fields.get("capabilities")
    known = {c.value for c in Capability}
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(isinstance(c, str) for c in capabilities)
        or not set(capabilities) <= known
    ):
        raise AIInvalidRequest(
            "capabilities must list known capabilities.", field="capabilities"
        )
    context_window = _int(
        fields.get("context_window_tokens"), "context_window_tokens", 1, 10_000_000
    )
    max_output = _int(fields.get("max_output_tokens"), "max_output_tokens", 1, 1_000_000)
    dimensions = fields.get("embedding_dimensions")
    if Capability.EMBEDDINGS.value in capabilities:
        dimensions = _int(dimensions, "embedding_dimensions", 1, 16_000)
    elif dimensions is not None:
        raise AIInvalidRequest(
            "Only an embedding model has embedding_dimensions.",
            field="embedding_dimensions",
        )
    input_price = _int(
        fields.get("input_micros_per_mtok"), "input_micros_per_mtok", 0, 10**12
    )
    output_price = _int(
        fields.get("output_micros_per_mtok"), "output_micros_per_mtok", 0, 10**12
    )
    provider = AIProvider.objects.filter(key=str(fields.get("provider"))).first()
    if provider is None:
        raise AIInvalidRequest(
            "provider must be a registered provider.", field="provider"
        )
    adapter = adapter_for(provider.adapter)
    if adapter is None or (
        Capability.EMBEDDINGS.value in capabilities
        and "embedding" not in adapter.operations
    ):
        raise AIInvalidRequest(
            "That provider cannot serve those capabilities.", field="capabilities"
        )

    try:
        with transaction.atomic():
            row = AIModel.objects.create(
                key=key,
                provider=provider,
                model_id=model_id,
                display_name=display_name,
                status=RegistryStatus.DISABLED.value,
                tier=tier,
                capabilities=sorted(set(capabilities)),
                context_window_tokens=context_window,
                max_output_tokens=max_output,
                embedding_dimensions=dimensions,
                priority=_int(fields.get("priority", 100), "priority", 0, 1000),
            )
            AIModelPrice.objects.create(
                model=row,
                version=1,
                currency=settings.AI_ACCOUNTING_CURRENCY,
                input_micros_per_mtok=input_price,
                output_micros_per_mtok=output_price,
                effective_from=timezone.now(),
            )
            audit.record(
                ctx,
                "ai.model.created",
                resource_type="ai.model",
                resource_id=row.id,
                changes={"model": key, "provider": provider.key, "status": row.status},
                reason=why,
            )
    except IntegrityError:
        raise AIInvalidRequest(
            "A model with that key already exists.", field="key"
        ) from None
    return registry_view(ctx)


@in_scope
def publish_price(
    ctx: TenantContext,
    model_key: Any,
    *,
    input_micros_per_mtok: Any,
    output_micros_per_mtok: Any,
    reason: Any,
) -> dict[str, Any]:
    """A new, immutable price version, effective now. History keeps the old
    one: usage already recorded stays costed at the version it used."""
    from workeasy360.platform.audit import services as audit

    _require(ctx, MANAGE)
    why = _reason(reason)
    input_price = _int(input_micros_per_mtok, "input_micros_per_mtok", 0, 10**12)
    output_price = _int(output_micros_per_mtok, "output_micros_per_mtok", 0, 10**12)
    with transaction.atomic():
        row = AIModel.objects.select_for_update().filter(key=str(model_key)).first()
        if row is None:
            raise RegistryNotFound("No such model.")
        latest = (
            AIModelPrice.objects.filter(model=row).aggregate(v=Max("version"))["v"] or 0
        )
        price = AIModelPrice.objects.create(
            model=row,
            version=latest + 1,
            currency=settings.AI_ACCOUNTING_CURRENCY,
            input_micros_per_mtok=input_price,
            output_micros_per_mtok=output_price,
            effective_from=timezone.now(),
        )
        audit.record(
            ctx,
            "ai.price.published",
            resource_type="ai.model_price",
            resource_id=price.id,
            changes={
                "model": row.key,
                "version": price.version,
                "input_micros_per_mtok": input_price,
                "output_micros_per_mtok": output_price,
            },
            reason=why,
        )
    return registry_view(ctx)


# --- an organization's AI --------------------------------------------------------------


@in_scope
def organization_ai(ctx: TenantContext, organization_id: UUID) -> dict[str, Any]:
    """Policy, this month's spend and the latest executions of one customer -
    metadata only."""
    _require(ctx, VIEW)
    with organization_scope(ctx, organization_id) as customer:
        policy = policy_for(customer).as_dict()
        periods = list(
            AIBudgetPeriod.objects.filter(
                period_start=timezone.now().date().replace(day=1)
            )
            .order_by("scope")
            .values(
                "scope",
                "reserved_micros",
                "spent_micros",
                "requests",
                "input_tokens",
                "output_tokens",
                "embedding_tokens",
            )
        )
        recent = list(
            AIExecution.objects.order_by("-created_at").values(
                "id",
                "product_code",
                "operation",
                "skill_key",
                "skill_version",
                "status",
                "outcome",
                "model_key",
                "provider_key",
                "cost_micros",
                "fallback_used",
                "created_at",
            )[:25]
        )
    return {
        "organization_id": str(organization_id),
        "policy": policy,
        "currency": settings.AI_ACCOUNTING_CURRENCY,
        "periods": periods,
        "recent_executions": [
            {**e, "id": str(e["id"]), "created_at": e["created_at"].isoformat()}
            for e in recent
        ],
    }


@in_scope
def set_organization_policy(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    reason: Any,
    platform_suspended: Any = None,
    platform_cap_micros: Any = ...,
    premium_models_allowed: Any = None,
) -> dict[str, Any]:
    """The platform's side of an organization's AI policy. Validated before
    any write; audited in both logs."""
    from workeasy360.platform.audit import services as audit

    _require(ctx, MANAGE)
    why = _reason(reason)
    changes: dict[str, Any] = {}
    if platform_suspended is not None:
        if not isinstance(platform_suspended, bool):
            raise AIInvalidRequest(
                "platform_suspended must be a boolean.", field="platform_suspended"
            )
        changes["platform_suspended"] = platform_suspended
        changes["platform_suspension_reason"] = why if platform_suspended else ""
    if platform_cap_micros is not ...:
        if platform_cap_micros is not None:
            platform_cap_micros = _int(
                platform_cap_micros, "platform_cap_micros", 0, MAX_BUDGET_MICROS
            )
        changes["platform_cap_micros"] = platform_cap_micros
    if premium_models_allowed is not None:
        if not isinstance(premium_models_allowed, bool):
            raise AIInvalidRequest(
                "premium_models_allowed must be a boolean.",
                field="premium_models_allowed",
            )
        changes["premium_models_allowed"] = premium_models_allowed
    if not changes:
        raise AIInvalidRequest("Nothing to change.", field="platform_suspended")

    with organization_scope(ctx, organization_id) as customer, transaction.atomic():
        OrganizationAIPolicy.objects.bulk_create(
            [OrganizationAIPolicy(organization_id=organization_id)], ignore_conflicts=True
        )
        row = OrganizationAIPolicy.objects.select_for_update().get()
        before = {k: getattr(row, k) for k in changes}
        for k, v in changes.items():
            setattr(row, k, v)
        row.save()
        audit.record(
            customer,
            "ai.policy.changed_by_staff",
            resource_type="OrganizationAIPolicy",
            resource_id=row.id,
            changes={"before": before, "after": changes},
            reason=why,
        )
    audit.record(
        ctx,
        "ai.policy.changed_by_staff",
        resource_type="OrganizationAIPolicy",
        resource_id=organization_id,
        changes={"organization_id": str(organization_id), "after": changes},
        reason=why,
    )
    return organization_ai(ctx, organization_id)


# --- internals ------------------------------------------------------------------------


def _require(ctx: TenantContext, permission: str) -> None:
    authz.require(ctx, permission)
    authz.require_platform_staff(ctx, operation=f"ai.{permission}", permission=permission)


def _status(value: Any) -> str:
    if value not in {s.value for s in RegistryStatus}:
        raise AIInvalidRequest(
            "status must be active, deprecated or disabled.", field="status"
        )
    return str(value)


def _reason(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not MIN_REASON <= len(text) <= MAX_REASON:
        raise AIInvalidRequest(
            f"reason must be {MIN_REASON}-{MAX_REASON} characters: it is what an "
            "auditor reads.",
            field="reason",
        )
    return text


def _slug(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or not set(value) <= _KEY_CHARS
    ):
        raise AIInvalidRequest(
            f"{field} must be 1-64 lowercase letters, digits, '-', '.' or '_'.",
            field=field,
        )
    return value


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise AIInvalidRequest(f"{field} must be 1-{limit} characters.", field=field)
    return value.strip()


def _int(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AIInvalidRequest(
            f"{field} must be an integer from {low} to {high}.", field=field
        )
    return value
