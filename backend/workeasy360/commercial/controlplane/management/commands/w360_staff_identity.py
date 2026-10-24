"""Designate - or stop designating - an account as Workeasy360 staff (Phase 7 final audit).

Staff authority is two things held by different people on purpose:

- **Being a staff identity** (``User.is_staff``): an operator decision, made
  here, from a terminal. It confers nothing by itself.
- **Holding a permission**: a ``StaffGrant`` issued by a security
  administrator in the Super Admin UI, with a reason and an optional expiry.
  A grant can only be issued to a staff identity.

The split limits a compromised security-administrator session: it can grant
only to accounts the operator already designated, never to a fresh account an
attacker controls. Before this command the only way to designate a second
person was hand-written SQL.

``--disable`` is also the **one-step cut-off** for a compromised or departing
staff member: every grant they hold stops working on their next request
(``staff_permissions`` requires an active staff identity), while the grant
rows - who could do what, and when - are kept.

Refuses to disable the last *permanent* security administrator (the same rule
``authz.staff.end_grant`` enforces); ``bootstrap_superadmin`` covers a platform
that has none. Without ``--confirm`` it only reports. Every change is audited
as a system action in the platform organization's log.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.values.control_plane import (
    PLATFORM_ORGANIZATION_ID,
    PLATFORM_SYSTEM_USER_EMAIL,
)
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import staff
from workeasy360.platform.authz.control_plane import ensure_platform_organization
from workeasy360.platform.identity import lifecycle
from workeasy360.platform.identity.email import normalize_email
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.db import organization_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access


class Command(BaseCommand):
    help = "Designate an existing account as staff, or remove that designation."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--email", required=True, help="Address of an existing account."
        )
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--enable", action="store_true", help="Make it a staff identity."
        )
        mode.add_argument(
            "--disable",
            action="store_true",
            help="Remove the staff designation (ends every grant's effect at once).",
        )
        parser.add_argument("--reason", required=True, help="Recorded in the audit log.")
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Make the change (default: report only).",
        )

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        reason = str(options["reason"] or "").strip()
        if len(reason) < staff.MIN_REASON_LENGTH:
            raise CommandError("A --reason of at least 10 characters is required.")
        enable = bool(options["enable"])
        address = normalize_email(str(options["email"]))
        if address == normalize_email(PLATFORM_SYSTEM_USER_EMAIL):
            raise CommandError("The control-plane system identity is not a person.")

        with unscoped_access(
            reason="Operator designates a staff identity", module=__name__
        ):
            target = User.objects.filter(email=address).first()
        if target is None or (enable and target.status != UserStatus.ACTIVE.value):
            raise CommandError("No active account has that address.")
        if target.is_staff is enable:
            self.stdout.write(
                f"{target.email} is already {'a' if enable else 'not a'} staff identity."
            )
            return
        if not options["confirm"]:
            verb = "designate" if enable else "remove the staff designation of"
            self.stdout.write(f"Would {verb} {target.email}. Re-run with --confirm.")
            return

        ensure_platform_organization()
        with transaction.atomic():
            # One operator change at a time, and the same lock the bootstrap
            # takes, so the "last administrator" reading below cannot race it.
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", ["w360:bootstrap-staff"]
                )
            if not enable and staff.is_last_permanent_security_administrator(target.id):
                raise CommandError(
                    "That is the last permanent security administrator. Grant "
                    "platform.staff_security.manage (without an expiry) to someone "
                    "else first."
                )
            with unscoped_access(
                reason="Operator designates a staff identity", module=__name__
            ):
                lifecycle.set_staff(target, enable)
            with organization_db_scope(PLATFORM_ORGANIZATION_ID):
                audit.record_system(
                    PLATFORM_ORGANIZATION_ID,
                    "staff.identity_enabled" if enable else "staff.identity_disabled",
                    resource_type="identity.user",
                    resource_id=str(target.id),
                    changes={"is_staff": enable, "at": timezone.now().isoformat()},
                    reason=reason,
                )
        self.stdout.write(
            f"{target.email} is {'now' if enable else 'no longer'} a staff identity."
        )
