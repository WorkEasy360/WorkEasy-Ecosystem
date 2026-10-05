"""Refuse to start a container that must not serve (Phase 12, final audit).

    python manage.py w360_startup_gate && exec gunicorn ...

Runs the startup contract (``health.startup_failures``: database reachable,
migrations applied, registries loaded, the runtime role cannot bypass RLS and
owns no table, the database timeouts are in force) and exits non-zero if any
check fails. The compose topology runs it before gunicorn and before every
Celery worker, so a misconfigured role - or a database the container cannot
verify - stops the deploy instead of serving traffic without tenant
isolation. The same checks answer ``/healthz/startup``.

A database that is merely still starting is waited for, briefly; a failure
that persists (a role that can bypass RLS does not fix itself) ends it.
"""

from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Exit non-zero unless the startup checks pass (RLS role, ownership, timeouts, migrations)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--wait",
            type=float,
            default=30.0,
            help="Seconds to keep retrying while a check fails (default 30).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from workeasy360.platform.observability.health import startup_failures

        deadline = time.monotonic() + max(0.0, options["wait"])
        while True:
            failed = startup_failures()
            # Never hand an open connection to the process this one execs.
            connections.close_all()
            if not failed:
                self.stdout.write("w360 startup gate: all startup checks passed")
                return
            if time.monotonic() >= deadline:
                raise CommandError(
                    "w360 startup gate: refusing to start - failed: "
                    + ", ".join(failed)
                    + " (details: /healthz/startup and the healthz.check_failed log lines)"
                )
            time.sleep(2)
