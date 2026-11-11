"""Login as Guest: Workeasy360 staff looking at a partner's portal (ADR-062).

The Super Admin's "Login as Guest" (All Partners, the Affiliate, Reseller and
White Label lists, and a partner's own page). It is impersonation in the sense
of 04-identity-authz.md section 6, and it keeps that section's rules:

- **Never the partner's identity.** The staff member stays signed in as
  themselves; their requests to that one partner's portal are answered with a
  ``PARTNER`` context whose user is the staff member, marked ``is_break_glass``
  with the reason they gave. No partner password, code or secret is read or
  used. Every audit entry names the staff member, never the partner.
- **Read-only.** The portal adapter refuses every unsafe method for a guest,
  the permission resolver gives a guest the *viewer* tier of the partner's
  capabilities only, ``dangerous`` permissions are blocked under impersonation
  by the decision function itself, and a partner-portal session that exists
  only for a guest view is refused every write on every endpoint
  (``interfaces.api.guest_session``). Bulk export, KYC details and payout
  details are not shown to a guest.
- **Explicit permission, reason, time box.** Starting one needs
  ``platform.staff_partner.guest`` (dangerous: audited, refused under
  impersonation - so a guest cannot start another) and a reason of a few
  words. The view ends by itself after ``GUEST_MINUTES``, and also on "Exit
  Guest Mode", on signing out of the Admin, when another is started from the
  same sign-in, or when another Admin revokes it ("Manage Guest Access").
- **Visible to the partner.** Starting and ending it is written to the audit
  log against the partner, which is what the partner's Activity reads.

**The record is a row** (``PartnerGuestSession``), read on every guest request,
so ending or revoking it takes effect on the next request; a database that
cannot be read means no guest view (it fails closed).

**Reaching the partner portal's host.** Each portal keeps its own host-only
session (ADR-059), so the Admin's session does not exist on ``partner.``. In
multi-portal mode ``start`` also issues a one-time ticket (sixty seconds,
stored only as a SHA-256 digest). The Admin page posts it - in a form body,
never a URL - to ``partner.``'s ``/api/v1/partner/guest-view/enter``, which
spends it (``enter``) and opens a session there that carries the guest view's
id and the Admin's sign-in, can only read, and ends with the view. In
single-host mode (development) one session serves every page and the view
attaches to it directly.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.partners import capabilities, selectors
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import (
    PartnerError,
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz.control_plane import in_scope

#: Starting, revoking: the explicit guest-session permission (implies view).
STAFF_GUEST: Final = "platform.staff_partner.guest"
STAFF_VIEW: Final = "platform.staff_partner.view"

#: How long a guest view lasts before it ends by itself.
GUEST_MINUTES: Final = 30
#: How long the one-time ticket that opens the partner portal's session lives.
ENTRY_SECONDS: Final = 60
REASON_MIN: Final = 3
REASON_MAX: Final = 200
#: Rows "Manage Guest Access" shows.
HISTORY: Final = 20

_TICKET_RE: Final = re.compile(r"^[A-Za-z0-9_-]{40,64}$")

#: Where "Exit Guest Mode" may return in the Admin: the partner lists and one
#: partner's page. Anything else returns to All Partners.
_RETURN_RE: Final = re.compile(
    r"^/admin/partners(/(all|affiliate|reseller|white-label|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))?$"
)
DEFAULT_RETURN: Final = "/admin/partners/all"

#: Where each workspace opens in the partner portal (the frontend's
#: ``workspaceHome`` in ``features/partnerpanel/experiences.ts``).
WORKSPACE_HOMES: Final[dict[str, str]] = {
    capabilities.RESELLER: "/partner/reseller/dashboard",
    capabilities.WHITE_LABEL: "/partner/white-label/dashboard",
    capabilities.AFFILIATE: "/partner/affiliate/dashboard",
}


@dataclass(frozen=True)
class GuestView:
    id: UUID
    partner_id: UUID
    user_id: UUID
    sign_in_id: UUID
    reason: str
    started_at: datetime
    expires_at: datetime
    return_path: str = DEFAULT_RETURN
    entered_at: datetime | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "partner_id": str(self.partner_id),
            "reason": self.reason,
            "started_at": self.started_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


def _model() -> Any:
    from workeasy360.commercial.partners.models import PartnerGuestSession

    return PartnerGuestSession


def _view(row: Any) -> GuestView:
    return GuestView(
        id=row.id,
        partner_id=row.partner_id,
        user_id=row.staff_user_id,
        sign_in_id=row.sign_in_id,
        reason=row.reason,
        started_at=row.started_at,
        expires_at=row.expires_at,
        return_path=row.return_path or DEFAULT_RETURN,
        entered_at=row.entered_at,
    )


def _digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("ascii")).hexdigest()


def _uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


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


def clean_return_path(value: Any) -> str:
    """A path of the Admin's partner pages, or All Partners. Never an origin."""
    text = str(value or "").strip()
    return text if _RETURN_RE.match(text) else DEFAULT_RETURN


