"""Create, migrate and seed the scratch database for this check.

    python scripts/partner_setup_browser_check/prepare.py          # w360_psetup_fresh
    python scripts/partner_setup_browser_check/prepare.py --drop   # remove it

Scratch only (the local Docker PostgreSQL on 5442): never ``workeasy360`` or
the test database. The journeys CREATE partners, a domain and a brand, so
re-create and re-seed before every run.
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

DB = "w360_psetup_fresh"


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
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{ROOT / 'backend'}{os.pathsep}{ROOT / 'scripts' / 'extension_browser_check'}",
        DJANGO_SETTINGS_MODULE="w360_extension_browser_settings",
        W360_DB_NAME=DB,
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_POOL_MODE="direct",
    )
    seeded = subprocess.run(
        [PY, str(Path(__file__).with_name("seed.py"))],
        env=env,
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
