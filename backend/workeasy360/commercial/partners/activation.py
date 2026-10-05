"""Customer-owner activation: the link, the claim and the resend (Phase 10e,
closing debt D-020).

**The gap this closes.** A partner provisions a customer and names its owner by
email. The organization exists, the owner's membership is ``invited``, and an
invitation row waits - but the person named has no password, so nothing could
ever sign them in to accept it. This module gives them a complete, secure
journey using the identity system that already exists, and adds no second one:

    provision -> email with a one-time link -> preview -> set a password (or sign
    in, if the address already has one) -> the existing owner activation ->
    signed in, owner of the organization

**The link** is ``<organization id, hex>.<256 random bits>``:

- *unguessable* - 32 bytes from ``secrets``;
- *single use* - accepting or withdrawing clears the stored hash, and the row is
  locked while it is redeemed, so two browsers presenting it at once produce one
  activation and one refusal;
- *expiring* - ``expires_at``, days not minutes (a person may read the email
  tomorrow), refreshed only by sending a new one;
- *bound* to one user and one organization - the hash names exactly one row, and
  the row names both, so a link cannot activate another organization or be used
  by another person;
- *never stored or logged* - only its SHA-256 is at rest; the token travels in
  the sealed notification context and the URL **fragment** (which browsers never
  send to a server), and in the POST body of the two public calls. It appears in
  no URL the server sees, no log line, no audit entry and no API response.

**Who may do what.** The person holding a valid link may preview and claim. A
signed-in visitor must be the invited person. A person whose address already has
a password must sign in as that account - holding a link, however valid, never
lets anyone set a password on an account that can already sign in. A pending
self-signup account for the address (which may have been registered by someone
else) is completed with the *invitee's* password, which is what defeats
pre-registration. Everything else is one uniform ``invitation_invalid``.

**Sending.** The partner that provisioned the customer (with the same dangerous
permission provisioning needs) and staff (partner management, dangerous) may send
the link again - which replaces it - or withdraw it. Both are audited.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.partners import access, selectors
from workeasy360.contracts.enums import OrganizationStatus, PrincipalKind, UserStatus
from workeasy360.contracts.errors import (
    InvitationEmailMismatch,
    InvitationInvalid,
    InvitationSignInRequired,
    MembershipValidationError,
    OrganizationNotWritable,
    PartnerRecordNotFound,
    PartnerValidationError,
)
from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

PROVISION = "platform.partner_customer.provision"
STAFF_MANAGE = "platform.staff_partner.manage"
TEMPLATE_KEY = "platform.customer.owner_invited"

_TOKEN_BYTES = 32
#: Longest token looked up; anything longer was never ours.
MAX_TOKEN_LENGTH = 200
OWNER_CLAIMS = "partner_owner_claims_total"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token(organization_id: UUID) -> str:
    return f"{organization_id.hex}.{secrets.token_urlsafe(_TOKEN_BYTES)}"


def _organization_of(token: str) -> UUID | None:
    """The organization a token *names*. Not a secret: it lets the redemption
    check that the link belongs to the organization it claims, and nothing
    about it is trusted - the hash decides whether anything is there."""
    head, sep, tail = token.strip().partition(".")
    if not sep or not tail or len(head) != 32:
        return None
    try:
        return UUID(hex=head)
    except ValueError:
        return None


def invite_ttl() -> timedelta:
    return timedelta(days=max(1, int(getattr(settings, "OWNER_INVITE_DAYS", 14))))


def _url(token: str) -> str:
    return f"{settings.W360_FRONTEND_URL}/owner-invitations/accept#token={token}"


def public_state(invite: Any, now: Any | None = None) -> dict[str, Any]:
    """The facts about an invitation a screen may show its provisioning partner
    or staff: where it stands, when it expires, how often it was sent. **Never
    the token, its hash or anything that could redeem it**, and no email."""
    from workeasy360.commercial.partners.domain import owner_activation

    moment = now or timezone.now()
    return {
        "activation": owner_activation(invite.status, invite.expires_at, moment),
        "expires_at": invite.expires_at.isoformat(),
        "resolved_at": invite.resolved_at.isoformat() if invite.resolved_at else None,
        "last_sent_at": invite.last_sent_at.isoformat() if invite.last_sent_at else None,
        "send_count": invite.send_count,
    }


# --- sending -----------------------------------------------------------------------------


def issue_and_send(invite: Any) -> None:
    """Mint a fresh link for ``invite``, store its hash and email it.

    The caller holds the row (locked, or just created) inside its transaction,
    so the row change and the queued email commit together or not at all. The
    previous link, if any, is replaced and stops working; the row returns to
    ``pending`` with a new expiry, which is how a resend revives an expired,
    withdrawn or declined invitation. Never called for an accepted one.
    """
    from workeasy360.platform.identity import selectors as identity
    from workeasy360.platform.notifications.services import notify_account
    from workeasy360.platform.organizations.selectors import organization_names

    now = timezone.now()
    token = _new_token(invite.organization_id)
    invite.token_hash = _hash(token)
    invite.status = "pending"
    invite.expires_at = now + invite_ttl()
    invite.resolved_at = None
    invite.revoked_at = None
    invite.send_count += 1
    invite.last_sent_at = now
    invite.save(
        update_fields=[
            "token_hash",
            "status",
            "expires_at",
            "resolved_at",
            "revoked_at",
            "send_count",
            "last_sent_at",
        ]
    )
    organization_name = organization_names([invite.organization_id]).get(
        invite.organization_id, ""
    )
    notify_account(
        template_key=TEMPLATE_KEY,
        recipient_email=identity.contact_address(invite.user_id),
        context={
            "organization_name": organization_name,
            "partner_name": invite.partner.display_name,
            "expires_on": invite.expires_at.strftime("%d %B %Y"),
        },
        sealed_context={"claim_url": _url(token)},
        idempotency_key=f"owner-invite:{invite.id}:{invite.send_count}",
    )
    metrics.increment("partner_owner_invitations_sent_total")


# --- redeeming ---------------------------------------------------------------------------------------


def _redeemable(token: Any, *, lock: bool) -> Any:
    """The pending, unexpired invitation this token opens - or ``InvitationInvalid``.

    One refusal for every reason (no such link, wrong organization, used,
    withdrawn, expired), so a failed attempt says nothing about which.
    """
    from workeasy360.commercial.partners.models import PartnerOwnerInvite

    raw = token if isinstance(token, str) else ""
    if not raw or len(raw) > MAX_TOKEN_LENGTH:
        raise InvitationInvalid("This link is not valid.")
    organization_id = _organization_of(raw)
    if organization_id is None:
        raise InvitationInvalid("This link is not valid.")
    rows = PartnerOwnerInvite.objects.filter(
        token_hash=_hash(raw), organization_id=organization_id
    ).select_related("user", "partner")
    if lock:
        rows = rows.select_for_update(of=("self",))
    invite = rows.first()
    if (
        invite is None
        or invite.status != "pending"
        or invite.expires_at <= timezone.now()
    ):
        raise InvitationInvalid("This link is not valid.")
    return invite


def _needs_name(user: Any) -> bool:
    return user.status != UserStatus.ACTIVE.value or not user.full_name


def preview(token: Any, *, signed_in_user: Any = None) -> dict[str, Any]:
    """What the activation page shows before the person commits.

    The link's holder is the mailbox's owner, so telling them whether the
    address already has a password is not an account oracle: they already hold
    proof of the address.
    """
    from workeasy360.platform.organizations.selectors import organization_lifecycle

    with transaction.atomic():
        invite = _redeemable(token, lock=False)
        organization = organization_lifecycle(invite.organization_id)
        user = invite.user
    if organization is None:
        raise InvitationInvalid("This link is not valid.")
    can_sign_in = user.status == UserStatus.ACTIVE.value and user.has_usable_password()
    return {
        "organization_name": organization["display_name"],
        "partner_name": invite.partner.display_name,
        "email": user.email_original or user.email,
        "expires_at": invite.expires_at.isoformat(),
        "account_exists": can_sign_in,
        "signed_in_as_invitee": bool(
            signed_in_user is not None and signed_in_user.pk == invite.user_id
        ),
        # A person named before they ever signed in has no name on record; and
        # a pending self-signup's name was chosen by whoever registered the
        # address, so the person who proved the mailbox is asked for their own.
        "needs_name": _needs_name(user),
    }


@dataclass(frozen=True, slots=True)
class Claimed:
    user: Any
    organization_id: UUID
    organization_slug: str
    #: A password was set by this claim (as opposed to an existing account that
    #: signed in), so the caller must establish the session.
    credentials_set: bool


def claim(
    token: Any,
    *,
    signed_in_user: Any = None,
    password: str | None = None,
    full_name: str | None = None,
) -> Claimed:
    """Redeem the link: the person becomes the organization's owner.

    One transaction. The invitation row is locked for its whole length, so a
    second browser presenting the same link waits, then finds it used and is
    refused - exactly one claim succeeds. Password policy is the platform's own
    (``check_new_password``); the hash is written by the identity module's
    lifecycle function, never here.
    """
    from workeasy360.platform.authz.control_plane import ensure_platform_organization
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.identity.account import check_new_password
    from workeasy360.platform.onboarding.services import activate_pending_owner
    from workeasy360.platform.organizations.selectors import organization_lifecycle
    from workeasy360.platform.tenancy.context import no_tenant
    from workeasy360.platform.tenancy.db import organization_db_scope

    ensure_platform_organization()
    with transaction.atomic():
        invite = _redeemable(token, lock=True)
        organization = organization_lifecycle(invite.organization_id)
        if organization is None:
            raise InvitationInvalid("This link is not valid.")
        status = OrganizationStatus(organization["status"])
        if not status.allows_write:
            raise OrganizationNotWritable(
                "This organization is not accepting changes at present.",
                organization_id=organization["organization_id"],
                status=status.value,
            )
        user = lifecycle.lock(invite.user_id)
        credentials_set = False
        if signed_in_user is not None:
            if signed_in_user.pk != user.pk:
                metrics.increment(OWNER_CLAIMS, outcome="wrong_user")
                raise InvitationEmailMismatch(
                    "This invitation was sent to a different account. Sign out and "
                    "open the link again."
                )
        else:
            if user.status not in (UserStatus.ACTIVE.value, UserStatus.PENDING.value):
                raise InvitationInvalid("This link is not valid.")
            # Only an *active* account can sign in. A pending one (a
            # self-service registration nobody verified) cannot, whoever chose
            # its password - so the link's holder, who has just proven the
            # mailbox, replaces it below rather than being told to sign in to
            # an account that will not let them.
            if user.status == UserStatus.ACTIVE.value and user.has_usable_password():
                metrics.increment(OWNER_CLAIMS, outcome="sign_in_required")
                raise InvitationSignInRequired(
                    "An account already exists for this address. Sign in to accept."
                )
            name = str(full_name or "").strip()[:255]
            if _needs_name(user) and not name:
                raise MembershipValidationError("Enter your name.", field="full_name")
            secret = password if isinstance(password, str) else ""
            check_new_password(secret, user=user)
            lifecycle.complete_with_password(user, secret, full_name=name, activate=True)
            credentials_set = True

        activate_pending_owner(user_id=user.pk, organization_id=invite.organization_id)
        invite.status = "accepted"
        invite.resolved_at = timezone.now()
        invite.token_hash = ""  # spent: nothing left that could match again
        invite.save(update_fields=["status", "resolved_at", "token_hash"])
        with no_tenant(), organization_db_scope(PLATFORM_ORGANIZATION_ID):
            audit.record_system(
                PLATFORM_ORGANIZATION_ID,
                "partner.owner_activated",
                resource_type="partners.owner_invite",
                resource_id=invite.id,
                changes={
                    "partner_id": str(invite.partner_id),
                    "organization_id": str(invite.organization_id),
                    # Not named for "credentials": the audit redactor blanks any
                    # such key, and this fact - a new account was completed, as
                    # against an existing one signing in - is worth keeping.
                    "account_completed": credentials_set,
                },
                reason="The named owner followed the emailed link and took ownership.",
            )
    metrics.increment(OWNER_CLAIMS, outcome="claimed")
    logger.info(
        "partner.owner_claimed",
        organization_id=str(invite.organization_id),
        credentials_set=credentials_set,
    )
    return Claimed(
        user=user,
        organization_id=invite.organization_id,
        organization_slug=organization["slug"],
        credentials_set=credentials_set,
    )


# --- resending and withdrawing ----------------------------------------------------------------------


def _authorize(
    ctx: TenantContext, organization_id: UUID, partner_id: UUID | None
) -> None:
    """The partner that provisioned the customer, or staff - never anyone else."""
    if ctx.principal is PrincipalKind.PARTNER:
        authz.require(ctx, PROVISION)
        # An *active* link with this exact customer, and an active partner.
        access.customer_context(ctx, organization_id, write=True)
    else:
        authz.require(ctx, STAFF_MANAGE)
        if partner_id is not None and selectors.partner_row(partner_id) is None:
            raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))


def _latest_invite_locked(
    ctx: TenantContext, organization_id: UUID, partner_id: UUID | None
) -> Any:
    from workeasy360.commercial.partners.models import PartnerOwnerInvite

    invite = (
        PartnerOwnerInvite.objects.select_for_update(of=("self",))
        .select_related("partner")
        .filter(organization_id=organization_id)
        .order_by("-created_at")
        .first()
    )
    expected = ctx.partner_id if ctx.principal is PrincipalKind.PARTNER else partner_id
    # One 404 for "no invitation", "not this partner's" and "not yours".
    if invite is None or (expected is not None and invite.partner_id != expected):
        raise PartnerRecordNotFound("No such invitation.")
    return invite


@in_scope
def resend(
    ctx: TenantContext, organization_id: UUID, *, partner_id: UUID | None = None
) -> Any:
    """Send the owner a new activation link. The old one stops working and the
    new one expires after the usual period. Refused once the owner has activated."""
    _authorize(ctx, organization_id, partner_id)
    with transaction.atomic():
        invite = _latest_invite_locked(ctx, organization_id, partner_id)
        if invite.status == "accepted":
            raise PartnerValidationError(
                "The owner has already activated this account.", field="organization_id"
            )
        issue_and_send(invite)
        audit.record(
            ctx,
            "partner.owner_invitation_sent",
            resource_type="partners.owner_invite",
            resource_id=invite.id,
            changes={
                "partner_id": str(invite.partner_id),
                "organization_id": str(organization_id),
                "send_count": invite.send_count,
            },
        )
    return invite


@in_scope
def revoke(
    ctx: TenantContext, organization_id: UUID, *, partner_id: UUID | None = None
) -> Any:
    """Withdraw the activation link. It stops working at once; the customer
    stays as it is, and a later resend can offer it again."""
    _authorize(ctx, organization_id, partner_id)
    with transaction.atomic():
        invite = _latest_invite_locked(ctx, organization_id, partner_id)
        if invite.status == "accepted":
            raise PartnerValidationError(
                "The owner has already activated this account.", field="organization_id"
            )
        if invite.status == "revoked":
            return invite  # already withdrawn: the same outcome
        invite.status = "revoked"
        invite.revoked_at = timezone.now()
        invite.token_hash = ""
        invite.save(update_fields=["status", "revoked_at", "token_hash"])
        audit.record(
            ctx,
            "partner.owner_invitation_revoked",
            resource_type="partners.owner_invite",
            resource_id=invite.id,
            changes={
                "partner_id": str(invite.partner_id),
                "organization_id": str(organization_id),
            },
        )
    return invite
