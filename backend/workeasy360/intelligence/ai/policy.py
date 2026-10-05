"""Who may use AI, how much, and with which providers (Tasks 7, 10, 45, 88).

**The access decision** (``authorize``) is taken before anything is reserved,
retrieved or sent, in this order - each step's refusal is final:

1. the organization exists and is accepting new work (not suspended or
   read-only) - read live from the control plane, never from a snapshot;
2. the calling product is entitled to the organization, for writes - the
   same live decision the entitlement API and event ingestion use, so AI can
   never be more permissive than the product itself;
3. the organization's plan grants **this product** AI: the feature
   ``<product>.ai.monthly_budget_micros`` (a quota sold through the ordinary
   catalog, most generous source wins; ``-1`` = no product-level limit).
   Absent or zero means no AI - a product does not gain AI merely because it
   can reach the gateway;
4. the organization has not switched AI off, and the platform has not
   suspended its AI.

The result carries the limits the reservation enforces: the product's
monthly quota, and the organization's monthly cap - the lower of the
administrator's own budget and the platform cap, which applies even to an
"unlimited" plan, so no configuration means unbounded spend.

**Settings** (``update_settings``) are the administrator's: on/off, a monthly
budget at most the platform cap, the providers the organization permits, and
whether a request may fall back to another vendor. Changing them is
``platform.ai.manage`` (dangerous: it decides whether the organization's data
may be sent to AI providers at all), validated before any write, audited.
The platform's own fields on the same row are written only by
``intelligence.ai.staff``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import transaction

from workeasy360.contracts.errors import (
    AIDisabled,
    AIInvalidRequest,
    AINotEntitled,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.intelligence.ai.models import OrganizationAIPolicy
from workeasy360.platform.observability.metrics import metrics

#: The entitlement feature, per product: ``<product>.ai.monthly_budget_micros``.
BUDGET_FEATURE: Final = "ai.monthly_budget_micros"

VIEW: Final = "platform.ai.view"
MANAGE: Final = "platform.ai.manage"

#: The largest budget a request may express: 1,000,000.00 in micro-units.
MAX_BUDGET_MICROS: Final = 1_000_000 * 1_000_000


def budget_feature(product_code: str) -> str:
    return f"{product_code}.{BUDGET_FEATURE}"


@dataclass(frozen=True, slots=True)
class PolicyDTO:
    ai_enabled: bool
    monthly_budget_micros: int | None
    allowed_providers: tuple[str, ...]
    allow_provider_fallback: bool
    platform_suspended: bool
    platform_suspension_reason: str
    platform_cap_micros: int
    premium_models_allowed: bool
    revision: int

    @property
    def organization_cap_micros(self) -> int:
        if self.monthly_budget_micros is None:
            return self.platform_cap_micros
        return min(self.monthly_budget_micros, self.platform_cap_micros)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ai_enabled": self.ai_enabled,
            "monthly_budget_micros": self.monthly_budget_micros,
            "allowed_providers": list(self.allowed_providers),
            "allow_provider_fallback": self.allow_provider_fallback,
            "platform_suspended": self.platform_suspended,
            "platform_cap_micros": self.platform_cap_micros,
            "organization_cap_micros": self.organization_cap_micros,
            "premium_models_allowed": self.premium_models_allowed,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """Everything a reservation and a model selection need, decided once."""

    organization_id: UUID
    product_code: str
    #: The context work for this organization runs in: nil actor, live status.
    ctx: TenantContext
    #: ``None`` = the plan sets no product-level limit.
    product_limit_micros: int | None
    organization_limit_micros: int
    allowed_providers: frozenset[str]
    allow_provider_fallback: bool
    premium_allowed: bool


def authorize(organization_id: UUID, product_code: str) -> AccessGrant:
    """The AI access decision for a product acting for an organization.

    Raises ``AINotEntitled`` / ``AIDisabled``, or lets
    ``VerificationUnavailable`` through - a platform failure reading the
    entitlement is a 503, never a denial.
    """
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.ecosystem import verification
    from workeasy360.platform.entitlements.selectors import feature_for_organization

    ctx = routing_context(organization_id)
    if ctx is None:
        _deny("unknown_organization")
        raise AINotEntitled("This product may not use AI for that organization.")

    decision = verification.verify(
        organization_id=organization_id, product_code=product_code, write=True
    )
    # A read-only organization (it or its subscription suspended) is entitled
    # but takes no new work - verify answers a write check "suspended".
    suspended = decision.decision != verification.ALLOW and decision.reason == "suspended"
    if decision.decision != verification.ALLOW and not suspended:
        _deny("product_not_entitled")
        raise AINotEntitled("This product may not use AI for that organization.")
    if suspended or not decision.write_allowed or not ctx.is_write_allowed:
        _deny("organization_inactive")
        raise AIDisabled("The organization is not accepting new AI work.")

    quota = feature_for_organization(organization_id, budget_feature(product_code))
    product_limit = _product_limit(quota)
    if product_limit == 0:
        _deny("no_ai_entitlement")
        raise AINotEntitled("This product may not use AI for that organization.")

    policy = policy_for(ctx)
    if policy.platform_suspended:
        _deny("platform_suspended")
        raise AIDisabled("AI is suspended for this organization.")
    if not policy.ai_enabled:
        _deny("organization_disabled")
        raise AIDisabled("The organization has turned AI off.")

    return AccessGrant(
        organization_id=organization_id,
        product_code=product_code,
        ctx=ctx,
        product_limit_micros=product_limit,
        organization_limit_micros=policy.organization_cap_micros,
        allowed_providers=frozenset(policy.allowed_providers),
        allow_provider_fallback=policy.allow_provider_fallback,
        premium_allowed=policy.premium_models_allowed,
    )


def policy_for(ctx: TenantContext) -> PolicyDTO:
    """The organization's policy, or the defaults if it has never set one."""
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with tenant_context(ctx), organization_db_scope(ctx.organization_id):
        row = OrganizationAIPolicy.objects.first()
    return _dto(row)


