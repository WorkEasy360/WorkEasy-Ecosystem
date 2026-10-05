"""Named staff roles: least-privilege bundles of control-plane grants (Phase 9).

**A role is a name for a set of ``platform.staff_*`` permissions, nothing
more.** Holding a role *is* holding every one of its grants - each an ordinary
``StaffGrant`` row, created, audited, expired and revoked exactly like a grant
given one at a time. There is no role table, no role assignment and no second
authorization path: ``authz.check`` still reads grants (H1). A person "holds"
a role when they hold all of its permissions, which is derived, never stored,
so it cannot drift from what they can actually do.

| Role              | For                                                   |
|-------------------|-------------------------------------------------------|
| ``SUPER_ADMIN``   | every control-plane permission                        |
| ``USER_ADMIN``    | accounts: sign out, unlock, deactivate / reactivate    |
| ``PRODUCT_ADMIN`` | the catalog, prices, product integrations             |
| ``BILLING_ADMIN`` | subscriptions, invoices, payments, commissions        |
| ``SUPPORT_ADMIN`` | reading what a support case needs, and diagnosing it  |
| ``READ_ONLY_ADMIN`` | every read, no write                                |

Dangerous platform-wide operations - staff grants themselves
(``staff_security.manage``), payout approval, partners, platform settings,
communication and AI governance - belong to ``SUPER_ADMIN`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from workeasy360.platform.authz.registry import permission_registry
from workeasy360.platform.authz.staff import STAFF_KEY_PREFIX


@dataclass(frozen=True, slots=True)
class StaffRole:
    key: str
    name: str
    description: str
    permissions: frozenset[str]


def _all_staff_keys() -> frozenset[str]:
    return frozenset(
        d.key
        for d in permission_registry.all()
        if d.control_plane and d.key.startswith(STAFF_KEY_PREFIX)
    )


def _declared() -> tuple[StaffRole, ...]:
    everything = _all_staff_keys()
    return (
        StaffRole(
            "SUPER_ADMIN",
            "Super Admin",
            "Everything on the control plane, including granting staff access.",
            everything,
        ),
        StaffRole(
            "USER_ADMIN",
            "User Admin",
            "Accounts: sign people out, unlock, deactivate and reactivate.",
            frozenset(
                {
                    "platform.staff_user.view",
                    "platform.staff_user.manage",
                    "platform.staff_customer.view",
                    "platform.staff_audit.view",
                }
            ),
        ),
        StaffRole(
            "PRODUCT_ADMIN",
            "Product Admin",
            "The product catalog, plans, prices and product integrations.",
            frozenset(
                {
                    "platform.staff_catalog.view",
                    "platform.staff_catalog.manage",
                    "platform.staff_pricing.manage",
                    "platform.staff_integration.view",
                    "platform.staff_integration.manage",
                    "platform.staff_customer.view",
                }
            ),
        ),
        StaffRole(
            "BILLING_ADMIN",
            "Billing Admin",
            "Subscriptions, orders, invoices, payments, refunds and commissions.",
            frozenset(
                {
                    "platform.staff_customer.view",
                    "platform.staff_billing.view",
                    "platform.staff_subscription.manage",
                    "platform.staff_payment.view",
                    "platform.staff_payment.manage",
                    "platform.staff_commission.view",
                    "platform.staff_commission.manage",
                    "platform.staff_payout.manage",
                }
            ),
        ),
        StaffRole(
            "SUPPORT_ADMIN",
            "Support Admin",
            "Read what a support case needs: customers, users, billing, audit, "
            "and diagnose why someone cannot open a product.",
            frozenset(
                {
                    "platform.staff_support.view",
                    "platform.staff_customer.view",
                    "platform.staff_user.view",
                    "platform.staff_billing.view",
                    "platform.staff_payment.view",
                    "platform.staff_audit.view",
                    "platform.staff_partner.view",
                }
            ),
        ),
        StaffRole(
            "READ_ONLY_ADMIN",
            "Read-only Admin",
            "Every control-plane page, read-only.",
            frozenset(k for k in everything if k.endswith(".view")),
        ),
    )


ROLE_KEYS: Final = (
    "SUPER_ADMIN",
    "USER_ADMIN",
    "PRODUCT_ADMIN",
    "BILLING_ADMIN",
    "SUPPORT_ADMIN",
    "READ_ONLY_ADMIN",
)


def roles() -> tuple[StaffRole, ...]:
    """Every role, with its permissions resolved against the live registry."""
    return _declared()


def role(key: str) -> StaffRole | None:
    return next((r for r in _declared() if r.key == key), None)


def roles_held(permissions: set[str] | frozenset[str]) -> list[str]:
    """The roles whose every permission is in ``permissions`` (the keys a
    person holds grants for, before ``implies``)."""
    return [r.key for r in _declared() if r.permissions <= set(permissions)]
