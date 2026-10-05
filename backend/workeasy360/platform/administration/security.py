"""The organization's own access policy, and its name.

Auth configuration (required MFA, session length, password minimum) and
network access (the IP allow-list) are two pages in the console but one
policy on the organization row, applied by ``contracts.values.access_policy``
at both doors - the console and product sign-in. Changing either is
``platform.security.update``: dangerous (AAL 2, refused under impersonation,
refused while the organization is read-only) and audited with before/after.

Both edits carry the version the administrator loaded: two administrators
saving at once get a conflict, not a silently merged allow-list. The row is
written by ``platform.organizations.services`` - its one writer - which holds
that version check; this module validates, authorizes and audits.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction

from workeasy360.contracts.errors import (
    NetworkLockout,
    SecurityPolicyValidationError,
)
from workeasy360.contracts.values import access_policy
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.organizations.services import update_access_policy

UPDATE = "platform.security.update"
VIEW = "platform.security.view"


def read(ctx: TenantContext) -> dict[str, Any]:
    from workeasy360.platform.organizations.selectors import security_policy

    authz.require(ctx, VIEW)
    policy = security_policy(ctx)
    policy["platform_password_min_length"] = int(settings.PASSWORD_MIN_LENGTH)
    return policy


def _optional_int(raw: Any, field: str, *, low: int, high: int) -> int | None:
    if raw in (None, "", 0):
        return None
    if isinstance(raw, bool):
        raise SecurityPolicyValidationError(f"{field} must be a number.", field=field)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise SecurityPolicyValidationError(
            f"{field} must be a number.", field=field
        ) from exc
    if not low <= value <= high:
        raise SecurityPolicyValidationError(
            f"{field} must be between {low} and {high}.", field=field
        )
    return value


def update_authentication(
    ctx: TenantContext,
    *,
    require_mfa: Any,
    session_max_age_minutes: Any,
    password_min_length: Any,
    expected_version: int,
) -> dict[str, Any]:
    authz.require(ctx, UPDATE)
    if not isinstance(require_mfa, bool):
        raise SecurityPolicyValidationError(
            "require_mfa must be true or false.", field="require_mfa"
        )
    max_age = _optional_int(
        session_max_age_minutes,
        "session_max_age_minutes",
        low=access_policy.MIN_SESSION_MAX_AGE_MINUTES,
        high=access_policy.MAX_SESSION_MAX_AGE_MINUTES,
    )
    minimum = _optional_int(
        password_min_length,
        "password_min_length",
        low=int(settings.PASSWORD_MIN_LENGTH),
        high=access_policy.MIN_PASSWORD_LENGTH_CEILING,
    )
    changes = {
        "require_mfa": require_mfa,
        "session_max_age_minutes": max_age,
        "password_min_length": minimum,
    }
    with transaction.atomic():
        previous = update_access_policy(
            ctx.organization_id, changes, expected_version=expected_version
        )
        audit.record(
            ctx,
            "security.policy_updated",
            resource_type="organizations.organization",
            resource_id=ctx.organization_id,
            changes={"before": _auth_view(previous), "after": _auth_view(changes)},
        )
    return read(ctx)


def _auth_view(values: dict[str, Any]) -> dict[str, Any]:
    """Audit keys avoid the words the audit redactor treats as secrets
    ("mfa", "session", "password"): these values are policy, not credentials,
    and an auditor must be able to read them."""
    return {
        "two_step_verification": values["require_mfa"],
        "sign_in_max_age_minutes": values["session_max_age_minutes"],
        "minimum_length": values["password_min_length"],
    }


def update_network(
    ctx: TenantContext,
    *,
    enabled: Any,
    entries: Any,
    expected_version: int,
    client_ip: str,
) -> dict[str, Any]:
    authz.require(ctx, UPDATE)
    if not isinstance(enabled, bool):
        raise SecurityPolicyValidationError(
            "enabled must be true or false.", field="ip_allowlist_enabled"
        )
    if not isinstance(entries, list):
        raise SecurityPolicyValidationError(
            "ip_allowlist must be a list.", field="ip_allowlist"
        )
    try:
        networks = access_policy.normalise_allowlist(entries)
    except ValueError as exc:
        raise SecurityPolicyValidationError(str(exc), field="ip_allowlist") from exc
    if enabled and not networks:
        raise SecurityPolicyValidationError(
            "Add at least one network before turning the allow-list on.",
            field="ip_allowlist",
        )
    if enabled and not access_policy.address_allowed(client_ip, networks):
        # Saving this would lock the administrator out on their next request.
        raise NetworkLockout(
            "Your current network is not in the list, so saving it would lock "
            "you out. Add your own network first.",
            your_network=_display_network(client_ip),
        )
    with transaction.atomic():
        previous = update_access_policy(
            ctx.organization_id,
            {"ip_allowlist_enabled": enabled, "ip_allowlist": list(networks)},
            expected_version=expected_version,
        )
        audit.record(
            ctx,
            "security.network_policy_updated",
            resource_type="organizations.organization",
            resource_id=ctx.organization_id,
            changes={
                "before": {
                    "enabled": previous["ip_allowlist_enabled"],
                    "networks": list(previous["ip_allowlist"] or []),
                },
                "after": {"enabled": enabled, "networks": list(networks)},
            },
        )
    return read(ctx)


def reset_network_as_staff(
    actor: TenantContext, organization_id: UUID, *, reason: str
) -> dict[str, Any]:
    """Break-glass for an organization that locked itself out: turn its
    allow-list off. ``platform.staff_customer.manage`` (dangerous), a reason of
    at least ten characters, audited in the customer's own log."""
    from workeasy360.platform.authz.control_plane import organization_scope

    authz.require(actor, "platform.staff_customer.manage")
    if len((reason or "").strip()) < 10:
        raise SecurityPolicyValidationError(
            "Give a reason of at least 10 characters.", field="reason"
        )
    with organization_scope(actor, organization_id) as ctx, transaction.atomic():
        # Break-glass overrides whatever the customer last saved: no version.
        previous = update_access_policy(
            organization_id, {"ip_allowlist_enabled": False}, expected_version=None
        )
        audit.record(
            ctx,
            "security.network_policy_reset",
            resource_type="organizations.organization",
            resource_id=organization_id,
            changes={
                "before": {"enabled": previous["ip_allowlist_enabled"]},
                "after": {"enabled": False},
            },
            reason=reason.strip(),
        )
    return {"organization_id": str(organization_id), "ip_allowlist_enabled": False}


def _display_network(client_ip: str) -> str:
    from workeasy360.platform.identity.devices import network_prefix

    return network_prefix(client_ip) or "unknown"


def rename(ctx: TenantContext, *, display_name: Any) -> dict[str, Any]:
    """Change the organization's display name (not its slug, which is URL
    identity and immutable). ``platform.organization.update``."""
    from workeasy360.platform.organizations.selectors import organization_summary
    from workeasy360.platform.organizations.services import rename_organization

    authz.require(ctx, "platform.organization.update")
    with transaction.atomic():
        # The row and every member's chooser row; None when nothing changed.
        renamed = rename_organization(ctx.organization_id, display_name)
        if renamed is not None:
            audit.record(
                ctx,
                "organization.renamed",
                resource_type="organizations.organization",
                resource_id=ctx.organization_id,
                changes={"before": renamed[0], "after": renamed[1]},
            )
    return organization_summary(ctx) or {}
