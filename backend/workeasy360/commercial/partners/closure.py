"""What partner records say about closing a person's account (ADR-075 review).

``platform.administration.account_security.close_account`` must never leave
something without anyone able to run it, and two such things live here, in
``commercial``, which ``platform`` may not import (F-4). Both answers are
registered from ``PartnersConfig.ready()``:

- **M-1, the last administrator.** An active partner keeps at least one
  working administrator - the rule ``services.remove_member`` and
  ``change_member_role`` already keep for a partner acting on itself.
  Closing the account of that administrator would lock the partner out of
  its own portal, so closure is refused with the partners named.
- **M-2, the invited owner.** A partner-provisioned customer's owner is
  INVITED until they accept (``PartnerOwnerInvite``). That membership is not
  in the access projection, so ``platform`` cannot find it alone; this names
  the candidate organizations and ``platform`` checks each ``Membership``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.partners import capabilities
from workeasy360.contracts.enums import PartnerStatus, UserStatus


def partners_left_without_admin(
    user_id: UUID, *, lock: bool = False
) -> list[dict[str, Any]]:
    """The active partners whose only working administrator is this person.

    ``lock`` (inside the closing transaction) locks every active
    administrator row of those partners first, in a fixed order, so two
    administrators of one partner who close at the same moment cannot both
    pass on the strength of the other: the second waits, then sees the first
    closed (``user__status``) and is refused."""
    from workeasy360.commercial.partners.models import PartnerMember

    admin_roles = sorted(capabilities.ADMIN_ROLES)
    mine = list(
        PartnerMember.objects.filter(
            user_id=user_id,
            status="active",
            role__in=admin_roles,
            partner__status=PartnerStatus.ACTIVE.value,
        )
        .order_by("partner_id")
        .values("partner_id", "partner__display_name")
    )
    if not mine:
        return []
    partner_ids = [row["partner_id"] for row in mine]
    admins = PartnerMember.objects.filter(
        partner_id__in=partner_ids, status="active", role__in=admin_roles
    )
    if lock:
        list(admins.select_for_update().order_by("pk").values_list("pk", flat=True))
    covered = set(
        admins.exclude(user_id=user_id)
        .filter(user__status=UserStatus.ACTIVE.value)
        .values_list("partner_id", flat=True)
    )
    return [
        {"partner_id": str(row["partner_id"]), "name": row["partner__display_name"]}
        for row in mine
        if row["partner_id"] not in covered
    ]


def invited_owner_organizations(user_id: UUID) -> list[UUID]:
    """Every organization a partner invited this person to own. A candidate
    list only: any status, because an expired or revoked invitation can leave
    the owner membership INVITED - the ``Membership`` row decides."""
    from workeasy360.commercial.partners.models import PartnerOwnerInvite

    return list(
        PartnerOwnerInvite.objects.filter(user_id=user_id)
        .values_list("organization_id", flat=True)
        .distinct()
    )
