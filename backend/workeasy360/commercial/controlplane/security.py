"""Who holds control-plane permissions: grant, revoke, list (Task 2, 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction

from workeasy360.contracts.errors import ControlPlaneInvariantViolation
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz import staff
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.identity.selectors import active_user_id_for_email
from workeasy360.platform.observability.metrics import metrics

MANAGE = "platform.staff_security.manage"
VIEW = "platform.staff_security.view"


@in_scope
def grant_permission(
    ctx: TenantContext,
    *,
    email: Any,
    permission: Any,
    reason: Any,
    expires_at: datetime | None = None,
) -> Any:
    """Give a staff member one control-plane permission, optionally time-boxed.

    Dangerous. The target is named by address and must be an active
    staff account; anything else is one uniform refusal. A person cannot grant
    to themselves, and the permission must be a registered control-plane key.
    """
    authz.require(ctx, MANAGE)
    user_id = active_user_id_for_email(str(email or ""))
    if user_id is None:
        raise ControlPlaneInvariantViolation(
            "That identity cannot hold control-plane permissions.", field="email"
        )
    with transaction.atomic():
        grant, created = staff.create_grant(
            user_id=user_id,
            permission=str(permission or ""),
            granted_by_id=ctx.user_id,
            reason=str(reason or ""),
            expires_at=expires_at,
        )
        if created:
            audit.record(
                ctx,
                "staff.permission_granted",
                resource_type="authz.staff_grant",
                resource_id=grant.id,
                changes={
                    "permission": grant.permission,
                    "grantee": str(user_id),
                    "expires_at": (
                        grant.expires_at.isoformat() if grant.expires_at else None
                    ),
                },
                reason=grant.reason,
            )
    metrics.increment("staff_permission_changes_total", operation="grant")
    return grant


@in_scope
def revoke_permission(ctx: TenantContext, *, grant_id: UUID, reason: Any) -> Any:
    """End a grant. Effective on the holder's very next request; the last
    security administrator cannot be removed."""
    authz.require(ctx, MANAGE)
    with transaction.atomic():
        grant, changed = staff.end_grant(
            grant_id=grant_id, revoked_by_id=ctx.user_id, reason=str(reason or "")
        )
        if changed:
            audit.record(
                ctx,
                "staff.permission_revoked",
                resource_type="authz.staff_grant",
                resource_id=grant.id,
                changes={"permission": grant.permission, "holder": str(grant.user_id)},
                reason=grant.revoke_reason,
            )
    metrics.increment("staff_permission_changes_total", operation="revoke")
    return grant


def list_grants(ctx: TenantContext) -> list[dict[str, Any]]:
    authz.require(ctx, VIEW)
    return staff.live_grants()


def my_permissions(ctx: TenantContext) -> list[str]:
    """What the acting staff member holds - for the admin shell's navigation.

    Convenience for the UI only; the backend re-checks every request.
    """
    return sorted(
        k for k in authz.effective_permissions(ctx) if k.startswith("platform.staff_")
    )


# --- Phase 9: named roles ---------------------------------------------------------


def _held_keys(user_id: UUID) -> dict[str, str]:
    """``{permission: grant id}`` for the grants this person holds in force."""
    return {
        g["permission"]: g["id"] for g in staff.live_grants(user_id=user_id, limit=500)
    }


@in_scope
def grant_role(
    ctx: TenantContext,
    *,
    email: Any,
    role: Any,
    reason: Any,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Give a staff member every permission of a named role, in one audited
    transaction. Grants they already hold are kept as they are."""
    from workeasy360.platform.authz import staff_roles

    authz.require(ctx, MANAGE)
    definition = staff_roles.role(str(role or ""))
    if definition is None:
        raise ControlPlaneInvariantViolation("Unknown staff role.", field="role")
    user_id = active_user_id_for_email(str(email or ""))
    if user_id is None:
        raise ControlPlaneInvariantViolation(
            "That identity cannot hold control-plane permissions.", field="email"
        )
    added: list[str] = []
    with transaction.atomic():
        for permission in sorted(definition.permissions):
            _grant, created = staff.create_grant(
                user_id=user_id,
                permission=permission,
                granted_by_id=ctx.user_id,
                reason=str(reason or ""),
                expires_at=expires_at,
            )
            if created:
                added.append(permission)
        audit.record(
            ctx,
            "staff.role_granted",
            resource_type="identity.user",
            resource_id=user_id,
            changes={
                "role": definition.key,
                "grantee": str(user_id),
                "added": added,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
            reason=str(reason or ""),
        )
    metrics.increment("staff_permission_changes_total", operation="grant_role")
    return {"user_id": str(user_id), "role": definition.key, "added": added}


@in_scope
def revoke_role(
    ctx: TenantContext, *, user_id: UUID, role: Any, reason: Any
) -> dict[str, Any]:
    """End a role: the grants it needs that no *other* role the person still
    holds needs too. The last permanent security administrator cannot be
    removed (``end_grant`` refuses, and the whole revocation rolls back)."""
    from workeasy360.platform.authz import staff_roles

    authz.require(ctx, MANAGE)
    definition = staff_roles.role(str(role or ""))
    if definition is None:
        raise ControlPlaneInvariantViolation("Unknown staff role.", field="role")
    ended: list[str] = []
    with transaction.atomic():
        held = _held_keys(user_id)
        others = [
            staff_roles.role(key)
            for key in staff_roles.roles_held(set(held))
            if key != definition.key
        ]
        still_needed = (
            set().union(*(r.permissions for r in others if r)) if others else set()
        )
        for permission in sorted(definition.permissions & set(held) - still_needed):
            staff.end_grant(
                grant_id=UUID(held[permission]),
                revoked_by_id=ctx.user_id,
                reason=str(reason or ""),
            )
            ended.append(permission)
        audit.record(
            ctx,
            "staff.role_revoked",
            resource_type="identity.user",
            resource_id=user_id,
            changes={"role": definition.key, "holder": str(user_id), "ended": ended},
            reason=str(reason or ""),
        )
    metrics.increment("staff_permission_changes_total", operation="revoke_role")
    return {"user_id": str(user_id), "role": definition.key, "ended": ended}


def role_catalog(ctx: TenantContext) -> list[dict[str, Any]]:
    """Every role, its permissions and who holds it - derived from the grants
    in force, so it always matches what people can actually do."""
    from workeasy360.platform.authz import staff_roles

    authz.require(ctx, VIEW)
    by_user: dict[str, dict[str, Any]] = {}
    for grant in staff.live_grants(limit=500):
        entry = by_user.setdefault(
            grant["user_id"], {"email": grant["email"], "keys": set()}
        )
        entry["keys"].add(grant["permission"])
    return [
        {
            "key": r.key,
            "name": r.name,
            "description": r.description,
            "permissions": sorted(r.permissions),
            "holders": sorted(
                (
                    {"user_id": uid, "email": entry["email"]}
                    for uid, entry in by_user.items()
                    if r.permissions <= entry["keys"]
                ),
                key=lambda h: h["email"],
            ),
        }
        for r in staff_roles.roles()
    ]
