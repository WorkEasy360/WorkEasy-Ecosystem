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

import functools
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from workeasy360.contracts.enums import PrincipalKind
from workeasy360.contracts.errors import PermissionDenied
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


#: How a non-member principal's permission set is resolved. STAFF is answered
#: by this module's own grants; PARTNER is registered by ``commercial/partners``
#: at app-ready, because ``authz`` (P2) cannot import the commercial layer that
#: knows what a partner's capabilities are. The same inversion tenancy uses for
#: membership resolution (``register_resolver``), for the same reason.
PrincipalPermissionResolver = Callable[[TenantContext], frozenset[str]]
_principal_resolvers: dict[PrincipalKind, PrincipalPermissionResolver] = {}


def register_principal_resolver(
    kind: PrincipalKind, resolver: PrincipalPermissionResolver
) -> None:
    """Register how ``kind``'s permissions are computed. Idempotent."""
    _principal_resolvers[kind] = resolver


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


def is_platform_staff(ctx: TenantContext, permission: str | None = None) -> bool:
    """Whether the acting identity holds a live control-plane grant for
    ``permission`` (or, with no permission named, any live grant at all).

    A **separate axis** from roles and permissions, deliberately. Role
    assignments are tenant-owned data that a customer administrator controls;
    staff grants are control-plane data that they do not. Control-plane
    configuration - the product catalog, published prices, product
    integrations - must be gated on something a tenant cannot grant itself,
    or a compromised customer admin becomes a platform admin.

    **Django's ``is_staff`` is not that thing** (Phase 7 final audit). It is a
    precondition ``staff_permissions`` checks, never an authority: before this
    was tightened, ``is_staff`` plus ownership of *any* organization - whose
    owner role carries every non-control-plane permission - unlocked the
    global catalog, price books and integration registration through the
    organization-scoped routes, with no ``StaffGrant`` and no way to revoke it
    short of editing the account. The answer is now read from the same live,
    uncached grants the Super Admin uses, so revoking a grant ends every route
    to the power on the next request.

    Staff status grants **no tenant data access** on its own
    (04-identity-authz.md section 5); it only unlocks control-plane writes
    when paired with the matching permission.
    """
    from workeasy360.platform.authz.staff import staff_permissions

    held = staff_permissions(ctx.user_id)
    return bool(held) if permission is None else str(permission) in held


