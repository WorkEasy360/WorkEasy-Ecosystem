"""Break glass: a staff member lost their authenticator *and* recovery codes
(Phase 12, D-037 residual).

Since D-037 every control-plane request needs a session that verified a second
factor, so a lost factor locks a staff member out, and nothing in the
application can remove it (removing a factor needs a verified session).

This command, run by an operator from a terminal:

1. removes every factor of the account (authenticator and recovery codes);
2. signs every session of the account out;
3. **removes the staff designation**. A staff identity without a factor would
   otherwise let whoever holds the password enrol their own authenticator.
   Grants are kept, and work again once the account is designated again.

Recovery is then the ordinary, audited path. The person enrols a new
authenticator on their own account, and the operator confirms it is theirs and
runs ``w360_staff_identity --enable``, which requires that factor.

Without ``--confirm`` it only reports. Every reset is audited as a system
action in the platform organization's log.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from workeasy360.contracts.values.control_plane import (
    PLATFORM_ORGANIZATION_ID,
    PLATFORM_SYSTEM_USER_EMAIL,
)
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import staff
from workeasy360.platform.authz.control_plane import ensure_platform_organization
from workeasy360.platform.identity import lifecycle
from workeasy360.platform.identity.email import normalize_email
from workeasy360.platform.identity.models import AuthFactor, User
from workeasy360.platform.tenancy.db import organization_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access


class Command(BaseCommand):
    help = "Remove a staff member's second factors and staff designation (break glass)."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument("--email", required=True, help="The staff member's address.")
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
        address = normalize_email(str(options["email"]))
        if address == normalize_email(PLATFORM_SYSTEM_USER_EMAIL):
            raise CommandError("The control-plane system identity is not a person.")

        with unscoped_access(
            reason="Operator resets a staff second factor", module=__name__
        ):
            target = User.objects.filter(email=address).first()
        if target is None or not target.is_staff:
            raise CommandError("No staff identity has that address.")
        factors = AuthFactor.objects.filter(user_id=target.id).count()
        last_admin = staff.is_last_permanent_security_administrator(target.id)
        if not options["confirm"]:
            self.stdout.write(
                f"Would remove {factors} factor record(s) of {target.email}, sign out "
                f"every session and remove the staff designation. Re-run with --confirm."
            )
            if last_admin:
                self.stdout.write(
                    "Note: this is the last permanent security administrator. Security "
                    "administration is unavailable until the account is designated again."
                )
            return

        ensure_platform_organization()
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", ["w360:bootstrap-staff"]
                )
            with unscoped_access(
                reason="Operator resets a staff second factor", module=__name__
            ):
                removed = lifecycle.reset_second_factors(target)
                lifecycle.set_staff(target, False)
            with organization_db_scope(PLATFORM_ORGANIZATION_ID):
                audit.record_system(
                    PLATFORM_ORGANIZATION_ID,
                    "staff.mfa_reset",
                    resource_type="identity.user",
                    resource_id=str(target.id),
                    changes={
                        "factors_removed": removed,
                        "is_staff": False,
                        "at": timezone.now().isoformat(),
                    },
                    reason=reason,
                )
        self.stdout.write(
            f"Removed {removed} factor record(s) of {target.email}, signed out every "
            f"session and removed the staff designation. Next: the person enrols a new "
            f"authenticator on their own account; confirm it with them, then run "
            f"w360_staff_identity --email {target.email} --enable --reason ... --confirm."
        )
