"""Invitations: the Phase 1 ``Invitation`` model, finally written.

**The token.** ``<organization id>.<256 random bits>``. Only its SHA-256 is
stored (``Invitation.token_hash``), so a database dump yields no working link.
The organization id prefix is not a secret - it is what lets acceptance find
the invitation at all: invitations are tenant-scoped and RLS-protected, and a
lookup by hash alone, with no organization scope, matches nothing. It names
where to look; the hash decides whether anything is there.

**The link** travels in the notification's *sealed* context
(``notifications.sealing``) and is placed in the URL **fragment**
(``/invitations/accept#token=...``), which browsers never send to a server - it
cannot land in an access log, a proxy log or a ``Referer`` header.

**Privilege.** ``platform.member.invite`` invites members and read-only users.
Inviting someone as an administrator or billing manager *also* needs
``platform.role.assign`` (dangerous) - otherwise inviting would be a way round
the rule that assigning those roles needs step-up. Owners are never invited.

**Acceptance** is by the invited address only: a signed-in invitee must be
signed in as that address; otherwise an account is created (the password is
set here, and possession of the emailed link verifies the address) - unless the
address already has a usable account, in which case its owner signs in first.
Every failure to find a redeemable invitation is the same ``invitation_invalid``.

**Writers.** This module is the one writer of ``Invitation`` rows. Joining the
organization - the ``Membership`` and its chooser row - is written by
``platform.organizations.services.join_member``, the one writer of memberships.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.enums import OrganizationStatus
from workeasy360.contracts.errors import (
    InvitationEmailMismatch,
    InvitationInvalid,
    InvitationSignInRequired,
    MembershipConflict,
    MembershipValidationError,
    OrganizationNotWritable,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

TEMPLATE_KEY = "platform.member.invited"
_TOKEN_BYTES = 32

ROLE_NAMES = {
    "admin": "Administrator",
    "billing_manager": "Billing Manager",
    "member": "Member",
    "readonly": "Read Only",
}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token(organization_id: UUID) -> str:
    return f"{organization_id.hex}.{secrets.token_urlsafe(_TOKEN_BYTES)}"


def _organization_of(token: str) -> UUID | None:
    head, sep, tail = (token or "").strip().partition(".")
    if not sep or not tail or len(head) != 32:
        return None
    try:
        return UUID(hex=head)
    except ValueError:
        return None


def _ttl() -> timedelta:
    return timedelta(days=max(1, int(getattr(settings, "INVITATION_TTL_DAYS", 7))))


def _url(token: str) -> str:
    return f"{settings.W360_FRONTEND_URL}/invitations/accept#token={token}"


def _authorize_role(ctx: TenantContext, role_key: str) -> None:
    authz.require(ctx, "platform.member.invite")
    if role_key not in authz.ASSIGNABLE_SYSTEM_ROLES:
        raise MembershipValidationError(
            "Choose one of: " + ", ".join(authz.ASSIGNABLE_SYSTEM_ROLES) + ".",
            field="role",
        )
    if role_key in authz.PRIVILEGED_SYSTEM_ROLES:
        authz.require(ctx, "platform.role.assign")


# --- administrator side --------------------------------------------------------


def invite(ctx: TenantContext, *, email: Any, role_key: str = "member") -> dict[str, Any]:
    from workeasy360.platform.identity.email import is_plausible_email, normalize_email
    from workeasy360.platform.organizations.models import Invitation
    from workeasy360.platform.organizations.selectors import (
        invitation_row,
        member_status_for_email,
    )

    _authorize_role(ctx, role_key)
    address = normalize_email(str(email or ""))
    if not is_plausible_email(address) or len(address) > 254:
        raise MembershipValidationError("Enter a valid email address.", field="email")

    now = timezone.now()
    token = _new_token(ctx.organization_id)
    with transaction.atomic():
        existing = member_status_for_email(address)
        if existing is not None:
            raise MembershipConflict(
                "This person is already a member of the organization"
                + (
                    " (suspended - reactivate them instead)."
                    if existing == "suspended"
                    else "."
                )
            )
        open_invite = (
            Invitation.objects.select_for_update()
            .filter(email=address, accepted_at__isnull=True, revoked_at__isnull=True)
            .first()
        )
        if open_invite is not None:
            if open_invite.expires_at > now:
                raise MembershipConflict(
                    "This address already has a pending invitation. Resend it instead."
                )
            # An expired invitation holds the "one open invitation" slot until
            # it is closed; closing it is what makes room for the new one.
            open_invite.revoked_at = now
            open_invite.save(update_fields=["revoked_at", "updated_at"])
        try:
            with transaction.atomic():
                invitation = Invitation.objects.create(
                    email=address,
                    token_hash=_hash(token),
                    invited_by_id=ctx.membership_id,
                    expires_at=now + _ttl(),
                    role_key=role_key,
                    last_sent_at=now,
                )
        except IntegrityError as exc:
            raise MembershipConflict(
                "This address already has a pending invitation. Resend it instead."
            ) from exc
        _send(ctx, invitation, token)
        audit.record(
            ctx,
            "membership.invited",
            resource_type="organizations.invitation",
            resource_id=invitation.id,
            changes={"email": address, "role": role_key},
        )
    metrics.increment("organization_invitations_sent_total", kind="new")
    return invitation_row(invitation, now=now)


def resend(ctx: TenantContext, invitation_id: UUID) -> dict[str, Any]:
    """Send the invitation again under a **new** token (the old link stops
    working) with a fresh expiry."""
    from workeasy360.platform.organizations.models import Invitation
    from workeasy360.platform.organizations.selectors import invitation_row

    with transaction.atomic():
        invitation = (
            Invitation.objects.select_for_update()
            .filter(id=invitation_id, accepted_at__isnull=True, revoked_at__isnull=True)
            .first()
        )
        if invitation is None:
            raise InvitationInvalid("No such open invitation.")
        _authorize_role(ctx, invitation.role_key)
        now = timezone.now()
        token = _new_token(ctx.organization_id)
        invitation.token_hash = _hash(token)
        invitation.expires_at = now + _ttl()
        invitation.send_count += 1
        invitation.last_sent_at = now
        invitation.save(
            update_fields=[
                "token_hash",
                "expires_at",
                "send_count",
                "last_sent_at",
                "updated_at",
            ]
        )
        _send(ctx, invitation, token)
        audit.record(
            ctx,
            "membership.invitation_resent",
            resource_type="organizations.invitation",
            resource_id=invitation.id,
            changes={"send_count": invitation.send_count},
        )
    metrics.increment("organization_invitations_sent_total", kind="resend")
    return invitation_row(invitation, now=now)


def revoke(ctx: TenantContext, invitation_id: UUID) -> dict[str, Any]:
    from workeasy360.platform.organizations.models import Invitation
    from workeasy360.platform.organizations.selectors import invitation_row

    authz.require(ctx, "platform.member.invite")
    with transaction.atomic():
        invitation = (
            Invitation.objects.select_for_update()
            .filter(id=invitation_id, accepted_at__isnull=True, revoked_at__isnull=True)
            .first()
        )
        if invitation is None:
            raise InvitationInvalid("No such open invitation.")
        now = timezone.now()
        invitation.revoked_at = now
        invitation.save(update_fields=["revoked_at", "updated_at"])
        audit.record(
            ctx,
            "membership.invitation_revoked",
            resource_type="organizations.invitation",
            resource_id=invitation.id,
        )
    return invitation_row(invitation, now=now)


def page(ctx: TenantContext, *, state: str | None, limit: int, cursor: str | None) -> Any:
    from workeasy360.platform.organizations.selectors import invitation_page

    authz.require(ctx, "platform.member.invite")
    return invitation_page(
        ctx, state=state, now=timezone.now(), limit=limit, cursor=cursor
    )


def _send(ctx: TenantContext, invitation: Any, token: str) -> None:
    from workeasy360.contracts.ports.notifications import NotificationRequest
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.notifications.services import notify
    from workeasy360.platform.organizations.selectors import organization_names

    organization_name = organization_names([ctx.organization_id]).get(
        ctx.organization_id, ""
    )
    inviter = (
        User.objects.filter(pk=ctx.user_id).values_list("full_name", "email").first()
    )
    notify(
        NotificationRequest(
            organization_id=ctx.organization_id,
            template_key=TEMPLATE_KEY,
            recipient_email=invitation.email,
            context={
                "organization_name": organization_name,
                "expires_on": invitation.expires_at.strftime("%d %B %Y"),
                "inviter_name": (inviter[0] or inviter[1]) if inviter else "",
                "role_name": ROLE_NAMES.get(invitation.role_key, invitation.role_key),
            },
            sealed_context={"invitation_url": _url(token)},
            idempotency_key=f"invitation:{invitation.id}:{invitation.send_count}",
        )
    )


# --- invitee side (public: no tenant context exists yet) ------------------------


@dataclass(frozen=True, slots=True)
class Accepted:
    user: Any
    membership_id: UUID
    organization_slug: str
    account_created: bool


def _open_for_token(token: str, *, lock: bool) -> tuple[Any, dict[str, Any]]:
    """``(invitation, organization)`` for a redeemable token, inside the
    organization's database scope the caller has entered - or
    ``InvitationInvalid``, uniformly. The organization is its lifecycle
    summary (id, slug, name, status), never a row to write."""
    from workeasy360.platform.organizations.models import Invitation
    from workeasy360.platform.organizations.selectors import organization_lifecycle

    organization_id = _organization_of(token)
    if organization_id is None:
        raise InvitationInvalid("This invitation link is not valid.")
    organization = organization_lifecycle(organization_id)
    if organization is None:
        raise InvitationInvalid("This invitation link is not valid.")
    rows = Invitation.unscoped.filter(
        organization_id=organization_id, token_hash=_hash(token)
    )
    if lock:
        rows = rows.select_for_update()
    invitation = rows.first()
    if invitation is None or not invitation.is_redeemable:
        raise InvitationInvalid("This invitation link is not valid.")
    return invitation, organization


@contextmanager
def _invitee_scope(token: str) -> Iterator[None]:
    """The one organization the token names: Layer 2 relaxed (no tenant
    context can exist yet), Layer 3 - RLS - bound to that organization only."""
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    organization_id = _organization_of(token)
    if organization_id is None:
        raise InvitationInvalid("This invitation link is not valid.")
    with (
        unscoped_access(
            reason="An invitee redeems an invitation before any tenant context exists",
            module="platform.administration.invitations",
        ),
        organization_db_scope(organization_id),
    ):
        yield


def preview(token: str, *, signed_in_email: str | None) -> dict[str, Any]:
    """What the acceptance page shows before the invitee commits."""
    from workeasy360.platform.identity.models import User

    with transaction.atomic(), _invitee_scope(token):
        invitation, organization = _open_for_token(token, lock=False)
        account = User.objects.filter(email=invitation.email).first()
    return {
        "organization_name": organization["display_name"],
        "email": invitation.email,
        "role": invitation.role_key,
        "role_name": ROLE_NAMES.get(invitation.role_key, invitation.role_key),
        "expires_at": invitation.expires_at.isoformat(),
        # The address is the invitee's own (they hold the emailed link), so
        # telling them whether it already has a password is not an oracle.
        "account_exists": bool(
            account is not None and account.has_usable_password() and account.is_active
        ),
        "signed_in_as_invitee": bool(
            signed_in_email and signed_in_email == invitation.email
        ),
    }


def accept(
    token: str,
    *,
    signed_in_user: Any = None,
    password: str | None = None,
    full_name: str | None = None,
) -> Accepted:
    """Redeem the invitation. One transaction: the account (if created), the
    membership, its role, the invitation's acceptance, the chooser row, the
    audit entry and the event commit or roll back together."""
    from workeasy360.platform.identity.email import normalize_email

    with transaction.atomic(), _invitee_scope(token):
        invitation, organization = _open_for_token(token, lock=True)
        status = OrganizationStatus(organization["status"])
        if not status.allows_write:
            raise OrganizationNotWritable(
                "This organization is not accepting new members at present.",
                organization_id=organization["organization_id"],
                status=status.value,
            )
        if signed_in_user is not None:
            if normalize_email(signed_in_user.email) != invitation.email:
                raise InvitationEmailMismatch(
                    "This invitation was sent to a different email address. Sign "
                    "out and open the link again, or ask for an invitation to this "
                    "account."
                )
            user, created = signed_in_user, False
        else:
            user, created = _account_for(invitation.email, password, full_name)
        membership_id = _join(invitation.organization_id, status, user, invitation)
    metrics.increment("organization_invitations_accepted_total")
    logger.info(
        "organizations.invitation.accepted",
        organization_id=organization["organization_id"],
        account_created=created,
    )
    return Accepted(
        user=user,
        membership_id=membership_id,
        organization_slug=organization["slug"],
        account_created=created,
    )


def _account_for(
    email: str, password: str | None, full_name: str | None
) -> tuple[Any, bool]:
    """The invitee's account: created now, or completed if it exists without a
    usable password (someone was named - e.g. by a partner - before they ever
    signed in). An account that can already sign in must sign in instead."""
    from workeasy360.contracts.enums import UserStatus
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.identity.account import check_new_password
    from workeasy360.platform.identity.models import User

    name = str(full_name or "").strip()[:255]
    existing = lifecycle.lock_by_email(email)
    if existing is not None and existing.status == UserStatus.PENDING.value:
        # An unverified self-service signup for this address (Phase 9). The
        # emailed invitation link proves the mailbox, so the invitee completes
        # the account here with *their* password - replacing whatever the
        # signup was registered with, which is exactly what defeats someone
        # who pre-registered this address.
        check_new_password(password or "", user=existing)
        lifecycle.complete_with_password(
            existing, password or "", full_name=name, activate=True
        )
        return existing, True
    if existing is not None:
        if existing.status != UserStatus.ACTIVE.value:
            raise InvitationInvalid("This invitation link is not valid.")
        if existing.has_usable_password():
            raise InvitationSignInRequired(
                "An account already exists for this address. Sign in to accept."
            )
        check_new_password(password or "", user=existing)
        lifecycle.complete_with_password(existing, password or "", full_name=name)
        return existing, True
    if not name:
        raise MembershipValidationError("Enter your name.", field="full_name")
    candidate = User(email=email, full_name=name)
    check_new_password(password or "", user=candidate)
    try:
        with transaction.atomic():
            user = lifecycle.create_account(
                email=email,
                password=password,
                full_name=name,
                verified=True,
                source=User.AcquisitionSource.INVITATION,
            )
    except IntegrityError as exc:
        # Another request created this address a moment ago.
        raise InvitationSignInRequired(
            "An account already exists for this address. Sign in to accept."
        ) from exc
    return user, True


def _join(
    organization_id: UUID, status: OrganizationStatus, user: Any, invitation: Any
) -> UUID:
    from workeasy360.contracts.ports.events import get_event_publisher
    from workeasy360.platform.organizations.services import join_member
    from workeasy360.platform.tenancy.context import tenant_context

    # The membership and its chooser row; a suspended member or a pending
    # owner is refused there.
    member, joined = join_member(organization_id, user.pk)

    invitation.accepted_at = timezone.now()
    invitation.accepted_by_id = user.pk
    invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])

    ctx = TenantContext(
        organization_id=organization_id,
        membership_id=member.membership_id,
        user_id=user.pk,
        organization_status=status,
    )
    with tenant_context(ctx):
        if joined:
            authz.replace_system_role(
                organization_id=organization_id,
                membership_id=member.membership_id,
                role_key=invitation.role_key,
            )
        audit.record(
            ctx,
            "membership.invitation_accepted",
            resource_type="organizations.membership",
            resource_id=member.membership_id,
            changes={
                "invitation_id": str(invitation.id),
                "role": invitation.role_key,
                "already_member": not joined,
            },
        )
        publisher = get_event_publisher()
        if publisher is not None and joined:
            publisher.publish(
                ctx,
                "organizations.membership.added",
                1,
                aggregate_type="organizations.membership",
                aggregate_id=str(member.membership_id),
                payload={
                    "organization_id": str(organization_id),
                    "membership_id": str(member.membership_id),
                    "user_id": str(user.pk),
                    "member_type": member.member_type,
                },
            )
    return member.membership_id