def require_platform_staff(
    ctx: TenantContext, *, operation: str, permission: str | PermissionKey
) -> None:
    """Raise ``PermissionDenied`` unless the actor holds a live control-plane
    grant that confers ``permission``.

    ``permission`` is the ``commercial.*``/``platform.integration.*`` key the
    service has just checked through ``require``: a caller must hold it
    *through a StaffGrant* (``platform.staff_catalog.manage`` implies
    ``commercial.catalog.manage``, and so on), not merely through a customer
    role. Denials are logged as a security event: a customer principal
    reaching a control-plane write is either a bug in the calling service or
    an attempt, and both need to be visible.
    """
    if is_platform_staff(ctx, str(permission)):
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

    **Control-plane principals are never cached.** A staff grant, a partner
    capability or a partner membership can be revoked at any moment, and the
    requirement is that the very next check fails - which a cached set,
    however briefly, cannot promise (Phase 7 Task 61).
    """
    from django.core.cache import cache

    if ctx.principal is PrincipalKind.STAFF:
        from workeasy360.platform.authz.staff import staff_permissions

        return staff_permissions(ctx.user_id)
    if ctx.principal is PrincipalKind.PARTNER:
        resolver = _principal_resolvers.get(PrincipalKind.PARTNER)
        # No resolver registered means no partner module is installed: deny.
        return resolver(ctx) if resolver is not None else frozenset()

    version = _permission_set_version(ctx)
    cache_key = (
        f"w360:{ctx.organization_id}:perms:{ctx.membership_id}"
        f":v{version}:{_catalog_generation()}"
    )

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

    # A role never carries a control-plane permission, whatever a row says:
    # those are held only through StaffGrant rows and partner capabilities.
    # The owner role's "*" already skips them when it is seeded; this makes
    # the rule hold for a hand-built or stale role row too.
    return frozenset(
        key
        for key in permission_registry.expand(granted)
        if not (
            permission_registry.exists(key) and permission_registry.get(key).control_plane
        )
    )


def models_q_not_expired(now: Any) -> Any:
    """``expires_at IS NULL OR expires_at > now``.

    A helper rather than an inline Q so the expiry semantics are defined in
    one place - an assignment that has expired must not grant anything, and
    getting this backwards would silently extend every temporary grant.
    """
    from django.db.models import Q

    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)


@functools.cache
def _catalog_generation() -> str:
    """A fingerprint of what the system roles grant, as this code defines it.

    The per-organization version above catches every runtime role or
    assignment change. It cannot catch a *deploy* that changes what a system
    role grants: those grants are written by a migration or by the
    ``post_migrate`` sync (``ensure_system_role_catalog``), for every
    organization at once, and a set cached just before the deploy would read
    the new pages as 403 for up to its TTL. Putting the catalog's fingerprint
    in the key means processes running new code never read a set computed
    under the old catalog. Computed once per process, from code: no query.
    """
    catalog = sorted(
        (
            key,
            spec["permissions"]
            if spec["permissions"] == "*"
            else sorted(spec["permissions"]),
        )
        for key, spec in SYSTEM_ROLES.items()
    )
    material = repr((catalog, sorted(permission_registry.all_keys())))
    return hashlib.sha256(material.encode()).hexdigest()[:12]


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


def ensure_system_role_catalog() -> None:
    """Idempotently sync the permission registry and the system role catalog
    into the database (Phase 5, Task 30-31).

    **Not called by the migration.** ``authz.migrations.0002_seed_system_roles``
    seeds the same catalog with its own copy of this logic on historical
    models (it does still read the live ``SYSTEM_ROLES`` and permission
    registry, so renaming either breaks a fresh install - recorded in
    docs/phase-5/opus-audit.md). This function is the ``post_migrate``
    receiver (``authz.apps.AuthzConfig.ready()``), for the same reason
    Django's own
    ``create_permissions``/``create_contenttypes`` are: ``post_migrate``
    fires after **every** ``manage.py migrate`` (so a later deploy that adds
    a new permission gets it added to ``owner`` without a new migration) and
    after **every** ``manage.py flush`` - which is exactly what
    ``pytest.mark.django_db(transaction=True)``'s ``TransactionTestCase``-
    style teardown runs between tests. Without the second registration, a
    ``transaction=True`` test genuinely truncates ``authz_role`` along with
    everything else (TRUNCATE is not RLS-scoped, see conftest.py's own
    comment on the test-only TRUNCATE grant), and a migration-only seed -
    tracked as "already applied" in ``django_migrations``, which ``flush``
    does not touch - never runs again to restore it.

    **Bracketed by the same NO FORCE/FORCE toggle the migration documents.**
    Safe here for the identical reason: both callers only ever run on the
    migrator/owner connection (a `manage.py migrate`/`flush` invocation, or
    - in tests - the same connection ``conftest.django_db_setup`` has not
    yet rebound to the non-owner app role at the point ``post_migrate``
    fires), never on ``w360_app``.
    """
    from django.db import DatabaseError, connection

    from workeasy360.platform.authz.models import Permission, Role, RolePermission
    from workeasy360.platform.authz.registry import permission_registry

    all_keys: list[str] = []
    for declaration in permission_registry.all():
        key = str(declaration.key)
        if not declaration.control_plane:
            # "*" means every permission a *customer* role may carry.
            # Control-plane permissions are mirrored into the table (so a
            # foreign key could reference them) but never granted to a role.
            all_keys.append(key)
        Permission.objects.update_or_create(
            key=key,
            defaults={
                "label": declaration.label,
                "product": declaration.product,
                "category": declaration.category,
                "dangerous": declaration.dangerous,
                "implies": list(declaration.implies),
            },
        )

    try:
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE authz_role NO FORCE ROW LEVEL SECURITY;")
    except DatabaseError:
        # The current connection cannot ALTER TABLE - it is not the table
        # owner (``w360_app``, not ``w360_migrator``). Reachable when
        # ``post_migrate`` fires from a test's `flush`-based teardown after
        # `conftest.django_db_setup` has already rebound the connection to
        # the app role: role rows genuinely cannot be restored from there
        # (D-001 is doing exactly its job), so this is a silent no-op rather
        # than a teardown failure for an unrelated test. The migration and
        # any `manage.py migrate` run as the owner and are unaffected.
        logger.warning(
            "authz.system_role_reseed.skipped_insufficient_privilege",
        )
        return
    try:
        for role_key, spec in SYSTEM_ROLES.items():
            role = Role.objects.filter(key=role_key, organization__isnull=True).first()
            if role is None:
                import uuid as _uuid

                role = Role.objects.create(
                    id=_uuid.uuid4(),
                    key=role_key,
                    organization=None,
                    name=spec["name"],
                    description=spec["description"],
                    product="",
                    is_system=True,
                    is_assignable=True,
                )
            else:
                Role.objects.filter(pk=role.pk).update(
                    name=spec["name"],
                    description=spec["description"],
                    is_system=True,
                    is_assignable=True,
                )

            granted = (
                all_keys if spec["permissions"] == "*" else list(spec["permissions"])
            )
            existing = set(
                RolePermission.objects.filter(role=role).values_list(
                    "permission_id", flat=True
                )
            )
            for permission_key in granted:
                if permission_key in existing:
                    continue
                import uuid as _uuid

                RolePermission.objects.create(
                    id=_uuid.uuid4(), role=role, permission_id=permission_key
                )
    finally:
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE authz_role FORCE ROW LEVEL SECURITY;")


def assign_system_role(
    *, organization_id: Any, membership_id: Any, role_key: str
) -> None:
    """Bind a membership to a global system role (Phase 5, Task 4/30-31).

    The RBAC bootstrap step of organization creation, and the reason it is
    here rather than inline in ``platform.onboarding``: F-6 forbids a caller
    reading ``Role``/``RoleAssignment`` directly across a module boundary,
    even where the layer import is legal - a selector/service is the
    contract, the model is this module's own implementation detail.

    Runs inside the caller's transaction and tenant scope (this module does
    not open ``unscoped_access`` or ``organization_db_scope`` itself - the
    caller, already doing so for the membership it is attaching this role
    to, is expected to still be inside both).
    """
    from workeasy360.platform.authz.models import Role, RoleAssignment

    role = Role.objects.filter(
        key=role_key, organization__isnull=True, is_system=True
    ).first()
    if role is None:  # pragma: no cover - deploy-time invariant
        raise RuntimeError(
            f"System role {role_key!r} is not seeded. Run migrations "
            f"(authz.0002_seed_system_roles) before assigning it."
        )
    RoleAssignment(
        organization_id=organization_id, membership_id=membership_id, role=role
    ).save()


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
            "platform.member.sign_out",
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
            # Phase 6: the organization's own integrations and automations
            # (declared in integration/). Administrators run these; billing,
            # member and read-only roles do not. ``manage`` is dangerous, so
            # it is refused under impersonation.
            "platform.connection.view",
            "platform.connection.manage",
            "platform.webhook.replay",
            "platform.automation.view",
            "platform.automation.manage",
            # Phase 8: the organization's AI settings and usage, and the
            # analytics shell (declared in intelligence/). ``ai.manage``
            # decides whether the organization's data may be sent to AI
            # providers at all, so it is dangerous.
            "platform.ai.view",
            "platform.ai.manage",
            "platform.analytics.view",
        ],
    },
    "billing_manager": {
        "name": "Billing Manager",
        "description": "Billing and subscriptions only. Separable from admin on purpose.",
        "permissions": [
            "platform.organization.view",
            "platform.billing.view",
            "platform.billing.manage",
            # Phase 8 extension: the subscription the console's billing page
            # shows beside the invoices this role pays. View only.
            "commercial.subscription.view",
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


# --- Phase 8 extension: the member console ----------------------------------

#: System roles a member may be given through the console. ``owner`` is never
#: assignable - ownership moves by transfer - and ``collaborator`` is the
#: resource-scoped guest role, which the console does not hand out.
ASSIGNABLE_SYSTEM_ROLES: Final = ("admin", "billing_manager", "member", "readonly")

#: Roles whose holder can change who else holds what. Giving one away - by
#: assignment *or by invitation* - needs ``platform.role.assign`` (dangerous),
#: or ``platform.member.invite`` alone would be a privilege-escalation path.
PRIVILEGED_SYSTEM_ROLES: Final = ("admin", "billing_manager")


def system_role_keys(membership_ids: Any) -> dict[str, list[str]]:
    """``{membership_id: [role_key, ...]}`` for live system-role assignments,
    in the caller's tenant scope (RLS confines it to one organization)."""
    from django.utils import timezone

    from workeasy360.platform.authz.models import RoleAssignment

    ids = [str(i) for i in membership_ids]
    if not ids:
        return {}
    found: dict[str, list[str]] = {i: [] for i in ids}
    rows = (
        RoleAssignment.objects.filter(membership_id__in=ids, role__is_system=True)
        .filter(models_q_not_expired(timezone.now()))
        .values_list("membership_id", "role__key")
    )
    for membership_id, key in rows:
        found.setdefault(str(membership_id), []).append(key)
    return {k: sorted(v) for k, v in found.items()}


def replace_system_role(
    *, organization_id: Any, membership_id: Any, role_key: str
) -> list[str]:
    """Make ``role_key`` the membership's only system role. Returns the keys
    it held before. Custom-role assignments are left alone. Invalidates the
    organization's cached permission sets (the next request sees the change).

    Runs in the caller's transaction and tenant scope; the caller authorizes.
    """
    from workeasy360.platform.authz.models import Role, RoleAssignment

    if role_key not in ASSIGNABLE_SYSTEM_ROLES:
        raise ValueError(f"{role_key!r} is not an assignable system role.")
    role = Role.objects.filter(
        key=role_key, organization__isnull=True, is_system=True
    ).first()
    if role is None:  # pragma: no cover - deploy-time invariant
        raise RuntimeError(f"System role {role_key!r} is not seeded.")
    existing = RoleAssignment.objects.filter(
        membership_id=membership_id, role__is_system=True
    )
    previous = sorted(existing.values_list("role__key", flat=True))
    existing.delete()
    RoleAssignment(
        organization_id=organization_id, membership_id=membership_id, role=role
    ).save()
    bump_permission_version(organization_id)
    return previous
