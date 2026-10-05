"""Operator entry point for reconstructing the access-directory projection.

    manage.py rebuild_access_projection [--batch-size N] [--prune-stale]

Safe to run at any time, including against a live platform: every write is
the same idempotent upsert the transactional sync path uses
(``platform.organizations.projection.rebuild_access_projection``), rows
already correct are not rewritten, and a failure on one organization is
caught and counted rather than aborting the run (Task 23). Deletes nothing
unless ``--prune-stale`` is given, and then only rows whose authoritative
membership is no longer active.

Typical uses: after introducing the projection against an existing database
(the backfill this command doubles as - Task 61/62), after restoring from a
backup, or as a scheduled reconciliation job alongside
``check_access_projection_drift``.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Rebuild the organization-access projection from Membership/Organization."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Organizations processed per batch (default 500).",
        )
        parser.add_argument(
            "--prune-stale",
            action="store_true",
            help="Also delete rows whose membership is no longer active.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from workeasy360.platform.organizations.projection import (
            rebuild_access_projection,
        )

        stats = rebuild_access_projection(
            batch_size=options["batch_size"], prune_stale=options["prune_stale"]
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Scanned {stats.organizations_scanned} organizations "
                f"({stats.organizations_failed} failed), "
                f"wrote {stats.rows_written} access rows, "
                f"pruned {stats.rows_pruned}."
            )
        )
        if stats.organizations_failed:
            self.stdout.write(
                self.style.WARNING(
                    "Some organizations failed - see logs for "
                    "projection.rebuild.organization_failed entries. Re-run "
                    "is safe; failures do not block the rest of the batch."
                )
            )
