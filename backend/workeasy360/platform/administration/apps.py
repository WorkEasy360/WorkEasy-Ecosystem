"""App configuration for platform/administration (Phase 8 extension).

**Why a module, and why at P4.** The organization console's use cases -
inviting and admitting members, changing roles, suspending, signing a member
out, setting the organization's access policy, the dashboard - each compose
``organizations`` and ``identity`` (P1), ``authz``, ``audit`` and
``entitlements`` (P2) and ``notifications`` (P3, for the invitation email).
Composing a P3 module rules out P3 (siblings in a layer are independent), so
this sits at P4 beside ``launcher``, which is the same shape: a use-case layer
over everything beneath it, imported by nothing inside ``platform/``.

It owns no tables. Invitations, memberships and sign-ins are
``organizations``' rows, roles are ``authz``'s, the security trail is
``identity``'s - this module only decides and orchestrates.
"""

from __future__ import annotations

from django.apps import AppConfig


class AdministrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.administration"
    label = "administration"
    verbose_name = "Platform | Organization administration"
