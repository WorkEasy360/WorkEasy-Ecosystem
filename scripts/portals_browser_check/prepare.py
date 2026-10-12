"""Create, migrate and seed the scratch database for the multi-portal check.

    python scripts/portals_browser_check/prepare.py          # w360_portals_fresh
    python scripts/portals_browser_check/prepare.py --drop   # remove it

Scratch only (the local Docker PostgreSQL on 5442): never ``workeasy360`` or
the test database. The journeys START trials and BUY products, so re-create
and re-seed before every run.
"""

from __future__ import annotations

import argparse
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

DB = "w360_portals_fresh"
#: A Redis database of its own, so the controlled clock can flush its cache
#: without touching anyone else's (the development stack uses 0).
REDIS_URL = "redis://127.0.0.1:6392/9"


def backend_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{ROOT / 'backend'}{os.pathsep}{ROOT / 'scripts' / 'portals_browser_check'}",
        DJANGO_SETTINGS_MODULE="w360_portals_browser_settings",
        W360_DB_NAME=DB,
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_POOL_MODE="direct",
        W360_REDIS_URL=REDIS_URL,
    )
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true")
    args = parser.parse_args()
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
        [PY, str(Path(__file__).with_name("seed.py"))],
        env=backend_env(),
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
