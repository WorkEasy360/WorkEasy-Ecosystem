"""Staging: production, with exactly one stated relaxation (Phase 12).

Everything production refuses, staging refuses - it imports production and
changes nothing else. The single difference is the payment mode:

- **Payments are TEST / MANUAL.** No real payment provider is configured for
  this platform yet (Phase 12, Step 43), so the offline test provider may be
  switched on here with ``W360_PAYMENTS_FAKE_PROVIDER=true``. Production pins
  it off whatever the environment says, and ``ENVIRONMENT`` is ``staging`` so
  every log line, the deep health endpoint and the release banner say which
  kind of money is moving.

Selected with ``DJANGO_SETTINGS_MODULE=config.settings.staging``; migrations
use ``config.settings.migration`` with ``W360_ENVIRONMENT=staging``.
"""

from __future__ import annotations

from config.settings.production import *
from config.settings.production import env

ENVIRONMENT = "staging"

PAYMENTS_FAKE_PROVIDER_ENABLED = env.bool("W360_PAYMENTS_FAKE_PROVIDER", False)

env.require_all(context="staging configuration")
