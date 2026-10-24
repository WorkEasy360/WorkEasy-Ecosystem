"""Create, migrate and seed the scratch database for this check, and print the
scratch backend's environment.

    CHECK_PASSWORD=<fresh> python scripts/partner_portal_unification_browser_check/prepare.py
    python scripts/partner_portal_unification_browser_check/prepare.py --env    # the backend's env, as JSON
    python scripts/partner_portal_unification_browser_check/prepare.py --drop

The seed is ../partner_management_browser_check/seed.py (the Super Admin with an
authenticator, two sellable products, the default commission rule) - run here
under the multi-portal settings (../portals_browser_check/
w360_portals_browser_settings.py). The journeys create every partner through
the UI, so re-create and re-seed before every run. Scratch only (the local Docker
PostgreSQL on 5442): never ``workeasy360`` or the test database.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rehearse_phase8_upgrade import (  # noqa: E402
    HOST,
    PORT,
    PY,
    ROOT,
    create_database,
    create_vector_extension,
    drop_database,
    manage,
)

DB = "w360_ppu_fresh"
#: A Redis database of its own (the development stack uses 0, the portals check 9).
REDIS_URL = "redis://127.0.0.1:6392/10"


def backend_env() -> dict[str, str]:
    return {
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT / "backend"), str(ROOT / "scripts" / "portals_browser_check")]
        ),
        "DJANGO_SETTINGS_MODULE": "w360_portals_browser_settings",
        "W360_DB_NAME": DB,
        "W360_DB_HOST": HOST,
        "W360_DB_PORT": str(PORT),
        "W360_DB_DIRECT_HOST": HOST,
        "W360_DB_DIRECT_PORT": str(PORT),
        "W360_DB_POOL_MODE": "direct",
        "W360_REDIS_URL": REDIS_URL,
        "PYTHONUNBUFFERED": "1",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true")
    parser.add_argument("--env", action="store_true")
    args = parser.parse_args()
    if args.env:
        print(json.dumps(backend_env()))
        return 0
    if args.drop:
        drop_database(DB)
        print("dropped", DB)
        return 0
    drop_database(DB)
    create_database(DB)
    create_vector_extension(DB)
    rc, _out, err = manage(DB, "migrate", "--noinput")
    if rc:
        print(err[-2000:])
        return rc
    seeded = subprocess.run(  # noqa: S603 - this repository's own interpreter and script
        [PY, str(ROOT / "scripts" / "partner_management_browser_check" / "seed.py")],
        env={**os.environ, **backend_env()},
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    print(seeded.stdout)
    if seeded.returncode:
        print(seeded.stderr[-4000:])
    return seeded.returncode


if __name__ == "__main__":
    raise SystemExit(main())
