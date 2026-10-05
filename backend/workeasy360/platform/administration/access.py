"""What the signed-in member may see in the organization console.

``console_access`` answers the console's first question on every load - who am
I here, and what may I do - and records the member entering the organization
(``organizations.MemberSignIn``, once per browser session).

The permission list drives *navigation only*. Every endpoint behind it checks
its own permission again, server-side; hiding a menu item is a convenience,
never a control.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

#: Holding any of these puts the member in the administrator console (the
#: dashboard) rather than the member view (their apps). Owners and
#: administrators hold them; billing managers, members and read-only users do
#: not.
ADMIN_CONSOLE_PERMISSIONS = ("platform.audit.view", "platform.member.invite")


def console_access(
    ctx: TenantContext,
    *,
    session_ref: UUID | None,
    ip_prefix: str = "",
    device_label: str = "",
) -> dict[str, Any]:
    from workeasy360.platform.organizations.selectors import organization_summary

    permissions = sorted(authz.effective_permissions(ctx))
    roles = authz.system_role_keys([ctx.membership_id]).get(str(ctx.membership_id), [])
    organization = organization_summary(ctx) or {}
    membership = _membership(ctx)
    if session_ref is not None:
        record_sign_in(
            ctx, session_ref=session_ref, ip_prefix=ip_prefix, device_label=device_label
        )
    return {
        "organization": organization,
        "membership": {
            "membership_id": str(ctx.membership_id),
            "is_owner": bool(membership and membership["is_owner"]),
            "roles": roles,
        },
        "permissions": permissions,
        "console": "admin"
        if any(p in permissions for p in ADMIN_CONSOLE_PERMISSIONS)
        else "member",
    }


def _membership(ctx: TenantContext) -> dict[str, Any] | None:
    from workeasy360.platform.organizations.selectors import membership_for_user

    return membership_for_user(ctx, ctx.user_id)


def record_sign_in(
    ctx: TenantContext, *, session_ref: UUID, ip_prefix: str, device_label: str
) -> None:
    """Idempotent per (membership, session): ``ON CONFLICT DO NOTHING``.

    Skipped for a read-only organization rather than exempted from the R-10
    write block - a suspended organization's members still open the console,
    and a missing activity row is a smaller cost than widening the block's
    exemptions for an activity log.
    """
    from workeasy360.platform.organizations.services import record_member_sign_in

    if not ctx.is_write_allowed:
        return
    record_member_sign_in(
        organization_id=ctx.organization_id,
        membership_id=ctx.membership_id,
        user_id=ctx.user_id,
        session_ref=session_ref,
        ip_prefix=ip_prefix,
        device_label=device_label,
    )
