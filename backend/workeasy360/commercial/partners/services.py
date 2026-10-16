"""Partner administration - the staff side of the channel (Phase 7).

Every function is a use case: it authorizes through ``authz.require`` (which,
for a ``STAFF`` context, resolves from explicit ``platform.staff_*`` grants and
demands AAL 2 for each ``dangerous`` key), validates **before its first
write** (D-016), owns its transaction, audits in the one audit log and
publishes to the outbox in that same transaction.

They run in the platform-organization scope a control-plane request opens
(``authz.control_plane.acting_scope``). Actions that concern a specific
customer additionally enter *that* organization's scope to write a
customer-visible audit entry, which is why "who was given access to my
account, and by whom" is answerable from the customer's own audit log.

Concurrency: a partner's row is locked (``SELECT ... FOR UPDATE``) for every
status change and capability change, so two staff members racing on one
partner serialise instead of interleaving, and the ``version`` token turns a
stale read into an error rather than a silent overwrite (Task 60).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.partners import capabilities, selectors
from workeasy360.commercial.partners.domain import (
    assert_link_transition,
    assert_partner_transition,
    clean_reason,
    clean_text,
    validate_partner_code,
)
from workeasy360.contracts.enums import (
    GrantStatus,
    PartnerLinkStatus,
    PartnerStatus,
    PriceBookChannel,
    PrincipalKind,
)
from workeasy360.contracts.errors import (
    CommercialError,
    PartnerNotActive,
    PartnerRecordNotFound,
    PartnerValidationError,
)
from workeasy360.contracts.events import partner_events as schemas
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events import services as events
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope, organization_scope
from workeasy360.platform.identity import portals as portal_links
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MANAGE = "platform.staff_partner.manage"
MEMBER_MANAGE = "platform.partner_member.manage"

PARTNER_OPERATIONS = "partner_operations_total"


class ConcurrentPartnerModification(CommercialError):
    """The row changed between the caller reading it and writing it."""

    code = "concurrent_modification"


def _lock_partner(partner_id: UUID, *, expected_version: int | None = None) -> Any:
    """Row-lock a partner and check the optimistic token. Both, not either."""
    from workeasy360.commercial.partners.models import PartnerAccount

    partner = PartnerAccount.objects.select_for_update().filter(id=partner_id).first()
    if partner is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    if expected_version is not None and partner.version != expected_version:
        raise ConcurrentPartnerModification(
            f"Partner was at version {partner.version}, not {expected_version}. "
            f"Re-read it and retry.",
            expected_version=expected_version,
            actual_version=partner.version,
        )
    return partner


def _bump(partner: Any, fields: list[str]) -> None:
    partner.version += 1
    partner.save(update_fields=[*fields, "version", "updated_at"])


def _publish(
    ctx: TenantContext, schema: Any, aggregate_id: Any, payload: dict[str, Any]
) -> None:
    events.publish(
        events.build_event(
            ctx,
            schema.event_type,
            1,
            aggregate_type=schema.aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
        )
    )


# --- partners -------------------------------------------------------------


@in_scope
def create_partner(
    ctx: TenantContext,
    *,
    code: Any,
    display_name: Any,
    payout_currency: Any,
    legal_name: Any = "",
    contact_name: Any = "",
    contact_email: Any = "",
) -> Any:
    """Create a partner in ``draft``. It can do nothing until activated."""
    from workeasy360.commercial.partners.models import PartnerAccount

    authz.require(ctx, MANAGE)
    clean_code = validate_partner_code(code)
    name = clean_text(display_name, field="display_name", max_length=120)
    legal = clean_text(legal_name, field="legal_name", max_length=200, required=False)
    contact = clean_text(
        contact_name, field="contact_name", max_length=120, required=False
    )
    email = _clean_email(contact_email, required=False)
    currency = parse_currency(payout_currency)

    with transaction.atomic():
        try:
            with transaction.atomic():
                partner = PartnerAccount.objects.create(
                    code=clean_code,
                    display_name=name,
                    legal_name=legal,
                    contact_name=contact,
                    contact_email=email,
                    payout_currency=currency.value,
                    created_by_id=ctx.user_id,
                )
        except IntegrityError as exc:
            raise PartnerValidationError(
                f"Partner code {clean_code!r} is already in use.", field="code"
            ) from exc
        audit.record(
            ctx,
            "partner.created",
            resource_type="partners.partner",
            resource_id=partner.id,
            # No contact details: the audit trail is retained and widely read.
            changes={"code": partner.code, "payout_currency": partner.payout_currency},
        )
    metrics.increment(PARTNER_OPERATIONS, operation="create", outcome="ok")
    return partner


@in_scope
def update_partner(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    expected_version: int,
    display_name: Any = None,
    legal_name: Any = None,
    contact_name: Any = None,
    contact_email: Any = None,
) -> Any:
    """Change shared identity fields. ``expected_version`` is required: an
    edit made against a stale read is refused, never merged silently."""
    authz.require(ctx, MANAGE)
    changes: dict[str, Any] = {}
    if display_name is not None:
        changes["display_name"] = clean_text(
            display_name, field="display_name", max_length=120
        )
    if legal_name is not None:
        changes["legal_name"] = clean_text(
            legal_name, field="legal_name", max_length=200, required=False
        )
    if contact_name is not None:
        changes["contact_name"] = clean_text(
            contact_name, field="contact_name", max_length=120, required=False
        )
    if contact_email is not None:
        changes["contact_email"] = _clean_email(contact_email, required=False)
    if not changes:
        raise PartnerValidationError("Nothing to change.", field="body")

    with transaction.atomic():
        partner = _lock_partner(partner_id, expected_version=expected_version)
        for name, value in changes.items():
            setattr(partner, name, value)
        _bump(partner, list(changes))
        _mirror_primary_contact(partner, changes)
        audit.record(
            ctx,
            "partner.updated",
            resource_type="partners.partner",
            resource_id=partner.id,
            # Which fields changed, never their values (contact data is PII).
            changes={"fields": sorted(changes)},
        )
    return partner


def _mirror_primary_contact(partner: Any, changes: dict[str, Any]) -> None:
    """The account's contact name and email *are* the primary contact (see
    ``PartnerContact``); changing them here changes the contact row too, so the
    two can never disagree."""
    from workeasy360.commercial.partners.models import PartnerContact

    fields = {
        column: changes[key]
        for key, column in (("contact_name", "name"), ("contact_email", "email"))
        if key in changes
    }
    if fields:
        PartnerContact.objects.filter(partner_id=partner.id, kind="primary").update(
            **fields
        )


@in_scope
def set_partner_status(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    target: PartnerStatus,
    reason: Any,
    expected_version: int | None = None,
) -> Any:
    """Move a partner through its lifecycle.

    ``terminated`` additionally ends every live customer relationship and
    disables every custom domain **in the same transaction**, and touches
    nothing of any customer: their organization, subscription, entitlements
    and members are exactly as they were (Task 28). Suspension changes no rows
    apart from the partner's own status - what it takes away is the ability to
    act, which the permission resolver reads from that status.
    """
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)

    with transaction.atomic():
        partner = _lock_partner(partner_id, expected_version=expected_version)
        current = partner.status_enum
        assert_partner_transition(current, target)
        if current is target:
            return partner

        if target is PartnerStatus.ACTIVE and not _has_admin_member(partner.id):
            raise PartnerValidationError(
                "Add at least one administrator before activating the partner: "
                "an active partner nobody can act for is a misconfiguration.",
                field="members",
            )

        now = timezone.now()
        partner.status = target.value
        partner.status_reason = why
        fields = ["status", "status_reason"]
        stamp = {
            PartnerStatus.ACTIVE: "activated_at",
            PartnerStatus.SUSPENDED: "suspended_at",
            PartnerStatus.TERMINATED: "terminated_at",
        }[target]
        setattr(partner, stamp, now)
        fields.append(stamp)
        _bump(partner, fields)

        links_ended = 0
        if target is PartnerStatus.TERMINATED:
            links_ended = _end_all_links(ctx, partner, reason_code="partner_terminated")
            _disable_all_domains(partner)
        if target is not PartnerStatus.ACTIVE:
            # A suspended partner's custom domain stops serving its branding on
            # the next lookup, not a cache lifetime later.
            _refresh_public_hosts(partner.id)
        invitations_sent = 0
        if target is PartnerStatus.ACTIVE and current is PartnerStatus.DRAFT:
            # People named while the partner was a draft were not emailed
            # (``invite_member(send_link=False)``): activation is the moment
            # they are invited. Only first activation - a reactivation after a
            # suspension has nobody left to invite.
            invitations_sent = _send_deferred_invitations(partner)

        payload = {"partner_id": str(partner.id), "from_status": current.value}
        if target is PartnerStatus.ACTIVE:
            _publish(ctx, schemas.PARTNER_ACTIVATED_V1, partner.id, payload)
        elif target is PartnerStatus.SUSPENDED:
            _publish(ctx, schemas.PARTNER_SUSPENDED_V1, partner.id, payload)
        else:
            _publish(
                ctx,
                schemas.PARTNER_TERMINATED_V1,
                partner.id,
                {**payload, "links_ended": links_ended},
            )
        audit.record(
            ctx,
            f"partner.{target.value}",
            resource_type="partners.partner",
            resource_id=partner.id,
            changes={
                "from": current.value,
                "to": target.value,
                "links_ended": links_ended,
                **({"invitations_sent": invitations_sent} if invitations_sent else {}),
            },
            reason=why,
        )
    metrics.increment(PARTNER_OPERATIONS, operation=target.value, outcome="ok")
    return partner


def _send_deferred_invitations(partner: Any) -> int:
    """Email a setup link to each active member whose invitation was deferred
    (``invite_member(send_link=False)``) and who still has no password. Returns
    how many were sent. Members attached any other way are never touched."""
    from workeasy360.commercial.partners.models import PartnerMember

    sent = 0
    for member in (
        PartnerMember.objects.select_for_update()
        .filter(
            partner_id=partner.id,
            status="active",
            invitation_deferred=True,
            invitation_sent_at__isnull=True,
        )
        .select_related("user")
    ):
        member.invitation_deferred = False
        if _send_setup_link(partner, member, member.user_id):
            member.invitation_sent_at = timezone.now()
            sent += 1
        member.save(update_fields=["invitation_deferred", "invitation_sent_at"])
    return sent


def _refresh_public_hosts(partner_id: UUID) -> None:
    """Drop the partner's hosts from the public branding cache, now and again
    when the transaction commits (a lookup racing the commit could otherwise
    re-cache the state being replaced)."""
    from workeasy360.commercial.partners import domains

    domains.invalidate_partner_hosts(partner_id)
    transaction.on_commit(lambda: domains.invalidate_partner_hosts(partner_id))


def _has_admin_member(partner_id: UUID) -> bool:
    from workeasy360.commercial.partners.models import PartnerMember

    return PartnerMember.objects.filter(
        partner_id=partner_id,
        status="active",
        role__in=sorted(capabilities.ADMIN_ROLES),
        user__status="active",
    ).exists()


def _end_all_links(ctx: TenantContext, partner: Any, *, reason_code: str) -> int:
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    links = list(
        PartnerOrganizationLink.objects.select_for_update()
        .filter(partner_id=partner.id)
        .exclude(status=PartnerLinkStatus.ENDED.value)
        .order_by("id")
    )
    for link in links:
        _end_link_row(ctx, link, reason_code=reason_code)
    return len(links)


def _end_link_row(ctx: TenantContext, link: Any, *, reason_code: str) -> None:
    """End one relationship and record it where the customer can see it."""
    assert_link_transition(link.status_enum, PartnerLinkStatus.ENDED)
    link.status = PartnerLinkStatus.ENDED.value
    link.ended_at = timezone.now()
    link.ended_by_id = ctx.user_id
    link.end_reason = reason_code
    link.version += 1
    link.save(update_fields=["status", "ended_at", "ended_by", "end_reason", "version"])
    _publish(
        ctx,
        schemas.PARTNER_CUSTOMER_UNLINKED_V1,
        link.id,
        {
            "partner_id": str(link.partner_id),
            "organization_id": str(link.organization_id),
            "link_id": str(link.id),
            "reason_code": reason_code,
        },
    )
    with organization_scope(ctx, link.organization_id) as customer:
        audit.record(
            customer,
            "partner.relationship_ended",
            resource_type="partners.link",
            resource_id=link.id,
            changes={"reason_code": reason_code},
            reason=f"Partner relationship ended: {reason_code}.",
        )


def _disable_all_domains(partner: Any) -> None:
    from workeasy360.commercial.partners.models import PartnerDomain
    from workeasy360.contracts.enums import DomainStatus

    now = timezone.now()
    for domain in PartnerDomain.objects.select_for_update().filter(
        partner_id=partner.id,
        status__in=[
            DomainStatus.REQUESTED.value,
            DomainStatus.VERIFICATION_PENDING.value,
            DomainStatus.VERIFIED.value,
            DomainStatus.ACTIVE.value,
        ],
    ):
        domain.status = DomainStatus.DISABLED.value
        domain.disabled_at = now
        domain.version += 1
        domain.save(update_fields=["status", "disabled_at", "version", "updated_at"])


# --- capabilities ---------------------------------------------------------


@in_scope
def grant_capability(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    capability: Any,
    reason: Any,
    expires_at: datetime | None = None,
) -> Any:
    """Grant one capability (staff). Idempotent: see ``_grant_capability``."""
    authz.require(ctx, MANAGE)
    return _grant_capability(
        ctx, partner_id, capability=capability, reason=reason, expires_at=expires_at
    )


def _grant_capability(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    capability: Any,
    reason: Any,
    expires_at: datetime | None = None,
) -> Any:
    """Grant one capability. Idempotent: an existing active grant is returned."""
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    spec = capabilities.require_capability(str(capability))
    why = clean_reason(reason)
    if expires_at is not None and expires_at <= timezone.now():
        raise PartnerValidationError(
            "expires_at must be in the future.", field="expires_at"
        )

    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if partner.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot be granted anything.", field="partner_id"
            )
        existing = (
            PartnerCapabilityGrant.objects.select_for_update()
            .filter(
                partner_id=partner_id,
                capability=spec.key,
                status=GrantStatus.ACTIVE.value,
            )
            .first()
        )
        if existing is not None:
            if existing.expires_at is None or existing.expires_at > timezone.now():
                return existing
            # An expired grant still holds the one "active" slot the unique
            # index allows, and its terms are frozen. Returning it made a
            # re-grant a silent no-op; it is closed (never deleted - history
            # of what the partner could do stays answerable) and replaced.
            existing.status = GrantStatus.REVOKED.value
            existing.revoked_at = timezone.now()
            existing.revoked_by_id = ctx.user_id
            existing.revoke_reason = "Expired; superseded by a new grant"
            existing.save(
                update_fields=["status", "revoked_at", "revoked_by", "revoke_reason"]
            )
        grant = PartnerCapabilityGrant.objects.create(
            partner_id=partner_id,
            capability=spec.key,
            granted_by_id=ctx.user_id,
            granted_at=timezone.now(),
            expires_at=expires_at,
            reason=why,
        )
        _publish(
            ctx,
            schemas.PARTNER_CAPABILITY_GRANTED_V1,
            partner_id,
            {"partner_id": str(partner_id), "capability": spec.key},
        )
        audit.record(
            ctx,
            "partner.capability_granted",
            resource_type="partners.capability_grant",
            resource_id=grant.id,
            changes={"partner_id": str(partner_id), "capability": spec.key},
            reason=why,
        )
    metrics.increment(PARTNER_OPERATIONS, operation="capability_grant", outcome="ok")
    return grant


@in_scope
def revoke_capability(
    ctx: TenantContext, partner_id: UUID, *, capability: Any, reason: Any
) -> Any:
    """Revoke a capability (staff): see ``_revoke_capability``."""
    authz.require(ctx, MANAGE)
    return _revoke_capability(ctx, partner_id, capability=capability, reason=reason)


def _revoke_capability(
    ctx: TenantContext, partner_id: UUID, *, capability: Any, reason: Any
) -> Any:
    """Revoke a capability, effective on the partner's very next action.

    The permission set is computed from these rows on every check with no
    cache in between, so there is no window in which a revoked capability
    still works. Historical records that depended on it are untouched.
    """
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    spec = capabilities.require_capability(str(capability))
    why = clean_reason(reason)

    with transaction.atomic():
        _lock_partner(partner_id)
        grant = PartnerCapabilityGrant.objects.filter(
            partner_id=partner_id, capability=spec.key, status=GrantStatus.ACTIVE.value
        ).first()
        if grant is None:
            raise PartnerRecordNotFound(
                "That capability is not currently granted.", capability=spec.key
            )
        grant.status = GrantStatus.REVOKED.value
        grant.revoked_at = timezone.now()
        grant.revoked_by_id = ctx.user_id
        grant.revoke_reason = why
        grant.save(update_fields=["status", "revoked_at", "revoked_by", "revoke_reason"])
        _publish(
            ctx,
            schemas.PARTNER_CAPABILITY_REVOKED_V1,
            partner_id,
            {"partner_id": str(partner_id), "capability": spec.key},
        )
        audit.record(
            ctx,
            "partner.capability_revoked",
            resource_type="partners.capability_grant",
            resource_id=grant.id,
            changes={"partner_id": str(partner_id), "capability": spec.key},
            reason=why,
        )
        if spec.key in (capabilities.BRANDING_MANAGE, capabilities.DOMAIN_MANAGE):
            _refresh_public_hosts(partner_id)
    metrics.increment(PARTNER_OPERATIONS, operation="capability_revoke", outcome="ok")
    return grant


@in_scope
def enable_white_label(ctx: TenantContext, partner_id: UUID, *, reason: Any) -> list[str]:
    """Give a partner the white-label pair, ``branding.manage`` and
    ``domain.manage``, in one step. Returns the capabilities newly granted.

    White-label is a *label the pair earns*, not a kind of account, so this is
    only two ordinary capability grants made together: the same command, the
    same second factor and the same audit entry each as granting them one at a
    time - and nothing a partner could not be given by hand. Idempotent.
    """
    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    pair = (capabilities.BRANDING_MANAGE, capabilities.DOMAIN_MANAGE)
    granted: list[str] = []
    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if partner.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot be granted anything.", field="partner_id"
            )
        held = selectors.active_capabilities(partner_id)
        for key in pair:
            if key not in held:
                grant_capability(ctx, partner_id, capability=key, reason=why)
                granted.append(key)
        if granted:
            audit.record(
                ctx,
                "partner.white_label_enabled",
                resource_type="partners.partner",
                resource_id=partner_id,
                changes={"partner_id": str(partner_id), "capabilities": granted},
                reason=why,
            )
    return granted


# --- members --------------------------------------------------------------


def _authorize_members(
    ctx: TenantContext, partner_id: UUID, *, target_role: str | None = None
) -> bool:
    """Authorize a team command; ``True`` when the caller is the partner itself.

    One command, two callers, no second implementation (the pattern
    ``access.subject_partner`` uses for brand and domains):

    - **staff** need ``platform.staff_partner.manage`` (dangerous: AAL 2);
    - a **partner user** acts for their own partner only - an explicit
      ``partner_id`` that is not theirs is the same 404 as any other partner's
      record - needs ``platform.partner_member.manage`` (also dangerous: a
      stolen session must not be able to add a stranger) and an *active*
      partner, and only an owner may hand out the owner role.
    """
    if ctx.principal is PrincipalKind.PARTNER:
        if partner_id != ctx.partner_id:
            raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
        authz.require(ctx, MEMBER_MANAGE)
        partner = selectors.partner_row(partner_id)
        if partner is None or not partner.status_enum.may_transact:
            raise PartnerNotActive(
                "This partner may not change its team.",
                partner_status=partner.status if partner else None,
            )
        if target_role == "owner" and not _caller_is_owner(ctx):
            raise PartnerValidationError(
                "Only an owner can give the owner role.", field="role"
            )
        return True
    authz.require(ctx, MANAGE)
    return False


def _caller_is_owner(ctx: TenantContext) -> bool:
    member = selectors.member_of(ctx.partner_id, ctx.user_id)
    return member is not None and member.role == "owner"


def _other_admin_exists(partner_id: UUID, excluding_member_id: UUID) -> bool:
    from workeasy360.commercial.partners.models import PartnerMember

    return (
        PartnerMember.objects.filter(
            partner_id=partner_id,
            status="active",
            role__in=sorted(capabilities.ADMIN_ROLES),
            user__status="active",
        )
        .exclude(id=excluding_member_id)
        .exists()
    )


@in_scope
def change_member_role(
    ctx: TenantContext, partner_id: UUID, *, member_id: UUID, role: Any
) -> Any:
    """Move a partner user to another role preset.

    Takes effect on their next request - permissions are computed from the row
    on every check. An *active* partner must keep one administrator, so the last
    one cannot be turned into a non-administrator role (the same rule that stops
    activating a partner nobody can act for). A partner user cannot change their
    own role, and only an owner can change an owner's.
    """
    from workeasy360.commercial.partners.models import PartnerMember

    target = _parse_role(role)
    by_partner = _authorize_members(ctx, partner_id, target_role=target.value)
    with transaction.atomic():
        partner = _lock_partner(partner_id)
        member = (
            PartnerMember.objects.select_for_update()
            .filter(id=member_id, partner_id=partner_id, status="active")
            .first()
        )
        if member is None:
            raise PartnerRecordNotFound("No such member.", member_id=str(member_id))
        if by_partner:
            if member.user_id == ctx.user_id:
                raise PartnerValidationError(
                    "You cannot change your own role.", field="role"
                )
            if member.role == "owner" and not _caller_is_owner(ctx):
                raise PartnerValidationError(
                    "Only an owner can change an owner's role.", field="role"
                )
        if member.role == target.value:
            return member
        if (
            capabilities.is_admin_role(member.role)
            and not capabilities.is_admin_role(target.value)
            and partner.status_enum is PartnerStatus.ACTIVE
            and not _other_admin_exists(partner_id, member.id)
        ):
            raise PartnerValidationError(
                "An active partner needs at least one administrator. Add another first.",
                field="role",
            )
        before = member.role
        member.role = target.value
        member.save(update_fields=["role"])
        audit.record(
            ctx,
            "partner.member_role_changed",
            resource_type="partners.member",
            resource_id=member.id,
            changes={"partner_id": str(partner_id), "from": before, "to": target.value},
        )
    return member


@in_scope
def add_member(ctx: TenantContext, partner_id: UUID, *, email: Any, role: Any) -> Any:
    """Attach a Workeasy360 identity to a partner.

    The identity is looked up by address and must already exist and be
    active; nothing is created. One uniform refusal covers "no such account",
    "disabled" and "already a member", so the endpoint is not an account
    directory.
    """
    from workeasy360.commercial.partners.models import PartnerMember
    from workeasy360.platform.identity.selectors import active_user_id_for_email

    authz.require(ctx, MANAGE)
    member_role = _parse_role(role)
    address = _clean_email(email, required=True)

    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if partner.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot take on members.", field="partner_id"
            )
        user_id = active_user_id_for_email(address)
        if user_id is None:
            raise PartnerValidationError("That account cannot be added.", field="email")
        try:
            with transaction.atomic():
                member = PartnerMember.objects.create(
                    partner_id=partner_id,
                    user_id=user_id,
                    role=member_role.value,
                    added_by_id=ctx.user_id,
                    added_at=timezone.now(),
                )
        except IntegrityError as exc:
            raise PartnerValidationError(
                "That account cannot be added.", field="email"
            ) from exc
        audit.record(
            ctx,
            "partner.member_added",
            resource_type="partners.member",
            resource_id=member.id,
            changes={"partner_id": str(partner_id), "role": member.role},
        )
    return member


@in_scope
def remove_member(ctx: TenantContext, partner_id: UUID, *, member_id: UUID) -> Any:
    """Remove a partner user. Partner access ends on their next request; their
    membership of any customer organization is a different table and is
    untouched (Task 65). A partner user cannot remove themselves, only an owner
    removes an owner, and an active partner keeps one administrator."""
    from workeasy360.commercial.partners.models import PartnerMember

    by_partner = _authorize_members(ctx, partner_id)
    with transaction.atomic():
        partner = _lock_partner(partner_id)
        member = (
            PartnerMember.objects.select_for_update()
            .filter(id=member_id, partner_id=partner_id, status="active")
            .first()
        )
        if member is None:
            raise PartnerRecordNotFound("No such member.", member_id=str(member_id))
        if by_partner:
            if member.user_id == ctx.user_id:
                raise PartnerValidationError(
                    "You cannot remove yourself. Ask another administrator.",
                    field="member",
                )
            if member.role == "owner" and not _caller_is_owner(ctx):
                raise PartnerValidationError(
                    "Only an owner can remove an owner.", field="member"
                )
            # A partner cannot lock itself out. (Staff keep the older, wider
            # power to remove anyone, including the last administrator.)
            if (
                capabilities.is_admin_role(member.role)
                and partner.status_enum is PartnerStatus.ACTIVE
                and not _other_admin_exists(partner_id, member.id)
            ):
                raise PartnerValidationError(
                    "An active partner needs at least one administrator. "
                    "Add another first.",
                    field="member",
                )
        member.status = "removed"
        member.removed_at = timezone.now()
        member.removed_by_id = ctx.user_id
        member.save(update_fields=["status", "removed_at", "removed_by"])
        audit.record(
            ctx,
            "partner.member_removed",
            resource_type="partners.member",
            resource_id=member.id,
            changes={"partner_id": str(partner_id)},
        )
    return member


@dataclass(frozen=True, slots=True)
class MemberInvite:
    member: Any
    #: The address had no account, so one was created (uncredentialed).
    account_created: bool
    #: A one-time link that sets the first password was emailed.
    setup_link_sent: bool


@in_scope
def invite_member(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    email: Any,
    role: Any,
    full_name: Any = "",
    send_link: bool = True,
) -> MemberInvite:
    """Attach a person to a partner (staff, or the partner's own administrator):
    see ``_invite_member``."""
    _authorize_members(ctx, partner_id, target_role=_parse_role(role).value)
    return _invite_member(
        ctx, partner_id, email=email, role=role, full_name=full_name, send_link=send_link
    )


def _invite_member(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    email: Any,
    role: Any,
    full_name: Any = "",
    send_link: bool = True,
) -> MemberInvite:
    """Attach a person to a partner - creating their account if the address has none.

    The Phase 10 counterpart of :func:`add_member` for a person who has never
    signed in. It uses the existing identity path unchanged: the identity is
    created uncredentialed (``ensure_identity_for_email``), the person becomes a
    ``PartnerMember`` exactly as before, and an existing one-time
    ``AccountToken`` link - the same mechanism as a forgotten-password link -
    lets them set their first password. Nothing is granted to an uncredentialed
    account that cannot sign in, and no second identity or credential store
    exists.

    Uniform refusal, like ``add_member``: "disabled", "already a member" and
    "cannot be assigned" are one message, so the endpoint is not an account
    directory. An account that already has a password is attached without
    sending anything.

    ``full_name`` names a person whose account this call creates (it never
    renames an existing one). ``send_link=False`` attaches the person without
    emailing them - used while a partner is still a draft, whose administrator
    is invited when it is activated (``set_partner_status``).
    """
    from workeasy360.commercial.partners.models import PartnerMember
    from workeasy360.platform.identity.services import ensure_identity_for_email

    member_role = _parse_role(role)
    address = _clean_email(email, required=True)
    person = clean_text(full_name, field="full_name", max_length=255, required=False)

    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if partner.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot take on members.", field="partner_id"
            )
        user_id, created = ensure_identity_for_email(address, full_name=person)
        if user_id is None:
            raise PartnerValidationError("That account cannot be added.", field="email")
        already = (
            PartnerMember.objects.select_for_update()
            .filter(partner_id=partner_id, user_id=user_id, status="active")
            .first()
        )
        if already is not None:
            if already.role == member_role.value:
                # A retry after a lost response (the Add Reseller wizard resumes
                # its steps): the person is already attached in this role, so
                # this is the same success, and no second email goes out.
                return MemberInvite(
                    member=already, account_created=False, setup_link_sent=False
                )
            raise PartnerValidationError("That account cannot be added.", field="email")
        try:
            with transaction.atomic():
                member = PartnerMember.objects.create(
                    partner_id=partner_id,
                    user_id=user_id,
                    role=member_role.value,
                    added_by_id=ctx.user_id,
                    added_at=timezone.now(),
                )
        except IntegrityError as exc:
            raise PartnerValidationError(
                "That account cannot be added.", field="email"
            ) from exc
        sent = _send_setup_link(partner, member, user_id) if send_link else False
        if sent:
            member.invitation_sent_at = timezone.now()
            member.save(update_fields=["invitation_sent_at"])
        elif not send_link and not _has_password(user_id):
            member.invitation_deferred = True
            member.save(update_fields=["invitation_deferred"])
        audit.record(
            ctx,
            "partner.member_invited",
            resource_type="partners.member",
            resource_id=member.id,
            changes={
                "partner_id": str(partner_id),
                "role": member.role,
                "account_created": created,
                "setup_link_sent": sent,
                "setup_link_deferred": not send_link,
            },
        )
    metrics.increment(PARTNER_OPERATIONS, operation="member_invite", outcome="ok")
    return MemberInvite(member=member, account_created=created, setup_link_sent=sent)


@in_scope
def resend_member_setup(ctx: TenantContext, partner_id: UUID, *, member_id: UUID) -> bool:
    """Send an active member who has no password a fresh setup link (the old
    one stops working). ``False`` - and nothing sent - when they already have a
    password: a person who can sign in has no use for one."""
    from workeasy360.commercial.partners.models import PartnerMember

    _authorize_members(ctx, partner_id)
    with transaction.atomic():
        partner = _lock_partner(partner_id)
        member = (
            PartnerMember.objects.select_for_update()
            .filter(id=member_id, partner_id=partner_id, status="active")
            .first()
        )
        if member is None:
            raise PartnerRecordNotFound("No such member.", member_id=str(member_id))
        sent = _send_setup_link(partner, member, member.user_id)
        if sent:
            member.invitation_sent_at = timezone.now()
            member.invitation_deferred = False
            member.save(update_fields=["invitation_sent_at", "invitation_deferred"])
        audit.record(
            ctx,
            "partner.member_setup_resent",
            resource_type="partners.member",
            resource_id=member.id,
            changes={"partner_id": str(partner_id), "setup_link_sent": sent},
        )
    return sent


def _has_password(user_id: UUID) -> bool:
    from workeasy360.platform.identity import selectors as identity

    return bool(identity.has_usable_password(user_id))


def _parse_role(role: Any) -> capabilities.RoleSpec:
    return capabilities.require_role(role)


#: The wording of the invitation email. Kept as it was for the two original
#: roles (an existing template and tests pin it); the presets use their label.
_ROLE_NAMES = {
    "admin": "Administrator",
    "viewer": "Viewer",
    **{
        key: spec.label
        for key, spec in capabilities.ROLE_PRESETS.items()
        if key not in {"admin", "viewer"}
    },
}


def _send_setup_link(partner: Any, member: Any, user_id: UUID) -> bool:
    """Email the one-time password-setup link to a member with no password.

    The token is minted by ``identity.tokens`` (only its SHA-256 is stored,
    single use, expiring, the latest one retires the earlier) and travels only
    in the *sealed* context of the notification, in the URL fragment - never in
    a log, an audit entry or a table a screen reads.
    """
    import hashlib

    from django.conf import settings

    from workeasy360.platform.identity import selectors as identity
    from workeasy360.platform.identity import tokens
    from workeasy360.platform.identity.models import AccountToken
    from workeasy360.platform.notifications.services import notify_account

    if identity.has_usable_password(user_id):
        return False
    days = max(1, int(settings.PARTNER_ADMIN_INVITE_DAYS))
    token = tokens.issue(
        user_id, AccountToken.Purpose.RESET_PASSWORD, ttl=timedelta(days=days)
    )
    notify_account(
        template_key="platform.partner.member_invited",
        recipient_email=identity.contact_address(user_id),
        context={
            "partner_name": partner.display_name,
            "role_name": _ROLE_NAMES.get(member.role, member.role),
            "expires_in": f"{days} day" + ("" if days == 1 else "s"),
        },
        sealed_context={
            "setup_url": f"{portal_links.auth_url()}/reset-password#token={token}"
        },
        idempotency_key=(
            f"partner-member:{member.id}:setup:"
            f"{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
        ),
    )
    return True


# --- product scope --------------------------------------------------------


def _flag(value: Any, *, field: str) -> bool | None:
    """An optional true/false from a request: absent is ``None`` (leave as is);
    anything that is not a real boolean is refused, never coerced."""
    if value is None:
        return None
    if not isinstance(value, bool):
        raise PartnerValidationError(f"{field} must be true or false.", field=field)
    return value


@in_scope
def grant_product(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    product_code: Any,
    plan_code: Any = None,
    trial_allowed: Any = None,
    portal_visible: Any = None,
) -> Any:
    """Allow a partner to sell a product (staff): see ``_grant_product``."""
    authz.require(ctx, MANAGE)
    return _grant_product(
        ctx,
        partner_id,
        product_code=product_code,
        plan_code=plan_code,
        trial_allowed=trial_allowed,
        portal_visible=portal_visible,
    )


def _grant_product(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    product_code: Any,
    plan_code: Any = None,
    trial_allowed: Any = None,
    portal_visible: Any = None,
) -> Any:
    """Allow a partner to sell a product (or one plan of it). Idempotent.

    ``trial_allowed`` and ``portal_visible`` are the two per-grant choices
    (``PartnerProductGrant``); left out they default to ``True``. Granting
    again with a different choice changes it - one command, whether the
    product is new to the partner or not.
    """
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.partners.models import PartnerProductGrant

    trial = _flag(trial_allowed, field="trial_allowed")
    visible = _flag(portal_visible, field="portal_visible")
    product_id, plan_id = catalog.resolve_product_and_plan(
        str(product_code or ""), str(plan_code) if plan_code else None
    )

    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if partner.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot be granted anything.", field="partner_id"
            )
        existing = (
            PartnerProductGrant.objects.select_for_update()
            .filter(
                partner_id=partner_id,
                product_id=product_id,
                plan_id=plan_id,
                status=GrantStatus.ACTIVE.value,
            )
            .first()
        )
        if existing is not None:
            _apply_grant_flags(ctx, existing, trial, visible)
            return existing
        grant = PartnerProductGrant.objects.create(
            partner_id=partner_id,
            product_id=product_id,
            plan_id=plan_id,
            granted_by_id=ctx.user_id,
            granted_at=timezone.now(),
            trial_allowed=True if trial is None else trial,
            portal_visible=True if visible is None else visible,
        )
        audit.record(
            ctx,
            "partner.product_granted",
            resource_type="partners.product_grant",
            resource_id=grant.id,
            changes={
                "partner_id": str(partner_id),
                "product_code": str(product_code),
                "plan_code": str(plan_code) if plan_code else None,
                "trial_allowed": grant.trial_allowed,
                "portal_visible": grant.portal_visible,
            },
        )
    return grant


def _apply_grant_flags(
    ctx: TenantContext, grant: Any, trial: bool | None, visible: bool | None
) -> None:
    """Change a live grant's two choices, recording what changed. A no-op (and
    no audit entry) when nothing differs, so a retry is safe."""
    changed: dict[str, bool] = {}
    if trial is not None and trial != grant.trial_allowed:
        grant.trial_allowed = trial
        changed["trial_allowed"] = trial
    if visible is not None and visible != grant.portal_visible:
        grant.portal_visible = visible
        changed["portal_visible"] = visible
    if not changed:
        return
    grant.save(update_fields=list(changed))
    audit.record(
        ctx,
        "partner.product_grant_updated",
        resource_type="partners.product_grant",
        resource_id=grant.id,
        changes={"partner_id": str(grant.partner_id), **changed},
    )


@in_scope
def update_product_grant(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    grant_id: UUID,
    trial_allowed: Any = None,
    portal_visible: Any = None,
) -> Any:
    """Change whether this partner's customers may trial a product it sells and
    whether the product shows on its portal. Neither can create a grant, widen
    what a partner may sell or touch an entitlement."""
    from workeasy360.commercial.partners.models import PartnerProductGrant

    authz.require(ctx, MANAGE)
    trial = _flag(trial_allowed, field="trial_allowed")
    visible = _flag(portal_visible, field="portal_visible")
    if trial is None and visible is None:
        raise PartnerValidationError("Nothing to change.", field="body")
    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if partner.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot be changed.", field="partner_id"
            )
        grant = (
            PartnerProductGrant.objects.select_for_update()
            .filter(id=grant_id, partner_id=partner_id, status=GrantStatus.ACTIVE.value)
            .first()
        )
        if grant is None:
            raise PartnerRecordNotFound("No such product grant.", grant_id=str(grant_id))
        _apply_grant_flags(ctx, grant, trial, visible)
    return grant


@in_scope
def revoke_product_grant(ctx: TenantContext, partner_id: UUID, *, grant_id: UUID) -> Any:
    """Withdraw a product grant (staff): see ``_revoke_product_grant``."""
    authz.require(ctx, MANAGE)
    return _revoke_product_grant(ctx, partner_id, grant_id=grant_id)


def _revoke_product_grant(ctx: TenantContext, partner_id: UUID, *, grant_id: UUID) -> Any:
    from workeasy360.commercial.partners.models import PartnerProductGrant

    with transaction.atomic():
        _lock_partner(partner_id)
        grant = (
            PartnerProductGrant.objects.select_for_update()
            .filter(id=grant_id, partner_id=partner_id, status=GrantStatus.ACTIVE.value)
            .first()
        )
        if grant is None:
            raise PartnerRecordNotFound("No such product grant.", grant_id=str(grant_id))
        grant.status = GrantStatus.REVOKED.value
        grant.revoked_at = timezone.now()
        grant.revoked_by_id = ctx.user_id
        grant.save(update_fields=["status", "revoked_at", "revoked_by"])
        audit.record(
            ctx,
            "partner.product_grant_revoked",
            resource_type="partners.product_grant",
            resource_id=grant.id,
            changes={"partner_id": str(partner_id)},
        )
    return grant


def capability_catalog(ctx: TenantContext) -> list[dict[str, str]]:
    """Every capability a partner can be granted, for the grant form.

    The registry is code, not data: this is the vocabulary staff choose from,
    so a screen never hard-codes (and never drifts from) it.
    """
    authz.require(ctx, "platform.staff_partner.view")
    group_of = {
        key: group_key
        for group_key, _label, keys in capabilities.CAPABILITY_GROUPS
        for key in keys
    }
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "short_label": capabilities.SHORT_LABELS.get(spec.key, spec.label),
            "group": group_of.get(spec.key, ""),
        }
        for spec in capabilities.CAPABILITIES.values()
    ]


def capability_presentation(ctx: TenantContext) -> dict[str, Any]:
    """The capability vocabulary *and* how a form presents it: the grouped
    headings and the presets (Phase 10). Nothing here can be granted by being
    listed - it is the same registry, arranged for a person."""
    return {
        "results": capability_catalog(ctx),
        "groups": [
            {"key": key, "label": label, "capabilities": list(keys)}
            for key, label, keys in capabilities.CAPABILITY_GROUPS
        ],
        "presets": [
            {
                "key": preset["key"],
                "label": preset["label"],
                "description": preset["description"],
                "capabilities": list(preset["capabilities"]),  # type: ignore[call-overload]
            }
            for preset in capabilities.CAPABILITY_PRESETS
        ],
    }


# --- partner pricing ------------------------------------------------------


@in_scope
def create_partner_price_book(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    code: str,
    name: str,
    currency: CurrencyCode,
    priority: int = 50,
    valid_from: datetime | None = None,
) -> Any:
    """A price book that applies **only** when this partner is the one selling.

    The partner exists check lives here because pricing sits below partners
    and cannot look. Prices are published into the book through the ordinary
    pricing service, so a partner price is a price row like any other - never
    copied into a partner table - and immutable once published.
    """
    from workeasy360.commercial.pricing import services as pricing

    authz.require(ctx, "platform.staff_pricing.manage")
    if selectors.partner_row(partner_id) is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    with transaction.atomic():
        book = pricing.create_price_book(
            ctx,
            code=code,
            name=name,
            currency=currency,
            channel=PriceBookChannel.PARTNER,
            priority=priority,
            valid_from=valid_from,
            partner_id=partner_id,
        )
        # The pricing service audits the book itself; this is the partner's
        # own trail - "which price books were made for this partner, by whom".
        audit.record(
            ctx,
            "partner.price_book_created",
            resource_type="partners.partner",
            resource_id=partner_id,
            changes={"price_book_id": str(book.id), "code": code},
        )
    return book


#: The most one unit of a partner's plan may cost, in minor units. Far above any
#: real price, far below where quantity x price could overflow the ledger's
#: bigint columns - so an absurd figure is refused at the door instead of
#: failing later, at a sale, with an error nobody can act on.
MAX_PARTNER_PRICE_MINOR = 10**12


@in_scope
def set_partner_price(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    plan_version_id: UUID,
    interval: Any,
    amount_minor: Any,
    currency: Any = None,
) -> Any:
    """Set what a customer of this partner pays for one plan and billing cycle.

    A thin, idempotent use case over the **existing** pricing services - it has
    no price rule of its own. It finds the partner's price book in that
    currency (or creates it through :func:`create_partner_price_book`) and
    publishes a ``PlanPrice`` through ``pricing.publish_price``, which
    supersedes the previous row instead of editing it, refuses to backdate, and
    keeps the historical price reproducible. ``resolve_price`` then selects it
    for this partner's sales exactly as for any partner book; a plan/cycle with
    no partner price simply falls through to the list price.

    Repeating the call with the amount already in force changes nothing, so a
    retry after a lost response is safe.
    """
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.pricing import selectors as price_selectors
    from workeasy360.commercial.pricing import services as pricing
    from workeasy360.contracts.enums import BillingInterval

    authz.require(ctx, "platform.staff_pricing.manage")
    try:
        cycle = BillingInterval(str(interval))
    except ValueError as exc:
        raise PartnerValidationError(
            f"interval must be one of {sorted(i.value for i in BillingInterval)}.",
            field="interval",
        ) from exc
    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or not 0 <= amount_minor <= MAX_PARTNER_PRICE_MINOR
    ):
        raise PartnerValidationError(
            "amount_minor must be a whole number of minor units, zero or more "
            f"and at most {MAX_PARTNER_PRICE_MINOR}.",
            field="amount_minor",
        )
    catalog.require_plan_version(plan_version_id)

    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if partner.status_enum is PartnerStatus.TERMINATED:
            raise PartnerValidationError(
                "A terminated partner cannot be priced.", field="partner_id"
            )
        book_currency = parse_currency(
            currency or partner.payout_currency, field="currency"
        )
        book = price_selectors.partner_book_locked(partner_id, book_currency.value)
        if book is None:
            book = create_partner_price_book(
                ctx,
                partner_id,
                code=f"pb-{partner_id.hex[:12]}-{book_currency.value.lower()}",
                name=f"{partner.display_name} ({book_currency.value})"[:128],
                currency=book_currency,
                priority=50,
            )
        current = price_selectors.current_price_in_book(
            book.id, plan_version_id, cycle.value
        )
        if current is not None and current.amount_minor == amount_minor:
            return current
        price = pricing.publish_price(
            ctx,
            price_book_id=book.id,
            interval=cycle,
            plan_version_id=plan_version_id,
            amount_minor=amount_minor,
        )
        audit.record(
            ctx,
            "partner.price_set",
            resource_type="partners.partner",
            resource_id=partner_id,
            changes={
                "plan_version_id": str(plan_version_id),
                "interval": cycle.value,
                "amount_minor": amount_minor,
                "currency": book_currency.value,
                "price_book": book.code,
            },
        )
    metrics.increment(PARTNER_OPERATIONS, operation="price_set", outcome="ok")
    return price


# --- customer relationships -----------------------------------------------


@in_scope
def link_customer(
    ctx: TenantContext, partner_id: UUID, organization_id: UUID, *, reason: Any
) -> Any:
    """Relate a partner to an existing customer.

    The customer stays its own tenant; the link grants the partner commercial
    and support reach and nothing of the customer's business data. A customer
    can have **one** live link, so a second partner is refused rather than
    quietly sharing the account (the partial unique index decides a race).
    """
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    authz.require(ctx, MANAGE)
    why = clean_reason(reason)

    with transaction.atomic():
        partner = _lock_partner(partner_id)
        if not partner.status_enum.may_transact:
            raise PartnerValidationError(
                "Only an active partner can be linked to a customer.", field="partner_id"
            )
        with organization_scope(ctx, organization_id) as customer:
            try:
                with transaction.atomic():
                    link = PartnerOrganizationLink.objects.create(
                        partner_id=partner_id,
                        organization_id=organization_id,
                        started_at=timezone.now(),
                        created_by_id=ctx.user_id,
                    )
            except IntegrityError as exc:
                raise CommercialError(
                    "That customer already has a live partner relationship.",
                    organization_id=str(organization_id),
                ) from exc
            audit.record(
                customer,
                "partner.relationship_started",
                resource_type="partners.link",
                resource_id=link.id,
                changes={"partner_code": partner.code},
                reason=why,
            )
        _publish(
            ctx,
            schemas.PARTNER_CUSTOMER_LINKED_V1,
            link.id,
            {
                "partner_id": str(partner_id),
                "organization_id": str(organization_id),
                "link_id": str(link.id),
            },
        )
        audit.record(
            ctx,
            "partner.customer_linked",
            resource_type="partners.link",
            resource_id=link.id,
            changes={
                "partner_id": str(partner_id),
                "organization_id": str(organization_id),
            },
            reason=why,
        )
    return link


@in_scope
def change_link_status(
    ctx: TenantContext, link_id: UUID, *, target: PartnerLinkStatus, reason: Any
) -> Any:
    """Suspend, resume or end one relationship. Ending never touches the customer."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    authz.require(ctx, MANAGE)
    why = clean_reason(reason)

    with transaction.atomic():
        link = (
            PartnerOrganizationLink.objects.select_for_update().filter(id=link_id).first()
        )
        if link is None:
            raise PartnerRecordNotFound("No such relationship.", link_id=str(link_id))
        current = link.status_enum
        assert_link_transition(current, target)
        if current is target:
            return link
        if target is PartnerLinkStatus.ENDED:
            _end_link_row(ctx, link, reason_code="ended_by_staff")
        else:
            link.status = target.value
            link.version += 1
            link.save(update_fields=["status", "version"])
            with organization_scope(ctx, link.organization_id) as customer:
                audit.record(
                    customer,
                    f"partner.relationship_{target.value}",
                    resource_type="partners.link",
                    resource_id=link.id,
                    reason=why,
                )
        audit.record(
            ctx,
            f"partner.link_{target.value}",
            resource_type="partners.link",
            resource_id=link.id,
            changes={"partner_id": str(link.partner_id), "from": current.value},
            reason=why,
        )
    return link


# --- helpers --------------------------------------------------------------


def _clean_email(value: Any, *, required: bool, field: str = "email") -> str:
    text = clean_text(value, field=field, max_length=254, required=required)
    if not text:
        return ""
    try:
        validate_email(text)
    except ValidationError as exc:
        raise PartnerValidationError(
            "That is not a valid email address.", field=field
        ) from exc
    return text


#: The same primitives the commands validate with, public so the reseller
#: review (a dry run that writes nothing) can report exactly the problems the
#: real commands would - it can never accept what a command would refuse.
clean_email = _clean_email


def parse_currency(value: Any, *, field: str = "payout_currency") -> CurrencyCode:
    try:
        return CurrencyCode(str(value))
    except ValueError as exc:
        raise PartnerValidationError(
            f"{field} must be one of {sorted(c.value for c in CurrencyCode)}.",
            field=field,
        ) from exc