def available(status: str) -> bool:
    """Whether a partner in ``status`` has a portal to look at. A draft holds no
    partner permission at all (``resolver``), so its portal would show nothing
    but its own KYC form - which a guest may not read."""
    return status != PartnerStatus.DRAFT.value


@in_scope
def start(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    reason: Any,
    sign_in_id: Any,
    return_path: Any = None,
    with_entry: bool = False,
) -> tuple[GuestView, str | None]:
    """Open ``partner_id``'s portal read-only for the staff member ``ctx``.

    Needs ``platform.staff_partner.guest``. Ends any guest view this sign-in
    already had. With ``with_entry`` (multi-portal mode) also returns the
    one-time ticket that opens the partner portal's session; the caller hands
    it to the browser in a response body and nowhere else.
    """
    from workeasy360.platform.audit import services as audit
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, STAFF_GUEST)
    text = clean_reason(reason)
    back = clean_return_path(return_path)
    partner = selectors.partner_row(partner_id)
    if partner is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    if not available(partner.status):
        raise PartnerValidationError(
            "This partner is still a draft, so it has no partner portal to view "
            "yet. Login as Guest opens once it is active.",
            field="partner",
        )
    signed = _uuid(sign_in_id)
    if signed is None:
        raise PermissionDenied(
            "Sign in again to open a guest view.",
            permission=STAFF_GUEST,
            reason="no_sign_in",
        )
    now = timezone.now()
    ticket = secrets.token_urlsafe(32) if with_entry else None
    model = _model()
    try:
        with transaction.atomic():
            for row in model.objects.select_for_update().filter(
                sign_in_id=signed, ended_at__isnull=True
            ):
                _close(
                    ctx,
                    row,
                    "expired" if row.expires_at <= now else "replaced",
                    by=None if row.expires_at <= now else ctx.user_id,
                    now=now,
                )
            row = model.objects.create(
                partner_id=partner_id,
                staff_user_id=ctx.user_id,
                sign_in_id=signed,
                reason=text,
                return_path=back,
                started_at=now,
                expires_at=now + timedelta(minutes=GUEST_MINUTES),
                entry_digest=_digest(ticket) if ticket else None,
                entry_expires_at=now + timedelta(seconds=ENTRY_SECONDS)
                if ticket
                else None,
            )
            # Never a guest view that was not recorded: the row and its audit
            # entry commit together or not at all.
            audit.record(
                ctx,
                "partner.guest_view_started",
                resource_type="partners.partner",
                resource_id=partner_id,
                changes={"guest_view_id": str(row.id), "minutes": GUEST_MINUTES},
                reason=text,
            )
    except IntegrityError as exc:
        # Another guest view of this sign-in was started at the same moment.
        raise PartnerError(
            "Another guest view was just opened from this sign-in. Try again."
        ) from exc
    return _view(row), ticket


def enter(ticket: Any) -> GuestView | None:
    """Spend a one-time entry ticket; the guest view it opens, or ``None``.

    ``None`` - one answer for every reason - unless the ticket is well formed,
    unspent, not past its sixty seconds, and its guest view is still open and
    unexpired. The caller re-checks the staff member before opening a session.
    """
    if not isinstance(ticket, str) or not _TICKET_RE.match(ticket):
        return None
    now = timezone.now()
    model = _model()
    with transaction.atomic():
        row = (
            model.objects.select_for_update()
            .filter(
                entry_digest=_digest(ticket),
                entry_expires_at__gt=now,
                entered_at__isnull=True,
                ended_at__isnull=True,
                expires_at__gt=now,
            )
            .first()
        )
        if row is None:
            return None
        row.entry_digest = None
        row.entered_at = now
        row.save(update_fields=["entry_digest", "entered_at"])
    return _view(row)


