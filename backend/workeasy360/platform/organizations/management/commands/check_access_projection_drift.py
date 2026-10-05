"""Operator entry point for auditing the access-directory projection.

    manage.py check_access_projection_drift [--batch-size N] [--fail-on-drift]

Read-only (Task 24): reports missing rows, stale rows and rows with wrong
denormalised metadata, and repairs nothing. ``--fail-on-drift`` exits 1 when
any drift is found, for wiring into a scheduled health check - repair is a
separate, explicit step (``rebuild_access_projection`` for missing/wrong
metadata, ``rebuild_access_projection --prune-stale`` for stale rows),
never automatic.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Report drift between the access projection and Membership/Organization."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--fail-on-drift",
            action="store_true",
            help="Exit with status 1 if any drift is found.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from workeasy360.platform.organizations.projection import (
            check_access_projection_drift,
        )

        report = check_access_projection_drift(batch_size=options["batch_size"])
        self.stdout.write(f"Scanned {report.organizations_scanned} organizations.")
        self.stdout.write(f"  missing rows:       {len(report.missing_rows)}")
        self.stdout.write(f"  stale rows:         {len(report.stale_rows)}")
        self.stdout.write(f"  wrong-metadata rows: {len(report.wrong_metadata_rows)}")

        if report.is_clean:
            self.stdout.write(self.style.SUCCESS("No drift detected."))
            return

        self.stdout.write(
            self.style.WARNING(
                "Drift detected. Nothing was repaired. missing/wrong-metadata "
                "rows are fixed by `rebuild_access_projection`; stale rows by "
                "`rebuild_access_projection --prune-stale`."
            )
        )
        if options["fail_on_drift"]:
            raise CommandError("Access projection drift detected.")
