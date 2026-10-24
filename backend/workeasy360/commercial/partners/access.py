"""Establishing a partner principal's context - the partner trust boundary.

Partner authorization needs **three independent facts**, and this module is
where the first two are established (Task 21):

1. **A partner relationship.** The acting user holds an active
   ``PartnerMember`` row for *this* partner. Membership of a customer
   organization confers nothing here - they are different tables.
2. **A capability.** Checked by the decision function from the partner's
   active grants (``resolver.py``), and again by each service against the
   specific permission it needs.
3. **The specific action permission.** The service's own
   ``authz.require(ctx, "platform.partner_...")``.

For a customer-scoped action there is a fourth: an **active link** between
this partner and that organization (``customer_context``). Deny by default at
every step, and a miss is a 404 rather than a 403, because "you may not" is
information about which identifiers exist.
"""

from __future__ import annotations

from uuid import UUID

from workeasy360.commercial.partners import selectors
from workeasy360.contracts.enums import PartnerLinkStatus, PartnerStatus, PrincipalKind
from workeasy360.contracts.errors import (
    PartnerNotActive,
    PartnerRecordNotFound,
    PermissionDenied,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz.control_plane import (
    context_for_organization,
    platform_context,
)

STAFF_MANAGE = "platform.staff_partner.manage"


def partner_actor(*, user_id: UUID, partner_id: UUID) -> TenantContext:
    """The context of ``user_id`` acting for ``partner_id``.

    Raises ``PartnerRecordNotFound`` unless the user is an active member of
    exactly this partner. The 404 is the same for "no such partner", "not your
    partner" and "you were removed", so a guessed partner id reveals nothing.
    """
    if selectors.member_of(partner_id, user_id) is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    return platform_context(
        user_id=user_id, principal=PrincipalKind.PARTNER, partner_id=partner_id
    )


def guest_actor(*, user_id: UUID, partner_id: UUID, reason: str) -> TenantContext:
    """A staff member's read-only *guest view* of ``partner_id`` (``guest.py``).

    A ``PARTNER`` context whose user is the staff member, marked break-glass
    with the reason they gave: the resolver gives it the viewer tier of the
    partner's capabilities, ``dangerous`` permissions are refused under it, and
    every audit entry records the staff member and the reason. Only the portal
    adapter builds one, after checking the live guest grant of this sign-in.
    """
    from dataclasses import replace

    if selectors.partner_row(partner_id) is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    ctx = platform_context(
        user_id=user_id, principal=PrincipalKind.PARTNER, partner_id=partner_id
    )
    return replace(ctx, is_break_glass=True, reason=reason)


def require_transacting(actor: TenantContext) -> None:
    """New commercial actions need an *active* partner (Task 62).

    The permission resolver already removes write permissions from a partner
    that may not transact; this states the reason plainly instead of a bare
    403, and holds even if a permission were ever granted some other way.
    """
    row = selectors.partner_row(actor.partner_id) if actor.partner_id else None
    if row is None or not row.status_enum.may_transact:
        raise PartnerNotActive(
            "This partner may not perform new commercial actions.",
            partner_status=row.status if row else None,
        )


def subject_partner(
    ctx: TenantContext,
    partner_id: UUID | None,
    *,
    partner_permission: str,
    write: bool = True,
) -> UUID:
    """The partner a brand or domain command is about, decided by who is asking.

    One command, two callers, no second implementation:

    - a **partner user** acts for their own partner only. An explicit
      ``partner_id`` that is not theirs is the same 404 as any other partner's
      record, and the command's own permission (``partner_permission``) is what
      authorizes it - so branding stays a non-dangerous action for the partner
      and a domain stays dangerous, exactly as before;
    - **staff** act for the partner they name, under
      ``platform.staff_partner.manage`` - which is dangerous, so every staff
      write is refused under impersonation.
      Staff may prepare a partner that is not yet active; a terminated
      partner takes no new brand or domain from anyone.
    """
    from workeasy360.platform.authz import services as authz

    if ctx.principal is PrincipalKind.PARTNER:
        if partner_id is not None and partner_id != ctx.partner_id:
            raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
        authz.require(ctx, partner_permission)
        if write:
            require_transacting(ctx)
        return ctx.partner_id  # type: ignore[return-value]

    from workeasy360.contracts.errors import PartnerValidationError

    authz.require(ctx, STAFF_MANAGE)
    if partner_id is None:
        raise PartnerValidationError("partner_id is required.", field="partner_id")
    row = selectors.partner_row(partner_id)
    if row is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    if write and row.status_enum is PartnerStatus.TERMINATED:
        raise PartnerNotActive(
            "A terminated partner cannot take a new brand or domain.",
            partner_status=row.status,
        )
    return partner_id


def customer_context(
    actor: TenantContext, organization_id: UUID, *, write: bool
) -> TenantContext:
    """The actor, re-bound to one customer it is *explicitly related to*.

    ``write=True`` additionally demands an ``active`` link and an active
    partner; a ``suspended`` link still permits reads. A customer with no live
    link to this partner - another partner's, a direct customer's, or one the
    relationship has ended for - is indistinguishable from one that does not
    exist.
    """
    if actor.partner_id is None:
        raise PermissionDenied("A partner context is required.", reason="not_a_partner")
    link = selectors.link_for(actor.partner_id, organization_id)
    if link is None:
        raise PartnerRecordNotFound(
            "No such customer.", organization_id=str(organization_id)
        )
    if write:
        require_transacting(actor)
        if link.status_enum is not PartnerLinkStatus.ACTIVE:
            raise PartnerNotActive(
                "This customer relationship is suspended.",
                link_status=link.status,
            )
    return context_for_organization(actor, organization_id)


def lock_active_link(actor: TenantContext, organization_id: UUID) -> None:
    """Re-verify the relationship **inside the writing transaction**, holding it.

    ``customer_context`` checks the link before a transaction exists; between
    that read and the write, another request may end the relationship. Locking
    the row here (``FOR NO KEY UPDATE``, which conflicts with the lock ending a
    link takes) closes that window: a write either commits before the link
    ends, or waits for it to end and then sees that it did. Either way no
    partner write commits after the relationship has ended (Task 116).
    """
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    link = (
        PartnerOrganizationLink.objects.select_for_update(no_key=True)
        .filter(partner_id=actor.partner_id, organization_id=organization_id)
        .exclude(status=PartnerLinkStatus.ENDED.value)
        .first()
    )
    if link is None:
        raise PartnerRecordNotFound(
            "No such customer.", organization_id=str(organization_id)
        )
    if link.status_enum is not PartnerLinkStatus.ACTIVE:
        raise PartnerNotActive(
            "This customer relationship is suspended.", link_status=link.status
        )
    require_transacting(actor)


def has_partner_membership(user_id: UUID) -> bool:
    """Whether this user works for any partner - for the shell's "open the
    partner console" link only; every partner request re-verifies its own
    partner through :func:`partner_actor`."""
    return selectors.has_active_membership(user_id)
