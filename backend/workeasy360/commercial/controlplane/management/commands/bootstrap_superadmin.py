"""Bootstrap the platform Super Admin from deployment configuration (Phase 9).

    PLATFORM_SUPERADMIN_EMAIL=...  PLATFORM_SUPERADMIN_PASSWORD=...  \\
        python manage.py bootstrap_superadmin

Both values come from the deployment's secret store, never from the
repository, the image or the command line (a command-line password lands in
shell history and process listings). Safe to run on every deploy:

- **Idempotent.** An account that already is the Super Admin is left exactly
  as it is and the command succeeds - so the same deployment step can run on
  every release.
- **Creates the account if absent**, verified and active, with the password
  from the environment (validated by the platform's password rules). The
  password is required only for this case.
- **Never changes an existing account's password**, and never prints it.
- **No back door.** It refuses once a *different* person already holds
  security administration: from then on, staff access is granted in the Super
  Admin console, with a reason and an audit entry. The check
  and the grant are serialised on an advisory lock, so two concurrent runs
  cannot both succeed.
- **Audited** as a system action in the platform log. The grants are made by
  the control-plane system identity.

It grants the ``SUPER_ADMIN`` role - every ``platform.staff_*`` permission,
each an ordinary ``StaffGrant`` (``platform.authz.staff_roles``).
``is_superuser`` is not set: it confers nothing on the control plane.
"""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.values.control_plane import (
    PLATFORM_ORGANIZATION_ID,
    PLATFORM_SYSTEM_USER_EMAIL,
)
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import staff, staff_roles
from workeasy360.platform.authz.control_plane import ensure_platform_organization
from workeasy360.platform.identity import lifecycle
from workeasy360.platform.identity.email import is_plausible_email, normalize_email
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.db import organization_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access

ADDRESS_ENV = "PLATFORM_SUPERADMIN_EMAIL"
CREDENTIAL_ENV = "PLATFORM_SUPERADMIN_PASSWORD"
REASON = "Platform Super Admin bootstrapped from deployment configuration"
ROLE = "SUPER_ADMIN"


class Command(BaseCommand):
    help = (
        "Create or confirm the platform Super Admin named by "
        f"{ADDRESS_ENV} (and {CREDENTIAL_ENV} for a new account). Idempotent."
    )

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen and change nothing.",
        )

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        address = normalize_email(os.environ.get(ADDRESS_ENV, ""))
        if not address or not is_plausible_email(address):
            raise CommandError(f"Set {ADDRESS_ENV} to the Super Admin's email address.")
        if address == PLATFORM_SYSTEM_USER_EMAIL:
            raise CommandError("That address belongs to the control plane itself.")
        role = staff_roles.role(ROLE)
        assert role is not None  # declared in code

        with unscoped_access(
            reason="Operator bootstraps the Super Admin", module=__name__
        ):
            existing = User.objects.filter(email=address).first()
        if options["dry_run"]:
            self.stdout.write(self._plan(existing, role))
            return

        ensure_platform_organization()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    ["w360:bootstrap-superadmin"],
                )
            with unscoped_access(
                reason="Operator bootstraps the Super Admin", module=__name__
            ):
                target = lifecycle.lock_by_email(address)
                holders = {
                    g["user_id"]
                    for g in staff.live_grants(limit=500)
                    if g["permission"] == staff.SECURITY_ADMIN
                }
                if holders and (target is None or str(target.id) not in holders):
                    raise CommandError(
                        "A different security administrator already exists. Grant "
                        "access in the Super Admin console instead."
                    )
                created = target is None
                if created:
                    target = self._create(address)
                elif target.status != UserStatus.ACTIVE.value:
                    raise CommandError(
                        "That account exists but is not active. Reactivate it in the "
                        "Super Admin console, or choose another address."
                    )
                lifecycle.set_staff(target, True)
                system = User.objects.get(email=PLATFORM_SYSTEM_USER_EMAIL)
            added = []
            for permission in sorted(role.permissions):
                _grant, was_created = staff.create_grant(
                    user_id=target.id,
                    permission=permission,
                    granted_by_id=system.id,
                    reason=REASON,
                )
                if was_created:
                    added.append(permission)
            if created or added:
                with organization_db_scope(PLATFORM_ORGANIZATION_ID):
                    audit.record_system(
                        PLATFORM_ORGANIZATION_ID,
                        "staff.bootstrap_superadmin",
                        resource_type="identity.user",
                        resource_id=str(target.id),
                        changes={
                            "role": ROLE,
                            "account_created": created,
                            "added": added,
                        },
                        reason=REASON,
                    )
        if created:
            self.stdout.write(f"Created {address} and granted the Super Admin role.")
        elif added:
            self.stdout.write(
                f"{address} now holds the Super Admin role ({len(added)} grants added). "
                "Its password was not changed."
            )
        else:
            self.stdout.write(f"{address} is already the Super Admin. Nothing changed.")

    def _create(self, address: str) -> User:
        from workeasy360.platform.identity.account import check_new_password

        password = os.environ.get(CREDENTIAL_ENV, "")
        if not password:
            raise CommandError(
                f"{address} has no account yet: set {CREDENTIAL_ENV} to create it."
            )
        candidate = User(email=address, full_name="Platform Super Admin")
        try:
            check_new_password(password, user=candidate)
        except Exception as exc:
            # Never echo the password, and never the validator's own message
            # verbatim in case a future validator quotes its input.
            raise CommandError(
                f"{CREDENTIAL_ENV} does not meet the platform password policy."
            ) from exc
        return lifecycle.create_account(
            email=address,
            password=password,
            full_name="Platform Super Admin",
            verified=True,
            source=User.AcquisitionSource.OPERATOR,
        )

    def _plan(self, existing: User | None, role: staff_roles.StaffRole) -> str:
        if existing is None:
            return (
                f"Would create the account and grant the {ROLE} role "
                f"({len(role.permissions)} permissions)."
            )
        held = {
            g["permission"] for g in staff.live_grants(user_id=existing.id, limit=500)
        }
        missing = role.permissions - held
        if not missing:
            return "The account already holds the Super Admin role. Nothing to do."
        return (
            f"Would grant {len(missing)} missing permission(s) to the existing account."
        )
