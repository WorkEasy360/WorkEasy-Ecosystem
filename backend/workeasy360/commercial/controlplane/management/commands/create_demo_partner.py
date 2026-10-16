"""Create (or confirm) the development demo partner and its owner login.

    python manage.py create_demo_partner [--currency INR] [--reset-password]

Development only: it refuses to run in production or staging. The owner's
temporary password is generated from the operating system's random source and
shown **once**, here, in an interactive terminal - it is never written to a file,
the log, the repository or the documentation, and the command will not create an
account whose password it could not show. The first sign-in must change it.
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand, CommandError

from workeasy360.commercial.controlplane import demo_partner


class Command(BaseCommand):
    help = "Create the development-only demo partner and its owner (never in production)."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--currency",
            default="INR",
            help="The payout currency of a new demo partner (default INR).",
        )
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Give the existing demo owner a new temporary password.",
        )

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        try:
            demo_partner.assert_development()
        except demo_partner.DemoPartnerRefused as exc:
            raise CommandError(str(exc)) from exc
        reset = bool(options["reset_password"])
        needs_password = demo_partner.will_set_password(reset_password=reset)
        if needs_password and not sys.stdout.isatty():
            raise CommandError(
                "A temporary password would be generated, and it is shown once in "
                "an interactive terminal only. Run this command in your own "
                "terminal (output that is piped or redirected is refused, so the "
                "password cannot end up in a file or a log)."
            )
        result = demo_partner.create_demo_partner(
            currency=str(options["currency"]), reset_password=reset
        )
        created = "created" if result.partner_created else "confirmed"
        self.stdout.write(f"Demo partner {created}: {demo_partner.DEMO_CODE}")
        self.stdout.write(f"Sign-in email: {demo_partner.DEMO_EMAIL}")
        if result.temporary_password:
            self.stdout.write("")
            self.stdout.write("Temporary password (shown ONCE, not stored anywhere):")
            self.stdout.write(f"    {result.temporary_password}")
            self.stdout.write("You must choose a new password at your first sign-in.")
        else:
            self.stdout.write(
                "The owner's password was not changed (use --reset-password for a "
                "new temporary one)."
            )
