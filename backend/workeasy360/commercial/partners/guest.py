"""Guest view: Workeasy360 staff looking at a partner's portal as that partner.

The Super Admin's "Guest login" (All Partners). It is impersonation in the
sense of 04-identity-authz.md section 6, and it keeps that section's rules:

- **Never the partner's identity.** The staff member stays signed in as
  themselves; their requests to that one partner's portal are answered with a
  ``PARTNER`` context whose user is the staff member, marked ``is_break_glass``
  with the reason they gave. Every audit entry names them, never the partner.
- **Read-only.** The portal adapter refuses every unsafe method for a guest,
  the permission resolver gives a guest the *viewer* tier of the partner's
  capabilities only, and ``dangerous`` permissions are blocked under
  impersonation by the decision function itself. Bulk export is refused too.
- **Reason-required and time-boxed.** A reason of a few words is required, and
  the view ends by itself after ``GUEST_MINUTES``; it also ends with the
  sign-in, on "Exit guest view", or when another partner is opened.
- **Visible to the partner.** Starting and ending it is written to the audit
  log against the partner, which is what the partner's Activity reads.

The grant is held in the cache under the sign-in id (ADR-043), so it reaches
every portal session handed off from the staff member's sign-in and nothing
else. A cache that cannot be read means no guest view: it fails closed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.core.cache import cache
from django.utils import timezone

from workeasy360.commercial.partners import capabilities, selectors
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz.control_plane import in_scope

STAFF_MANAGE: Final = "platform.staff_partner.manage"
STAFF_VIEW: Final = "platform.staff_partner.view"

#: How long a guest view lasts before it ends by itself.
GUEST_MINUTES: Final = 30
REASON_MIN: Final = 3
REASON_MAX: Final = 200

_KEY_PREFIX: Final = "partner_guest:"

#: Where each workspace opens in the partner portal (the frontend's
#: ``workspaceHome`` in ``features/partnerpanel/experiences.ts``).
WORKSPACE_HOMES: Final[dict[str, str]] = {
    capabilities.RESELLER: "/partner/reseller/dashboard",
    capabilities.WHITE_LABEL: "/partner/white-label/dashboard",
    capabilities.AFFILIATE: "/partner/affiliate/dashboard",
}


@dataclass(frozen=True)
class GuestView:
    partner_id: UUID
    user_id: UUID
    reason: str
    started_at: datetime
    expires_at: datetime

    def as_json(self) -> dict[str, Any]:
        return {
            "partner_id": str(self.partner_id),
            "reason": self.reason,
            "started_at": self.started_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


def _key(sign_in_id: str) -> str:
    return f"{_KEY_PREFIX}{sign_in_id}"


def clean_reason(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if any(unicodedata.category(ch).startswith("C") for ch in text):
        raise PartnerValidationError(
            "The reason contains invalid characters.", field="reason"
        )
    if len(text) < REASON_MIN:
        raise PartnerValidationError(
            "Say in a few words why you are opening this partner's portal.",
            field="reason",
        )
    if len(text) > REASON_MAX:
        raise PartnerValidationError(
            f"The reason can be at most {REASON_MAX} characters.", field="reason"
        )
    return text


@in_scope
def start(
    ctx: TenantContext, partner_id: UUID, *, reason: Any, sign_in_id: str | None
) -> GuestView:
    """Open ``partner_id``'s portal read-only for the staff member ``ctx``.

    Needs ``platform.staff_partner.manage`` (dangerous: audited,
    refused under impersonation - so a guest cannot start another).
    Replaces any guest view this sign-in already had, ending that one.
    """
    from workeasy360.platform.audit import services as audit
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, STAFF_MANAGE)
    text = clean_reason(reason)
    if selectors.partner_row(partner_id) is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    if not sign_in_id:
        raise PermissionDenied(
            "Sign in again to open a guest view.",
            permission=STAFF_MANAGE,
            reason="no_sign_in",
        )
    previous = current(ctx.user_id, sign_in_id)
    if previous is not None:
        end(ctx, sign_in_id)
    now = timezone.now()
    view = GuestView(
        partner_id=partner_id,
        user_id=ctx.user_id,
        reason=text,
        started_at=now,
        expires_at=now + timedelta(minutes=GUEST_MINUTES),
    )
    payload = {**view.as_json(), "user_id": str(ctx.user_id)}
    cache.set(_key(sign_in_id), payload, timeout=GUEST_MINUTES * 60)
    if cache.get(_key(sign_in_id)) != payload:
        raise PermissionDenied(
            "Guest view is unavailable right now. Try again shortly.",
            permission=STAFF_MANAGE,
            reason="guest_view_unavailable",
        )
    try:
        audit.record(
            ctx,
            "partner.guest_view_started",
            resource_type="partners.partner",
            resource_id=partner_id,
            changes={"minutes": GUEST_MINUTES},
            reason=text,
        )
    except Exception:
        # Never a guest view that was not recorded.
        cache.delete(_key(sign_in_id))
        raise
    return view


def portal_path(partner_id: UUID) -> str:
    """Where a guest view of ``partner_id`` opens: the workspace the partner
    itself lands in (its stored preference among the workspaces its live
    grants open, ``capabilities.resolve_landing``), else the generic panel.
    The same resolution the partner's own sign-in uses, so the guest sees the
    portal the partner sees. A path only - the portal authorizes the guest
    again on every request."""
    landing = selectors.landing_workspace(partner_id)
    return WORKSPACE_HOMES.get(landing or "", f"/partner/{partner_id}")


def current(user_id: UUID, sign_in_id: str | None) -> GuestView | None:
    """The live guest view of this sign-in, if it belongs to ``user_id``."""
    if not sign_in_id:
        return None
    try:
        raw = cache.get(_key(sign_in_id))
    except Exception:
        return None
    if not isinstance(raw, dict) or raw.get("user_id") != str(user_id):
        return None
    try:
        view = GuestView(
            partner_id=UUID(str(raw["partner_id"])),
            user_id=user_id,
            reason=str(raw["reason"]),
            started_at=datetime.fromisoformat(str(raw["started_at"])),
            expires_at=datetime.fromisoformat(str(raw["expires_at"])),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if view.expires_at <= timezone.now():
        return None
    return view


@in_scope
def end(ctx: TenantContext, sign_in_id: str | None) -> bool:
    """End this sign-in's guest view; whether there was one."""
    from workeasy360.platform.audit import services as audit

    view = current(ctx.user_id, sign_in_id)
    if sign_in_id:
        cache.delete(_key(sign_in_id))
    if view is None:
        return False
    audit.record(
        ctx,
        "partner.guest_view_ended",
        resource_type="partners.partner",
        resource_id=view.partner_id,
        reason=view.reason,
    )
    return True
