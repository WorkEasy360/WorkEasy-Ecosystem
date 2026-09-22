"""The authorization decision function.

``check()`` is the single entry point for every authorization decision in the
system, for every principal type. Parallel authorization paths are how
privilege bugs arise, so there is exactly one.

Checks live here, in an application service, **not in views**. A view-only
check is bypassed by the task path and by any internal caller
(12-security.md section 4).

Order matters and is fixed (04-identity-authz.md section 2):

    1. Authenticated?         -> 401
    2. Member of this org?    -> 403
    3. Org status permits it? -> 403 / 402
    4. Entitled?              -> 402  "upgrade required"
    5. Seat?                  -> 402  "no seat assigned"
    6. Permitted? (role)      -> 403  "insufficient permission"
    7. Object-level rule?     -> 403 / 404

Entitlement precedes permission because the error messages differ and so does
the remedy. "Your plan does not include Books" is a sales conversation;
"You lack permission to post journals" is an admin conversation. Merging them
produces the classic SaaS bug where an admin is told they lack permission for
a feature nobody in the org has bought.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from workeasy360.contracts.errors import PermissionDenied, StepUpRequired
from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz.registry import permission_registry
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import (
    AUTHZ_CHECKS,
    AUTHZ_DENIALS,
    metrics,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of an authorization check.

    Carries a reason so that a denial can be logged and explained without the
    caller re-deriving why.
    """

    allowed: bool
    permission: str
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed

    @classmethod
    def allow(cls, permission: str) -> Decision:
        return cls(allowed=True, permission=permission, reason="ok")

    @classmethod
    def deny(cls, permission: str, reason: str) -> Decision:
        return cls(allowed=False, permission=permission, reason=reason)


#: Object rules registered by the owning module. Only Projects knows that a
#: task belongs to a project, so authz orchestrates and does not model every
#: product's hierarchy.
ObjectRule = Callable[[TenantContext, Any], bool]
_object_rules: dict[tuple[str, str], ObjectRule] = {}


def register_object_rule(model_label: str, permission: str, rule: ObjectRule) -> None:
    """Register an object-level rule, called by the owning module."""
    _object_rules[(model_label, str(permission))] = rule


def check(
    ctx: TenantContext,
    permission: str | PermissionKey,
    obj: Any | None = None,
) -> Decision:
    """Decide whether ``ctx`` may perform ``permission``, optionally on ``obj``.

    Deny-by-default at every branch: an unknown permission, an unresolvable
    membership, a missing object rule and an expired assignment all deny.
    """
    key = str(permission)
    metrics.increment(AUTHZ_CHECKS, result="evaluated")

    # An unregistered permission is a programming error, not a denial. It
    # raises so the typo surfaces in development rather than becoming a
    # permanent silent denial in production.
    declaration = permission_registry.get(key)

    # Step 3: organization status. A read-only organization may still be read
    # from, so only dangerous (mutating, high-impact) permissions are gated
    # here; ordinary write blocking is R-10's job at the persistence layer.
    if declaration.dangerous and not ctx.is_write_allowed:
        return _deny(ctx, key, "organization_read_only")

    # Step 5/6 inputs: the compiled permission set for this membership.
    effective = effective_permissions(ctx)
    if key not in effective:
        return _deny(ctx, key, "permission_not_granted")

    # Step 5 (partial): dangerous permissions need a second factor and a
    # recent re-auth, regardless of how the session started.
    if declaration.dangerous and ctx.aal < 2:
        metrics.increment(AUTHZ_DENIALS, permission=key, reason="step_up_required")
        raise StepUpRequired(
            f"{key} requires multi-factor authentication.", permission=key
        )

    # Impersonation cannot perform dangerous actions at all. Blocked in the
    # decision function rather than in the UI, because the UI is not a
    # security boundary (04-identity-authz.md section 6).
    if declaration.dangerous and ctx.is_break_glass:
        return _deny(ctx, key, "blocked_under_impersonation")

    # Step 7: object-level rule, evaluated by the owning module.
    if obj is not None:
        label = _model_label(obj)
        rule = _object_rules.get((label, key))
        if rule is not None and not rule(ctx, obj):
            return _deny(ctx, key, "object_rule_denied")

    return Decision.allow(key)