def settings_for(ctx: TenantContext) -> dict[str, Any]:
    """The administrator's view: the policy plus the approved providers it may
    choose among (``platform.ai.view``)."""
    from workeasy360.intelligence.ai import registry
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, VIEW)
    row = OrganizationAIPolicy.objects.first()
    return {
        "policy": _dto(row).as_dict(),
        "providers": [p for p in registry.providers() if p["status"] == "active"],
    }


def update_settings(
    ctx: TenantContext,
    *,
    expected_revision: Any,
    ai_enabled: Any = None,
    monthly_budget_micros: Any = ...,
    allowed_providers: Any = None,
    allow_provider_fallback: Any = None,
) -> dict[str, Any]:
    """Change the organization's own AI settings. Validates every field
    before the first write (D-016); a stale revision is a conflict."""
    from workeasy360.contracts.errors import AIRequestConflict
    from workeasy360.intelligence.ai import registry
    from workeasy360.platform.audit import services as audit
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, MANAGE)

    changes: dict[str, Any] = {}
    if ai_enabled is not None:
        if not isinstance(ai_enabled, bool):
            raise AIInvalidRequest(
                "ai_enabled must be true or false.", field="ai_enabled"
            )
        changes["ai_enabled"] = ai_enabled
    if monthly_budget_micros is not ...:
        if monthly_budget_micros is not None and (
            isinstance(monthly_budget_micros, bool)
            or not isinstance(monthly_budget_micros, int)
            or not 0 <= monthly_budget_micros <= MAX_BUDGET_MICROS
        ):
            raise AIInvalidRequest(
                "monthly_budget_micros must be null or a non-negative integer.",
                field="monthly_budget_micros",
            )
        changes["monthly_budget_micros"] = monthly_budget_micros
    if allowed_providers is not None:
        known = {p["key"] for p in registry.providers()}
        if (
            not isinstance(allowed_providers, list)
            or not all(isinstance(p, str) for p in allowed_providers)
            or not set(allowed_providers) <= known
        ):
            raise AIInvalidRequest(
                "allowed_providers must list approved provider keys.",
                field="allowed_providers",
            )
        changes["allowed_providers"] = sorted(set(allowed_providers))
    if allow_provider_fallback is not None:
        if not isinstance(allow_provider_fallback, bool):
            raise AIInvalidRequest(
                "allow_provider_fallback must be true or false.",
                field="allow_provider_fallback",
            )
        changes["allow_provider_fallback"] = allow_provider_fallback
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise AIInvalidRequest("revision is required.", field="revision")

    with transaction.atomic():
        row = _locked_row(ctx)
        if row.revision != expected_revision:
            raise AIRequestConflict("The settings changed since they were read. Reload.")
        before = {key: getattr(row, key) for key in changes}
        for key, value in changes.items():
            setattr(row, key, value)
        row.revision += 1
        row.save()
        audit.record(
            ctx,
            "ai.settings.updated",
            resource_type="OrganizationAIPolicy",
            resource_id=row.id,
            changes={"before": before, "after": changes},
        )
    return _dto(row).as_dict()


def _locked_row(ctx: TenantContext) -> OrganizationAIPolicy:
    """The organization's policy row, created on first write and locked."""
    OrganizationAIPolicy.objects.bulk_create(
        [OrganizationAIPolicy(organization_id=ctx.organization_id)],
        ignore_conflicts=True,
    )
    return OrganizationAIPolicy.objects.select_for_update().get()


def _product_limit(quota: Any) -> int | None:
    from workeasy360.contracts.ports.entitlements import UNLIMITED

    if isinstance(quota, bool) or not isinstance(quota, int):
        return 0
    if quota == UNLIMITED:
        return None
    return max(quota, 0)


def _dto(row: OrganizationAIPolicy | None) -> PolicyDTO:
    default_cap = int(settings.AI_DEFAULT_ORG_MONTHLY_CAP_MICROS)
    if row is None:
        return PolicyDTO(
            ai_enabled=True,
            monthly_budget_micros=None,
            allowed_providers=(),
            allow_provider_fallback=False,
            platform_suspended=False,
            platform_suspension_reason="",
            platform_cap_micros=default_cap,
            premium_models_allowed=False,
            # The revision a row gets when first written, so an administrator
            # editing defaults sends the value the row will be created with.
            revision=1,
        )
    return PolicyDTO(
        ai_enabled=row.ai_enabled,
        monthly_budget_micros=row.monthly_budget_micros,
        allowed_providers=tuple(row.allowed_providers or ()),
        allow_provider_fallback=row.allow_provider_fallback,
        platform_suspended=row.platform_suspended,
        platform_suspension_reason=row.platform_suspension_reason,
        platform_cap_micros=(
            row.platform_cap_micros
            if row.platform_cap_micros is not None
            else default_cap
        ),
        premium_models_allowed=row.premium_models_allowed,
        revision=row.revision,
    )


def _deny(reason: str) -> None:
    metrics.increment("ai_access_denied_total", reason=reason)
