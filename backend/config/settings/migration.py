"""Settings for running migrations. Never used to serve traffic.

Two differences from the runtime settings, both required:

1. **The owner role.** The application connects as ``w360_app``, which is a
   non-owner with NOBYPASSRLS and therefore cannot create or alter tables.
   That is the point (ADR-009): if the application could own its tables it
   would bypass RLS unless FORCE were set, and a single missed FORCE would
   silently disable tenant isolation everywhere.

2. **A direct connection, bypassing PgBouncer.** ``CREATE DATABASE`` and
   several DDL statements cannot run inside the transaction a
   transaction-mode pooler wraps them in, and migrations legitimately need
   session-level state that transaction pooling discards.

Selected with::

    DJANGO_SETTINGS_MODULE=config.settings.migration python manage.py migrate

and run as a separate pre-deploy task, never from an application container at
boot - concurrent boots would race (13-deployment-evolution.md section 2.3).
"""

from __future__ import annotations

import os

# The base environment is chosen by W360_ENVIRONMENT so that a production
# migration inherits production's settings and its configuration validation,
# rather than silently migrating with development defaults.
_ENVIRONMENT = os.environ.get("W360_ENVIRONMENT", "development")

if _ENVIRONMENT == "production":
    from config.settings.production import *
    from config.settings.production import DATABASES, env
elif _ENVIRONMENT == "staging":
    from config.settings.staging import *
    from config.settings.staging import DATABASES, env
elif _ENVIRONMENT == "test":
    from config.settings.test import *
    from config.settings.test import DATABASES, env
else:
    from config.settings.development import *
    from config.settings.development import DATABASES, env

DATABASES["default"]["USER"] = env.str("W360_DB_MIGRATOR_USER", "w360_migrator")
# Required in production, and never the development default (Phase 12, G-06):
# it used to fall back to the local password silently.
DATABASES["default"]["PASSWORD"] = env.secret(
    "W360_DB_MIGRATOR_PASSWORD",
    None if _ENVIRONMENT in ("production", "staging") else "w360_migrator_pw",
    required=_ENVIRONMENT in ("production", "staging"),
)
env.check(
    _ENVIRONMENT not in ("production", "staging")
    or DATABASES["default"]["PASSWORD"] != "w360_migrator_pw",  # noqa: S105 - comparison
    "W360_DB_MIGRATOR_PASSWORD is still the local development default.",
)
DATABASES["default"]["HOST"] = env.str("W360_DB_DIRECT_HOST", "127.0.0.1")
DATABASES["default"]["PORT"] = env.int("W360_DB_DIRECT_PORT", 5442)

# DDL can legitimately take longer than an interactive query. A 10s statement
# timeout would abort a CREATE INDEX on a large table partway through.
DATABASES["default"]["OPTIONS"] = {
    "connect_timeout": 5,
    "options": "-c lock_timeout=30000",
    "prepare_threshold": None,
}

env.require_all(context="migration configuration")
