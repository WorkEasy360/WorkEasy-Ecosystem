"""Celery application.

Queue topology is three groups - ``critical``, ``default``, ``heavy`` - per
R-12: the seven-queue bulkhead design in 09-failure-resilience.md is correct
but assumes operational maturity a small team does not yet have.

The property that makes starting small safe is that **routing is declared
per task from day one**. Splitting ``heavy`` into ``imports``, ``ai`` and
``reports`` later is a deployment change (start more workers, add routes), not
a change to any business code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from celery import Celery
from celery.signals import setup_logging

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("workeasy360")
app.config_from_object("django.conf:settings", namespace="CELERY")


@setup_logging.connect
def _configure_logging(**_kwargs: object) -> None:
    """Use our structured logging rather than Celery's own format.

    Without this, worker logs are unstructured text while web logs are JSON,
    and correlating a request with the task it spawned becomes manual work.
    """
    from workeasy360.platform.observability.logging import configure_logging

    configure_logging()


app.autodiscover_tasks()


@app.task(
    bind=True,
    queue="default",
    soft_time_limit=10,
    time_limit=20,
    name="config.debug_task",
)
def debug_task(self) -> str:
    """Smoke test that the broker round-trips and routing works."""
    return f"ok:{self.request.id}"
