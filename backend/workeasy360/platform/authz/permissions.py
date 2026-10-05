"""Platform permission declarations.

This file is the platform's complete privilege surface, and under ADR-017 it
is the *only* privilege surface this repository declares. Products are
separate applications; they own their own permissions and will declare them
to the platform through the registration contract
(16-ecosystem-product-boundary.md section 7), not by adding entries here.

The naming convention is fixed here so every product follows one shape:

    <product>.<resource>.<action>     crm.contact.view, crm.contact.create
    platform.<resource>.<action>      platform.member.invite

``EXAMPLE_PRODUCT_PERMISSIONS`` below is that convention written down, and
nothing more. Those keys are **not registered**: a grantable permission with
nothing enforcing it is worse than none, and the code that would enforce a
product permission does not live in this repository at all.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)


def _p(
    key: str,
    label: str,
    *,
    category: str = "general",
    implies: tuple[str, ...] = (),
    dangerous: bool = False,
    control_plane: bool = False,
) -> PermissionDeclaration:
    return PermissionDeclaration(
        key=PermissionKey(key),
        label=label,
        category=category,
        implies=implies,
        dangerous=dangerous,
        control_plane=control_plane,
    )


# --- Organization administration -----------------------------------------

PLATFORM_PERMISSIONS: Final = (
    _p(
        "platform.organization.view",
        "View organization settings",
        category="organization",
    ),
    _p(
        "platform.organization.update",
        "Change organization settings",
        category="organization",
        implies=("platform.organization.view",),
    ),
    _p(
        "platform.organization.delete",
        "Delete the organization",
        category="organization",
        # Irreversible after the recovery window. Requires AAL 2, and is
        # blocked under impersonation entirely.
        dangerous=True,
        implies=("platform.organization.view",),
    ),
    _p(
        "platform.organization.transfer",
        "Transfer ownership",
        category="organization",
        dangerous=True,
    ),
    # --- Members ---
    _p("platform.member.view", "View members", category="members"),
    _p(
        "platform.member.invite",
        "Invite members",
        category="members",
        implies=("platform.member.view",),
    ),
    _p(
        "platform.member.update",
        "Change a member's status or type",
        category="members",
        implies=("platform.member.view",),
    ),
    _p(
        "platform.member.remove",
        "Remove members",
        category="members",
        implies=("platform.member.view",),
    ),
    # Phase 8 extension. Ends a member's sessions *for this organization*
    # (``Membership.sessions_valid_after``) - a revocation, so dangerous: AAL 2,
    # refused under impersonation, audited.
    _p(
        "platform.member.sign_out",
        "Sign a member out of the organization",
        category="members",
        dangerous=True,
        implies=("platform.member.view",),
    ),
    # --- Roles ---
    _p("platform.role.view", "View roles and assignments", category="access"),
    _p(
        "platform.role.create",
        "Create custom roles",
        category="access",
        implies=("platform.role.view",),
    ),
    _p(
        "platform.role.update",
        "Change a custom role's permissions",
        category="access",
        dangerous=True,
        implies=("platform.role.view",),
    ),
    _p(
        "platform.role.assign",
        "Assign roles to members",
        category="access",
        dangerous=True,
        implies=("platform.role.view", "platform.member.view"),
    ),
    # --- Billing. Separated from admin on purpose: in most customers the
    # person administering users is not the person holding the card, and
    # bundling them forces over-privileging (04-identity-authz.md section 5).
    _p("platform.billing.view", "View billing and invoices", category="billing"),
    # Phase 9 (owner decision, 2026-09-26): paying through the provider's
    # hosted checkout is not dangerous. The provider authenticates the card
    # (3-D Secure) and no stored instrument exists (D-028), so a second factor
    # here only stood between a new customer and their first purchase.
    # Refunds, void, mark-paid and provider configuration stay dangerous
    # staff permissions.
    _p(
        "platform.billing.manage",
        "Pay invoices and manage billing details",
        category="billing",
        implies=("platform.billing.view",),
    ),
    # --- Entitlements (read-only to customers; provisioning is the writer) ---
    _p(
        "platform.entitlement.view",
        "View purchased products and limits",
        category="billing",
    ),
    _p(
        "platform.seat.assign",
        "Assign and release product seats",
        category="billing",
        implies=("platform.entitlement.view", "platform.member.view"),
    ),
    # --- Audit ---
    _p("platform.audit.view", "Read the organization's audit log", category="security"),
    _p(
        "platform.audit.export",
        "Export the audit log",
        category="security",
        dangerous=True,
        implies=("platform.audit.view",),
    ),
    # --- Security ---
    _p("platform.security.view", "View security settings", category="security"),
    _p(
        "platform.security.update",
        "Change MFA and session policy",
        category="security",
        dangerous=True,
        implies=("platform.security.view",),
    ),
    _p(
        "platform.apikey.manage",
        "Create and revoke API keys",
        category="security",
        dangerous=True,
    ),
    # --- Files ---
    _p("platform.file.view", "View and download files", category="files"),
    _p(
        "platform.file.upload",
        "Upload files",
        category="files",
        implies=("platform.file.view",),
    ),
    _p(
        "platform.file.delete",
        "Delete files",
        category="files",
        implies=("platform.file.view",),
    ),
    # --- Notifications ---
    _p("platform.notification.view", "View notifications", category="notifications"),
    _p(
        "platform.notification.manage",
        "Change notification preferences",
        category="notifications",
        implies=("platform.notification.view",),
    ),
    # --- Data portability. A module that cannot export its data cannot ship;
    # this is the permission that gates the customer-facing side of that.
    _p(
        "platform.data.export",
        "Export organization data",
        category="data",
        dangerous=True,
    ),
)

permission_registry.register_all(PLATFORM_PERMISSIONS)


# --- The Super Admin control plane (Phase 7, ADR-027) ---------------------
#
# ``platform.staff_*``: the Workeasy360 control plane's own privilege
# surface. Three properties hold for every key below, and each is asserted
# by a test rather than promised here:
#
# 1. **No role grants them.** ``control_plane=True`` removes them from the
#    owner role's ``"*"``; they are held only through ``StaffGrant`` rows,
#    which a customer administrator cannot create.
# 2. **Writes are dangerous** - AAL 2, and refused under impersonation.
# 3. **Django's ``is_superuser`` is never consulted.** A staff member holds
#    exactly the grants they were given and nothing else.
#
# Several *imply* an existing ``commercial.*`` permission so that the one
# commercial service that already checks it (catalog, pricing, subscriptions)
# accepts a staff context without a second implementation of the command.

STAFF_PERMISSIONS: Final = (
    _p(
        "platform.staff_customer.view",
        "View the customer directory and customer detail",
        category="control_plane",
        control_plane=True,
    ),
    _p(
        "platform.staff_customer.manage",
        "Suspend and reactivate customer organizations",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_customer.view",),
    ),
    _p(
        "platform.staff_catalog.view",
        "View the product catalog and price books",
        category="control_plane",
        control_plane=True,
        implies=("commercial.catalog.view", "commercial.pricing.view"),
    ),
    _p(
        "platform.staff_catalog.manage",
        "Create and change products, features, plans and bundles",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=(
            "platform.staff_catalog.view",
            "commercial.catalog.manage",
        ),
    ),
    _p(
        "platform.staff_pricing.manage",
        "Publish prices, price books and negotiated contract prices",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=(
            "platform.staff_catalog.view",
            "commercial.pricing.manage",
        ),
    ),
    _p(
        "platform.staff_subscription.manage",
        "Suspend, resume, cancel and change customers' subscriptions",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=(
            "platform.staff_customer.view",
            "commercial.subscription.manage",
            "commercial.subscription.view",
            "commercial.catalog.view",
        ),
    ),
    _p(
        "platform.staff_billing.view",
        "Inspect billing accounts (provider-independent, manual state)",
        category="control_plane",
        control_plane=True,
        implies=("platform.staff_customer.view",),
    ),
    _p(
        "platform.staff_partner.view",
        "View partners, their grants, customers and branding",
        category="control_plane",
        control_plane=True,
    ),
    _p(
        "platform.staff_partner.manage",
        "Create, activate, suspend and terminate partners; grant capabilities",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_partner.view", "platform.staff_customer.view"),
    ),
    _p(
        "platform.staff_commission.view",
        "View commission rules, accruals and payouts",
        category="control_plane",
        control_plane=True,
    ),
    _p(
        "platform.staff_commission.manage",
        "Change commission rules; reverse or adjust accruals",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_commission.view",),
    ),
    _p(
        "platform.staff_payout.manage",
        "Prepare, cancel and record payment of payouts",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_commission.view",),
    ),
    _p(
        "platform.staff_payout.approve",
        "Approve a prepared payout",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_commission.view",),
    ),
    _p(
        "platform.staff_support.view",
        "Run read-only access diagnostics for a customer",
        category="control_plane",
        control_plane=True,
        implies=("platform.staff_customer.view",),
    ),
    _p(
        "platform.staff_audit.view",
        "Read the control-plane audit trail",
        category="control_plane",
        control_plane=True,
    ),
    # Product-integration administration (the Phase 3 registry: redirect URIs,
    # client credentials, lifecycle). Held through a grant like everything
    # above; before the Phase 7 final audit the registry was reachable with
    # Django's ``is_staff`` plus any organization's owner role instead.
    _p(
        "platform.staff_integration.view",
        "View registered product integrations and their credential metadata",
        category="control_plane",
        control_plane=True,
        implies=("platform.integration.view",),
    ),
    _p(
        "platform.staff_integration.manage",
        "Register products, change redirect URIs, manage lifecycle and credentials",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=(
            "platform.staff_integration.view",
            "platform.integration.register",
            "platform.integration.update",
            "platform.integration.lifecycle",
            "platform.integration.credential",
        ),
    ),
    # The shared AI platform (Phase 8, Task 87). Reading is diagnostics -
    # model registry, provider status, an organization's AI policy and usage,
    # never a credential (none is stored). Managing changes where every
    # customer's data may be sent and what it may cost, so it is dangerous.
    _p(
        "platform.staff_ai.view",
        "View AI providers, models, prices, organization AI policy and usage",
        category="control_plane",
        control_plane=True,
    ),
    _p(
        "platform.staff_ai.manage",
        "Enable or disable AI providers and models, add models and prices, "
        "and set an organization's AI policy",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_ai.view",),
    ),
    # Phase 8 extension. The platform-wide user directory: accounts, their
    # status, sign-in recency and MFA enrolment - never a credential. Managing
    # an account (sign out everywhere, unlock, disable, enable) reaches every
    # organization the person belongs to at once, so it is dangerous.
    _p(
        "platform.staff_user.view",
        "View the user directory and account security state",
        category="control_plane",
        control_plane=True,
    ),
    _p(
        "platform.staff_user.manage",
        "Sign a user out everywhere, unlock, disable or re-enable an account",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_user.view",),
    ),
    # Communication: what every platform email says and who it says it is
    # from. A compromised editor could turn every invitation into phishing,
    # so changing it is dangerous.
    _p(
        "platform.staff_communication.view",
        "View email templates and sender settings",
        category="control_plane",
        control_plane=True,
    ),
    _p(
        "platform.staff_communication.manage",
        "Change email templates and sender settings",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_communication.view",),
    ),
    # Phase 9: platform settings - the signup switch, public contact addresses
    # and the public catalog's default currency. Closing signup or changing
    # where customers are told to write is platform-wide, so managing is
    # dangerous.
    _p(
        "platform.staff_settings.view",
        "View platform settings",
        category="control_plane",
        control_plane=True,
    ),
    _p(
        "platform.staff_settings.manage",
        "Change platform settings",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_settings.view",),
    ),
    # Payments: providers, invoices, payment attempts, webhook log. Managing
    # moves money or records that it moved (refunds, manual mark-paid, void,
    # provider enablement), so it is dangerous.
    _p(
        "platform.staff_payment.view",
        "View payment providers, invoices, payments and webhook events",
        category="control_plane",
        control_plane=True,
        implies=("platform.staff_customer.view",),
    ),
    _p(
        "platform.staff_payment.manage",
        "Configure payment providers, refund payments, mark invoices paid or void",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_payment.view",),
    ),
    _p(
        "platform.staff_security.view",
        "View who holds control-plane permissions",
        category="control_plane",
        control_plane=True,
    ),
    _p(
        "platform.staff_security.manage",
        "Grant and revoke control-plane permissions",
        category="control_plane",
        dangerous=True,
        control_plane=True,
        implies=("platform.staff_security.view",),
    ),
)

permission_registry.register_all(STAFF_PERMISSIONS)


#: The naming convention, documented and deliberately unregistered. Every key
#: here belongs to a product built outside this repository; none is grantable
#: (asserted by ``test_example_product_permissions_remain_unregistered``).
EXAMPLE_PRODUCT_PERMISSIONS: Final = (
    "crm.contact.view",
    "crm.contact.create",
    "crm.contact.update",
    "crm.contact.delete",
    "crm.deal.view",
    "crm.deal.update",
    "books.invoice.view",
    "books.journal.post",
    "inventory.item.view",
    "projects.project.view",
)
