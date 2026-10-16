"""Partner permission declarations (ADR-028).

Every key is ``control_plane=True``: a partner user holds these through
capability grants and never through a customer role, so the owner role's
``"*"`` skips them, and a customer organization cannot obtain one by any role
it can build.

The keys are derived from ``capabilities.py`` - the registry is the source, and
this file only declares them to the permission registry with their labels and
implications - so a capability cannot unlock a permission that is not declared,
and a permission cannot exist that no capability unlocks (asserted by
``test_every_partner_permission_is_reachable_from_a_capability``).

Implications point at the ``commercial.*`` permissions the existing catalog,
billing and subscription services already check, so a partner acts through the
*same* services a direct sale uses (Task 56): those services accept a partner
context because its permission set contains what they ask for. Each
partner-facing service checks the specific ``platform.partner_*`` key first,
so holding one capability never opens another.

Four groups are ``dangerous`` (AAL 2), because a stolen partner session there
is directly profitable or directly harmful:

- **domain**: a custom domain changes where the platform's own pages are
  served, and an account takeover there is a phishing platform (Task 85);
- **provision / sell / subscription-manage / customer-manage**: creating a
  customer and selling it a plan earns the partner commission, so a hijacked
  session can manufacture commission. AAL 2 does not stop a dishonest partner
  (the holdback, reversals and staff payout review do) but it does stop a
  session thief from doing it silently.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)


def _d(
    key: str,
    label: str,
    *,
    implies: tuple[str, ...] = (),
    dangerous: bool = False,
) -> PermissionDeclaration:
    return PermissionDeclaration(
        key=PermissionKey(key),
        label=label,
        category="partner",
        implies=implies,
        dangerous=dangerous,
        control_plane=True,
    )


_SELL_IMPLIES: Final = (
    "platform.partner_customer.view",
    "commercial.subscription.manage",
    "commercial.subscription.view",
    "commercial.catalog.view",
)

PARTNER_PERMISSIONS: Final = (
    _d("platform.partner_customer.view", "View the partner's own customers"),
    _d(
        "platform.partner_customer.provision",
        "Create customer organizations",
        implies=("platform.partner_customer.view", "commercial.subscription.manage"),
        dangerous=True,
    ),
    _d(
        "platform.partner_customer.manage",
        "Set up billing for related customers",
        implies=("platform.partner_customer.view", "commercial.subscription.manage"),
        dangerous=True,
    ),
    _d(
        "platform.partner_subscription.sell",
        "Sell approved plans to related customers",
        implies=_SELL_IMPLIES,
        dangerous=True,
    ),
    _d(
        "platform.partner_subscription.manage",
        "Change, cancel and activate related customers' subscriptions",
        implies=_SELL_IMPLIES,
        dangerous=True,
    ),
    _d("platform.partner_pricing.view", "See prices approved for the partner"),
    _d("platform.partner_branding.view", "View the partner's branding"),
    _d(
        "platform.partner_branding.manage",
        "Change the partner's branding",
        implies=("platform.partner_branding.view",),
    ),
    _d("platform.partner_domain.view", "View the partner's custom domains"),
    _d(
        "platform.partner_domain.manage",
        "Request, verify and activate custom domains",
        dangerous=True,
        implies=("platform.partner_domain.view",),
    ),
    _d("platform.partner_commission.view", "View the partner's own commission"),
    _d("platform.partner_payout.view", "View the partner's own payouts"),
    _d(
        "platform.partner_support.access",
        "Run read-only access diagnostics for related customers",
        implies=("platform.partner_customer.view",),
    ),
    _d("platform.partner_referral.view", "View the partner's referrals"),
    _d(
        "platform.partner_referral.create",
        "Create referral codes",
        implies=("platform.partner_referral.view",),
    ),
    # The partner's own account (ADR-045). The first four are held by every
    # member of an active partner and the last by its administrators
    # (``capabilities.BASELINE_*``); none reaches a customer's data.
    _d("platform.partner_dashboard.view", "See the partner dashboard"),
    _d("platform.partner_profile.view", "See the partner's business profile"),
    _d(
        "platform.partner_profile.manage",
        "Change the partner's business profile",
        implies=("platform.partner_profile.view",),
    ),
    _d("platform.partner_member.view", "See who works for the partner"),
    _d(
        "platform.partner_member.manage",
        "Invite, re-role and remove the partner's own users",
        implies=("platform.partner_member.view",),
        dangerous=True,
    ),
    _d("platform.partner_product.view", "See the products the partner may sell"),
    _d(
        "platform.partner_product.manage",
        "Arrange and enable the partner's granted products for sale",
        implies=("platform.partner_product.view",),
    ),
    _d("platform.partner_activity.view", "See the partner's activity trail"),
    _d(
        "platform.partner_customer.export",
        "Export the partner's customer list",
        implies=("platform.partner_customer.view",),
    ),
    _d("platform.partner_subpartner.view", "See the partner's sub-partners"),
    _d(
        "platform.partner_subpartner.manage",
        "Create, activate, suspend and equip sub-partners",
        implies=("platform.partner_subpartner.view",),
        dangerous=True,
    ),
)

permission_registry.register_all(PARTNER_PERMISSIONS)
