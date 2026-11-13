"""Create, migrate and seed the scratch database for the Login as Guest check.

    CHECK_PASSWORD=<random> python scripts/guest_login_check/prepare.py   # w360_guest_fresh
    python scripts/guest_login_check/prepare.py --drop                    # remove it

Scratch only (the local Docker PostgreSQL on 5442): never ``workeasy360`` or the test
database. The journeys start and end guest views, so re-create before every run.
Never run it while a backend pytest run is going.
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

DB = "w360_guest_fresh"
STAFF = "staff@eco.test"
REDIS_URL = "redis://127.0.0.1:6392/11"
HERE = Path(__file__).resolve().parent


def backend_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{ROOT / 'backend'}{os.pathsep}{HERE}",
        DJANGO_SETTINGS_MODULE="w360_guest_login_settings",
        W360_DB_NAME=DB,
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_POOL_MODE="direct",
        W360_REDIS_URL=REDIS_URL,
        W360_PARTNER_KYC_REQUIRED="false",
        PLATFORM_SUPERADMIN_EMAIL=STAFF,
        PLATFORM_SUPERADMIN_PASSWORD=os.environ.get("CHECK_PASSWORD", ""),
    )
    return env


def run(*args: str) -> None:
    proc = subprocess.run(  # noqa: S603 - this repository's own interpreter and manage.py
        [PY, str(ROOT / "backend" / "manage.py"), *args],
        env=backend_env(),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    print(proc.stdout[-1500:])
    if proc.returncode:
        print(proc.stderr[-3000:])
        raise SystemExit(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true")
    args = parser.parse_args()
    if args.drop:
        drop_database(DB)
        print("dropped", DB)
        return 0
    if not os.environ.get("CHECK_PASSWORD"):
        print("Set CHECK_PASSWORD to a fresh random value.")
        return 2
    drop_database(DB)
    create_database(DB)
    create_vector_extension(DB)
    rc, _out, err = manage(DB, "migrate", "--noinput")
    if rc:
        print(err[-2000:])
        return rc
    run("bootstrap_superadmin")
    run("register_products", "--actor", STAFF, "--activate-integrations")
    seeded = subprocess.run(  # noqa: S603 - this repository's own interpreter and script
        [PY, str(HERE / "seed.py")],
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
