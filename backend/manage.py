#!/usr/bin/env python
"""Django management entrypoint.

Note the default settings module is *development*. There is no default that
resolves to production: a command run without an explicit setting should be
harmless, not aimed at the live database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    backend_dir = Path(__file__).resolve().parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - environment problem
        raise ImportError(
            "Django is not importable. Activate the virtualenv "
            "(.venv/Scripts/activate on Windows, .venv/bin/activate "
            "elsewhere) and run: pip install -r requirements-dev.txt"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