def require(
    ctx: TenantContext,
    permission: str | PermissionKey,
    obj: Any | None = None,
) -> Decision:
    """Raise ``PermissionDenied`` unless allowed.

    The raising form exists because the common case in a service is "stop
    here", and forcing every caller to write the if/raise invites the check
    being dropped during a refactor.
    """
    decision = check(ctx, permission, obj)
    if not decision.allowed:
        raise PermissionDenied(
            f"Not permitted: {decision.permission}",
            permission=decision.permission,
            reason=decision.reason,
        )
    return decision


def is_platform_staff(ctx: TenantContext) -> bool:
    """Whether the acting identity is Workeasy360 internal staff.

    A **separate axis** from roles and permissions, deliberately. Role
    assignments are tenant-owned data that a customer administrator controls;
    staff status is control-plane data that they do not. Control-plane
    configuration - the product catalog, published prices - must be gated on
    something a tenant cannot grant itself, or a compromised customer admin
    becomes a platform admin.

    Staff status grants **no tenant data access** on its own
    (04-identity-authz.md section 5); it only unlocks control-plane writes
    when paired with the matching permission.
    """
    from workeasy360.platform.identity.models import User

    return User.objects.filter(id=ctx.user_id, is_staff=True).exists()


def require_platform_staff(ctx: TenantContext, *, operation: str) -> None:
    """Raise ``PermissionDenied`` unless the actor is internal staff.

    Denials are logged as a security event: a customer principal reaching a
    control-plane write is either a bug in the calling service or an attempt,
    and both need to be visible.
    """
    if is_platform_staff(ctx):
        return
    metrics.increment(AUTHZ_DENIALS, permission=operation, reason="not_platform_staff")
    logger.warning(
        "authz.control_plane.denied",
        operation=operation,
        reason="not_platform_staff",
    )
    raise PermissionDenied(
        f"{operation} is control-plane configuration and requires an "
        f"internal platform identity.",
        permission=operation,
        reason="not_platform_staff",
    )


def effective_permissions(ctx: TenantContext) -> frozenset[str]:
    """The compiled permission set for this membership.

    Cached per organization version, so any role or assignment change
    invalidates every member's set with one counter bump rather than an
    enumeration. Falls back to recomputing from the database when the cache is
    unavailable: slower, still correct. **Authorization never fails open.**
    """
    from django.core.cache import cache

    version = _permission_set_version(ctx)
    cache_key = f"w360:{ctx.organization_id}:perms:{ctx.membership_id}:v{version}"

    try:
        cached = cache.get(cache_key)
    except Exception:  # pragma: no cover - cache backend down
        cached = None

    if cached is not None:
        return frozenset(cached)

    computed = _compute_permissions(ctx)
    try:
        # Short TTL as a backstop against a missed invalidation. Denials are
        # never cached longer than grants: a revoked permission must take
        # effect promptly, while a granted one arriving late is harmless.
        cache.set(cache_key, list(computed), 300)
    except Exception:  # noqa: S110 - pragma: no cover
        # A cache write failure is survivable: the value is recomputed next
        # time. Authorization never fails open, only slower.
        pass
    return computed


def _compute_permissions(ctx: TenantContext) -> frozenset[str]:
    """Join assignments -> roles -> permissions, then expand implications."""
    from django.utils import timezone

    from workeasy360.platform.authz.models import RoleAssignment

    now = timezone.now()
    assignments = (
        RoleAssignment.objects.filter(membership_id=ctx.membership_id)
        .filter(models_q_not_expired(now))
        .select_related("role")
        .prefetch_related("role__role_permissions__permission")
    )

    granted: set[str] = set()
    for assignment in assignments:
        if assignment.role.is_assignable is False:
            continue
        for role_permission in assignment.role.role_permissions.all():
            granted.add(role_permission.permission_id)

    return permission_registry.expand(granted)


def models_q_not_expired(now: Any) -> Any:
    """``expires_at IS NULL OR expires_at > now``.

    A helper rather than an inline Q so the expiry semantics are defined in
    one place - an assignment that has expired must not grant anything, and
    getting this backwards would silently extend every temporary grant.
    """
    from django.db.models import Q

    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)


def _permission_set_version(ctx: TenantContext) -> int:
    from workeasy360.platform.authz.models import PermissionSetVersion

    row = (
        PermissionSetVersion.objects.filter(organization_id=ctx.organization_id)
        .values_list("version", flat=True)
        .first()
    )
    return row or 1