def portal_path(partner_id: UUID) -> str:
    """Where a guest view of ``partner_id`` opens: the workspace the partner
    itself lands in (its stored preference among the workspaces its live
    grants open, ``capabilities.resolve_landing``), else the generic panel.
    The same resolution the partner's own sign-in uses, so the guest sees the
    portal the partner sees. A path only - the portal authorizes the guest
    again on every request."""
    landing = selectors.landing_workspace(partner_id)
    return WORKSPACE_HOMES.get(landing or "", f"/partner/{partner_id}")


def current(user_id: UUID, sign_in_id: Any) -> GuestView | None:
    """The live guest view of this sign-in, if it belongs to ``user_id``."""
    signed = _uuid(sign_in_id)
    if signed is None:
        return None
    try:
        row = (
            _model()
            .objects.filter(
                staff_user_id=user_id,
                sign_in_id=signed,
                ended_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
    except Exception:  # fails closed: no row read, no guest view
        return None
    return _view(row) if row is not None else None


def live(session_id: Any, user_id: UUID) -> GuestView | None:
    """The guest view ``session_id`` if it is still live and ``user_id``'s."""
    wanted = _uuid(session_id)
    if wanted is None:
        return None
    try:
        row = (
            _model()
            .objects.filter(
                id=wanted,
                staff_user_id=user_id,
                ended_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
    except Exception:  # fails closed
        return None
    return _view(row) if row is not None else None


_ID_SEGMENT = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def record_request(view: GuestView, *, method: str, path: str, status: int) -> None:
    """ADR-063 (the specification's 1.3, item 8): every request a guest view makes
    is in the audit log beside its start and end - who (the staff member), which
    partner, the view, what was asked and what the server answered. A refused
    write is recorded the same way. Identifiers in the path are kept as a count,
    never a customer's name. Best effort: an audit outage never breaks the view
    (the start and end entries are already durable)."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit import services as audit
    from workeasy360.platform.tenancy.db import organization_db_scope

    try:
        with organization_db_scope(PLATFORM_ORGANIZATION_ID):
            _record_request(view, audit, PLATFORM_ORGANIZATION_ID, method, path, status)
    except Exception:  # pragma: no cover - best effort, see above
        return


def _record_request(
    view: GuestView, audit: Any, org: UUID, method: str, path: str, status: int
) -> None:
    audit.record_system(
        org,
        "partner.guest_view_request",
        resource_type="partners.partner",
        resource_id=view.partner_id,
        changes={
            "guest_view_id": str(view.id),
            "staff_user_id": str(view.user_id),
            "method": method[:8],
            "path": _ID_SEGMENT.sub("/{id}", path)[:200],
            "status": int(status),
            "refused": status in (401, 403, 405),
        },
        reason=view.reason,
    )


def _close(
    ctx: TenantContext | None,
    row: Any,
    end_reason: str,
    *,
    by: UUID | None,
    now: datetime,
    why: str = "",
) -> bool:
    """End one open guest view and record it; whether this call ended it."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit import services as audit

    ended_at = min(now, row.expires_at) if end_reason == "expired" else now
    updated = (
        _model()
        .objects.filter(pk=row.pk, ended_at__isnull=True)
        .update(
            ended_at=ended_at,
            end_reason=end_reason,
            ended_by_id=by,
            entry_digest=None,
        )
    )
    if not updated:
        return False
    changes = {"guest_view_id": str(row.id), "end_reason": end_reason}
    if ctx is None:
        audit.record_system(
            PLATFORM_ORGANIZATION_ID,
            "partner.guest_view_ended",
            resource_type="partners.partner",
            resource_id=row.partner_id,
            changes=changes,
            reason=row.reason,
        )
    else:
        audit.record(
            ctx,
            "partner.guest_view_ended",
            resource_type="partners.partner",
            resource_id=row.partner_id,
            changes=changes,
            reason=why or row.reason,
        )
    return True


@in_scope
def end(ctx: TenantContext, sign_in_id: Any, *, end_reason: str = "exited") -> bool:
    """End this sign-in's guest view; whether there was one. Signing out of the
    Admin ends it with ``signed_out``."""
    signed = _uuid(sign_in_id)
    if signed is None:
        return False
    now = timezone.now()
    ended = False
    with transaction.atomic():
        for row in (
            _model()
            .objects.select_for_update()
            .filter(staff_user_id=ctx.user_id, sign_in_id=signed, ended_at__isnull=True)
        ):
            expired = row.expires_at <= now
            closed = _close(
                ctx,
                row,
                "expired" if expired else end_reason,
                by=None if expired else ctx.user_id,
                now=now,
            )
            if closed and not expired:
                ended = True
    return ended


@in_scope
def end_session(
    ctx: TenantContext, session_id: Any, *, end_reason: str = "exited"
) -> GuestView | None:
    """ "Exit Guest Mode" from the partner portal's own session (or signing out
    of it, ``signed_out``): end exactly the guest view that session was opened
    for. Returns it (for the way back), or ``None`` when it was not this staff
    member's or had already ended."""
    wanted = _uuid(session_id)
    if wanted is None:
        return None
    now = timezone.now()
    with transaction.atomic():
        row = (
            _model()
            .objects.select_for_update()
            .filter(id=wanted, staff_user_id=ctx.user_id)
            .first()
        )
        if row is None:
            return None
        if row.ended_at is None:
            expired = row.expires_at <= now
            _close(
                ctx,
                row,
                "expired" if expired else end_reason,
                by=None if expired else ctx.user_id,
                now=now,
            )
    return _view(row)


@in_scope
def revoke(
    ctx: TenantContext, partner_id: UUID, session_id: UUID, *, reason: Any
) -> dict[str, Any]:
    """Manage Guest Access → Revoke: end someone's guest view of this partner
    now. Takes effect on that guest's next request."""
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, STAFF_GUEST)
    why = clean_reason(reason)
    now = timezone.now()
    with transaction.atomic():
        row = (
            _model()
            .objects.select_for_update()
            .filter(id=session_id, partner_id=partner_id)
            .first()
        )
        if row is None:
            raise PartnerRecordNotFound("No such guest session.")
        if row.ended_at is None:
            expired = row.expires_at <= now
            _close(
                ctx,
                row,
                "expired" if expired else "revoked",
                by=None if expired else ctx.user_id,
                now=now,
                why=why,
            )
    return history(ctx, partner_id)


def _status(row: Any, now: datetime) -> str:
    if row.ended_at is not None:
        return row.end_reason
    return "active" if row.expires_at > now else "expired"


@in_scope
def history(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """Manage Guest Access: this partner's recent guest views - who, why, when,
    and how each ended - newest first. Never a ticket or a sign-in id."""
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, STAFF_VIEW)
    if selectors.partner_row(partner_id) is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    now = timezone.now()
    rows = list(
        _model()
        .objects.filter(partner_id=partner_id)
        .select_related("staff_user", "ended_by")
        .order_by("-started_at", "-id")[:HISTORY]
    )

    def person(user: Any) -> dict[str, str] | None:
        if user is None:
            return None
        return {"id": str(user.id), "name": user.full_name or "", "email": user.email}

    return {
        "minutes": GUEST_MINUTES,
        "may_start": authz.check(ctx, STAFF_GUEST).allowed,
        "sessions": [
            {
                "id": str(row.id),
                "staff": person(row.staff_user),
                "reason": row.reason,
                "started_at": row.started_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "entered_at": row.entered_at.isoformat() if row.entered_at else None,
                "ended_at": row.ended_at.isoformat() if row.ended_at else None,
                "ended_by": person(row.ended_by),
                "status": _status(row, now),
                "mine": row.staff_user_id == ctx.user_id,
            }
            for row in rows
        ],
    }


def close_expired(*, limit: int = 500) -> int:
    """Record the end of guest views that ran out (the scheduled sweep). They
    were already refused from the moment they expired; this only writes down
    that they ended, once each."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.authz.control_plane import ensure_platform_organization
    from workeasy360.platform.tenancy.db import organization_db_scope

    ensure_platform_organization()
    now = timezone.now()
    ids = list(
        _model()
        .objects.filter(ended_at__isnull=True, expires_at__lte=now)
        .order_by("expires_at")
        .values_list("id", flat=True)[:limit]
    )
    closed = 0
    for row_id in ids:
        with transaction.atomic(), organization_db_scope(PLATFORM_ORGANIZATION_ID):
            row = (
                _model()
                .objects.select_for_update()
                .filter(id=row_id, ended_at__isnull=True)
                .first()
            )
            if row is not None and _close(None, row, "expired", by=None, now=now):
                closed += 1
    return closed
