"""Create, migrate and seed the scratch database for the Digital Store check.

    CHECK_PASSWORD=<random> python scripts/store_domain_check/prepare.py   # w360_store_fresh
    python scripts/store_domain_check/prepare.py --drop                    # remove it

The User Admin check's seed (customer, wallets, Easy CRM in INR, the test provider),
then the Store's own additions (``store_seed.py``). Scratch only - the local Docker
PostgreSQL on 5442, never ``workeasy360`` or the test database. The journeys buy and
recharge, so re-create the database before every run, and never while a backend
pytest run is going.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

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

DB = "w360_store_fresh"
STAFF = "staff@eco.test"
REDIS_URL = "redis://127.0.0.1:6392/13"
UAP = HERE.parent / "user_admin_check"


def backend_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=os.pathsep.join([str(ROOT / "backend"), str(HERE), str(UAP)]),
        DJANGO_SETTINGS_MODULE="w360_store_settings",
        W360_DB_NAME=DB,
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_POOL_MODE="direct",
        W360_REDIS_URL=REDIS_URL,
        W360_PARTNER_KYC_REQUIRED="true",
        W360_PAYMENTS_FAKE_PROVIDER="true",
        PLATFORM_SUPERADMIN_EMAIL=STAFF,
        PLATFORM_SUPERADMIN_PASSWORD=os.environ.get("CHECK_PASSWORD", ""),
    )
    return env


def _manage(*args: str) -> None:
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


def _script(path: Path) -> int:
    proc = subprocess.run(  # noqa: S603 - this repository's own interpreter and script
        [PY, str(path)],
        env=backend_env(),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    print(proc.stdout)
    if proc.returncode:
        print(proc.stderr[-4000:])
    return proc.returncode


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
    _manage("bootstrap_superadmin")
    _manage("register_products", "--actor", STAFF, "--activate-integrations")
    return _script(UAP / "seed.py") or _script(HERE / "store_seed.py")


if __name__ == "__main__":
    raise SystemExit(main())