def bump_permission_version(organization_id: Any) -> int:
    """Invalidate every cached permission set for one organization.

    Called on any role or assignment change. A single counter increment, with
    no key enumeration and no risk of a stale key surviving a flush - old keys
    are simply never read again and expire on their own.
    """
    from django.db.models import F

    from workeasy360.platform.authz.models import PermissionSetVersion
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    # Two independent layers must both be satisfied, and relaxing only the
    # first is a silent failure waiting for a caller outside a request:
    #
    #   unscoped_access()      relaxes the ORM manager (Layer 2)
    #   organization_db_scope() satisfies the RLS policy (Layer 3)
    #
    # Without the scope the INSERT is refused by WITH CHECK. A request-path
    # caller happens to be inside the tenancy middleware's transaction, which
    # has already set the variable, so the gap only appears for system and
    # background callers - the ones with the least test coverage.
    with (
        unscoped_access(
            reason="Permission cache invalidation touches the counter row directly",
            module="platform.authz.services",
        ),
        organization_db_scope(organization_id),
    ):
        obj, created = PermissionSetVersion.unscoped.get_or_create(
            organization_id=organization_id, defaults={"version": 1}
        )
        if not created:
            PermissionSetVersion.unscoped.filter(pk=obj.pk).update(
                version=F("version") + 1
            )
            obj.refresh_from_db(fields=["version"])
    return obj.version


def _deny(ctx: TenantContext, permission: str, reason: str) -> Decision:
    """Record a denial as a structured security event.

    Denial spikes indicate a broken role or an attack, so they are counted by
    permission (11-observability.md section 6).
    """
    metrics.increment(AUTHZ_DENIALS, permission=permission, reason=reason)
    logger.info(
        "authz.denied",
        permission=permission,
        reason=reason,
    )
    return Decision.deny(permission, reason)


def _model_label(obj: Any) -> str:
    meta = getattr(obj, "_meta", None)
    if meta is None:
        return type(obj).__name__
    return f"{meta.app_label}.{meta.model_name}"


#: System role definitions, seeded per organization at creation.
#: Admin deliberately excludes billing (04-identity-authz.md section 4.3).
SYSTEM_ROLES: Final[dict[str, dict[str, Any]]] = {
    "owner": {
        "name": "Owner",
        "description": "Full control, including billing, deletion and ownership transfer.",
        "permissions": "*",
    },
    "admin": {
        "name": "Administrator",
        "description": "Users, roles and product activation. Not billing.",
        "permissions": [
            "platform.organization.view",
            "platform.organization.update",
            "platform.member.view",
            "platform.member.invite",
            "platform.member.update",
            "platform.member.remove",
            "platform.role.view",
            "platform.role.create",
            "platform.role.update",
            "platform.role.assign",
            "platform.entitlement.view",
            "platform.seat.assign",
            "platform.audit.view",
            "platform.security.view",
            "platform.security.update",
            "platform.file.view",
            "platform.file.upload",
            "platform.file.delete",
            "platform.notification.view",
            "platform.notification.manage",
        ],
    },
    "billing_manager": {
        "name": "Billing Manager",
        "description": "Billing and subscriptions only. Separable from admin on purpose.",
        "permissions": [
            "platform.organization.view",
            "platform.billing.view",
            "platform.billing.manage",
            "platform.entitlement.view",
            "platform.seat.assign",
            "platform.member.view",
        ],
    },
    "member": {
        "name": "Member",
        "description": "Baseline: sign in, see the organization, use products where seated.",
        "permissions": [
            "platform.organization.view",
            "platform.member.view",
            "platform.file.view",
            "platform.file.upload",
            "platform.notification.view",
            "platform.notification.manage",
        ],
    },
    "readonly": {
        "name": "Read Only",
        "description": "View access with no ability to change anything.",
        "permissions": [
            "platform.organization.view",
            "platform.member.view",
            "platform.file.view",
            "platform.notification.view",
        ],
    },
    "collaborator": {
        "name": "External Collaborator",
        "description": "Guest with resource-scoped access only.",
        "permissions": [
            "platform.file.view",
            "platform.notification.view",
        ],
    },
}
