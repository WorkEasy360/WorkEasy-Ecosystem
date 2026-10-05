"""Operator entry point for checking and repairing one organization's
analytics aggregates (Tasks 71, 80).

    manage.py w360_analytics_aggregates <organization-uuid> [--metric KEY]
        [--rebuild] [--fail-on-drift]

Without ``--rebuild`` it is read-only: it reports every bucket whose stored
sum or count differs from what the facts say. ``--rebuild`` recreates the
organization's aggregates inside the retained fact window from the facts,
under the lock the projection takes, so it is safe against a live platform.
Buckets older than the fact window have nothing to rebuild from and are left
as they are.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Reconcile (default) or rebuild an organization's analytics aggregates."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("organization", type=UUID)
        parser.add_argument("--metric", default="", help="Limit to one metric key.")
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Recreate the aggregates from facts instead of only reporting.",
        )
        parser.add_argument(
            "--fail-on-drift",
            action="store_true",
            help="Exit with status 1 if drift is found (without --rebuild).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from workeasy360.contracts.errors import MetricNotFound
        from workeasy360.intelligence.analytics.rebuild import rebuild, reconcile

        organization, metric = options["organization"], options["metric"]
        try:
            if options["rebuild"]:
                written = rebuild(organization, metric_key=metric)
                self.stdout.write(self.style.SUCCESS(f"Rebuilt: {written}"))
                return
            drift = reconcile(organization, metric_key=metric)
        except MetricNotFound as exc:
            raise CommandError(str(exc)) from None
        if not drift:
            self.stdout.write(self.style.SUCCESS("No drift detected."))
            return
        for row in drift[:50]:
            self.stdout.write(f"  {row}")
        self.stdout.write(
            self.style.WARNING(
                f"{len(drift)} bucket(s) differ from their facts. Nothing was "
                "repaired; run again with --rebuild to repair."
            )
        )
        if options["fail_on_drift"]:
            raise CommandError("Analytics aggregate drift detected.")
