"""The referral programme's global policy (ADR-054).

Two settings, owned by Super Admin (``platform.staff_partner.manage``):

- ``attribution_days`` - how long a captured referral may still convert. It is
  stamped on each referral when captured (``expires_at``), so a change applies
  to new clicks only and never re-dates one already made;
- ``revoked_links`` - what a product link does once its partner loses the
  promotion grant for its product: ``stop`` taking new visitors (the default),
  or ``continue``. Either way referrals already captured still convert, and
  commission already earned stays earned.

Until staff save a policy, the defaults apply: the
``W360_REFERRAL_CAPTURE_DAYS`` setting and ``stop``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction

from workeasy360.contracts.enums import RevokedLinkPolicy
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

MANAGE = "platform.staff_partner.manage"
VIEW = "platform.staff_partner.view"
MAX_DAYS = 365


@dataclass(frozen=True, slots=True)
class Policy:
    attribution_days: int
    revoked_links: RevokedLinkPolicy
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribution_days": self.attribution_days,
            "revoked_links": self.revoked_links.value,
            "updated_at": self.updated_at,
            # The fixed rules, stated beside the settings so no screen restates them.
            "attribution_rule": "first_touch",
            "max_attribution_days": MAX_DAYS,
        }


def current() -> Policy:
    from workeasy360.commercial.commissions.models import ReferralPolicy

    row = ReferralPolicy.objects.filter(id=1).first()
    if row is None:
        return Policy(
            attribution_days=int(getattr(settings, "W360_REFERRAL_CAPTURE_DAYS", 90)),
            revoked_links=RevokedLinkPolicy.STOP,
        )
    return Policy(
        attribution_days=row.attribution_days,
        revoked_links=RevokedLinkPolicy(row.revoked_links),
        updated_at=row.updated_at.isoformat(),
    )


@in_scope
def view(ctx: TenantContext) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    return current().as_dict()


@in_scope
def update(
    ctx: TenantContext, *, attribution_days: Any, revoked_links: Any
) -> dict[str, Any]:
    """Change the programme policy. Audited with the before and after."""
    from workeasy360.commercial.commissions.models import ReferralPolicy

    authz.require(ctx, MANAGE)
    if (
        isinstance(attribution_days, bool)
        or not isinstance(attribution_days, int)
        or not 1 <= attribution_days <= MAX_DAYS
    ):
        raise PartnerValidationError(
            f"The attribution window is 1 to {MAX_DAYS} days.", field="attribution_days"
        )
    try:
        policy = RevokedLinkPolicy(str(revoked_links or ""))
    except ValueError:
        raise PartnerValidationError(
            "Choose stop or continue.", field="revoked_links"
        ) from None
    before = current()
    with transaction.atomic():
        ReferralPolicy.objects.update_or_create(
            id=1,
            defaults={
                "attribution_days": attribution_days,
                "revoked_links": policy.value,
                "updated_by_id": ctx.user_id,
            },
        )
        audit.record(
            ctx,
            "partner.affiliate_policy_changed",
            resource_type="commissions.referral_policy",
            changes={
                "attribution_days": [before.attribution_days, attribution_days],
                "revoked_links": [before.revoked_links.value, policy.value],
            },
        )
    return current().as_dict()
