"""Configuration and composition. No application code lives here.

The Celery application is loaded with the package - Celery's documented
Django integration - so that every process importing Django settings binds
``shared_task`` to *this* app and its configuration. Without it a web
process (``wsgi.py``/``asgi.py`` never import ``config.celery`` themselves)
enqueued through Celery's unconfigured default app and its default broker,
and every after-commit enqueue failed silently, leaving the work to the next
sweep. Found in Phase 6, where the integration pipeline's latency depends on
that enqueue succeeding.
"""

from config.celery import app as celery_app

__all__ = ("celery_app",)
