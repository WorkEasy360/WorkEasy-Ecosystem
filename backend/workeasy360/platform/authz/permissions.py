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
) -> PermissionDeclaration:
    return PermissionDeclaration(
        key=PermissionKey(key),
        label=label,
        category=category,
        implies=implies,
        dangerous=dangerous,
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
    _p(
        "platform.billing.manage",
        "Change payment methods and subscriptions",
        category="billing",
        dangerous=True,
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
