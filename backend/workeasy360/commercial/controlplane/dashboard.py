"""The control-tower summary and the control-plane audit trail (Tasks 73-75).

**Counts of concrete operational facts, nothing derived or scored.** There is no
MRR or ARR here, and that is a decision rather than an omission: a global
monthly figure needs currency conversion (this platform has none and refuses to
invent one), monthly normalisation of annual and one-time prices, trial and
suspension treatment, and partner-book versus list-price attribution - and a
number that is wrong in any of those ways is worse than no number. Amounts that
*are* well defined are reported per currency, unconverted.

No customer-health score and no AI: only statuses that are facts.
"""

from __future__ import annotations

from typing import Any

from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.provisioning.selectors import status_counts
from workeasy360.contracts.values.cursor import Page
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events.selectors import outbox_backlog
from workeasy360.platform.audit import selectors as audit_selectors
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.organizations.selectors import organization_status_counts

VIEW = "platform.staff_customer.view"
AUDIT_VIEW = "platform.staff_audit.view"
COMMISSION_VIEW = "platform.staff_commission.view"
PARTNER_VIEW = "platform.staff_partner.view"


def summary(ctx: TenantContext) -> dict[str, Any]:
    """Only the sections the caller's permissions cover are populated."""
    authz.require(ctx, VIEW)
    result: dict[str, Any] = {
        "organizations": organization_status_counts(),
        "subscriptions": status_counts(),
        "mrr": {
            "available": False,
            "reason": (
                "No safe global figure exists: amounts span currencies with no "
                "conversion, and intervals are not normalised across plans."
            ),
        },
        "outbox": outbox_backlog(),
    }
    if authz.check(ctx, PARTNER_VIEW).allowed:
        result["partners"] = partner_selectors.status_counts()
    if authz.check(ctx, COMMISSION_VIEW).allowed:
        result["pending_payouts"] = commission_selectors.pending_payout_summary()
    # Phase 8 extension: accounts and collections, each behind its own grant.
    if authz.check(ctx, "platform.staff_user.view").allowed:
        from datetime import timedelta

        from django.utils import timezone

        from workeasy360.platform.identity.selectors import account_statistics

        result["users"] = {
            "window_days": 7,
            **account_statistics(since=timezone.now() - timedelta(days=7)),
        }
    if authz.check(ctx, "platform.staff_payment.view").allowed:
        from workeasy360.commercial.payments.staff import collections_summary

        result["collections"] = collections_summary(days=30)
    return result


@in_scope
def control_plane_audit(
    ctx: TenantContext,
    *,
    action_prefix: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> Page:
    """The platform organization's own audit log: partner, payout, commission,
    catalog and staff-permission actions."""
    authz.require(ctx, AUDIT_VIEW)
    return audit_selectors.audit_page(
        action_prefix=action_prefix, limit=limit, cursor=cursor
    )
