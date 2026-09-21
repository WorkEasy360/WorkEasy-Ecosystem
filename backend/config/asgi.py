"""ASGI entrypoint.

ASGI with synchronous views by default: Django's ORM is synchronous, and async
views calling it through sync_to_async add thread-pool overhead and subtle
bugs for no gain (10-performance-concurrency.md section 5).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

from django.core.asgi import get_asgi_application

application = get_asgi_application()
