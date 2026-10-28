"""Scratch database for the Product Manager check (ADR-051), built as an upgrade.

    CHECK_PASSWORD=... python scripts/product_manager_browser_check/prepare.py
    python scripts/product_manager_browser_check/prepare.py --drop

1. The **committed** code (``git archive HEAD``, extracted to a scratch folder)
   migrates ``w360_pm_fresh``, bootstraps the Super Admin and registers the four
   live products from ``infra/ecosystem/products.json`` - the catalog exactly as
   it is before this change.
2. The working tree's code applies ``catalog.0005_product_manager``.
3. The four products are compared column by column with the snapshot taken in
   step 1; the new columns must hold their defaults (every channel flag on).
4. ``catalog.0005`` is reversed and re-applied, and the products compared again.

Scratch only (the local Docker PostgreSQL on 5442): never ``workeasy360`` or the
test database. The browser journey changes rows, so re-run this before each run.
"""

# Fixed commands and constant SQL only - nothing here comes from input.
# ruff: noqa: S603, S607, S608

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rehearse_phase8_upgrade import (
    HOST,
    PORT,
    PY,
    ROOT,
    create_database,
    create_vector_extension,
    drop_database,
    q,
)

DB = "w360_pm_fresh"
STAFF = "staff@browser.test"
OLD_COLUMNS = "code, name, slug, status, listing, category, icon, tagline, description, marketing_url, sort_order, licensing_mode, activated_at"
NEW_DEFAULTS = (
    "store_title = '' AND store_summary = '' AND application_url = '' AND documentation_url = '' "
    "AND support_url = '' AND customer_store_enabled AND reseller_enabled AND affiliate_enabled "
    "AND white_label_enabled"
)


def env_for(backend: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(backend),
        DJANGO_SETTINGS_MODULE="config.settings.migration",
        W360_DB_NAME=DB,
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_POOL_MODE="direct",
        PLATFORM_SUPERADMIN_EMAIL=STAFF,
        PLATFORM_SUPERADMIN_PASSWORD=os.environ["CHECK_PASSWORD"],
    )
    return env


def run(backend: Path, *args: str) -> None:
    proc = subprocess.run(
        [PY, str(backend / "manage.py"), *args],
        env=env_for(backend),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode:
        print(proc.stdout[-2000:], proc.stderr[-3000:])
        raise SystemExit(f"manage.py {' '.join(args)} failed")
    tail = [line for line in proc.stdout.strip().splitlines() if line.strip()][-3:]
    print(f"  manage.py {' '.join(args)}: ok", *(f"\n    {line}" for line in tail))


def committed_backend(into: Path) -> Path:
    """The backend as committed at HEAD - read-only export, no worktree."""
    archive = into / "head.zip"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            "-o",
            str(archive),
            "HEAD",
            "backend",
            "infra/ecosystem",
        ],
        cwd=ROOT,
        check=True,
    )
    with zipfile.ZipFile(archive) as z:
        z.extractall(into)
    return into / "backend"


def snapshot() -> list[tuple]:
    return q(DB, f"SELECT {OLD_COLUMNS} FROM catalog_product ORDER BY code")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true")
    args = parser.parse_args()
    if args.drop:
        drop_database(DB)
        print("dropped", DB)
        return 0

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(
            f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else "")
        )
        if not ok:
            failures.append(name)

    create_database(DB)
    create_vector_extension(DB)
    scratch = Path(tempfile.mkdtemp(prefix="w360-pm-head-"))
    try:
        print("== 1. the committed code builds today's catalog")
        old = committed_backend(scratch)
        run(old, "migrate", "--noinput")
        run(old, "bootstrap_superadmin")
        run(old, "register_products", "--actor", STAFF, "--activate-integrations")
        before = snapshot()
        print(f"  products before: {[r[0] for r in before]}")
        check("four products exist before the upgrade", len(before) == 4)
        trials_before = q(DB, "SELECT count(*) FROM catalog_product_trial_config")[0][0]
        integrations_before = q(
            DB,
            "SELECT product_code, status FROM ecosystem_product_integration ORDER BY 1",
        )

        print("== 2. the working tree applies catalog.0005_product_manager")
        new = ROOT / "backend"
        run(new, "migrate", "--noinput")
        pending = subprocess.run(
            [PY, str(new / "manage.py"), "showmigrations", "--plan"],
            env=env_for(new),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        ).stdout
        check("no migration left unapplied", "[ ]" not in pending)
        check("catalog.0005 applied", "[X]  catalog.0005_product_manager" in pending)

        print("== 3. the four products are unchanged")
        after = snapshot()
        check("every existing column is identical", after == before)
        defaults = q(DB, f"SELECT count(*) FROM catalog_product WHERE {NEW_DEFAULTS}")[0][
            0
        ]
        check(
            "the new columns hold their defaults (every channel flag on)",
            defaults == len(before),
            f"{defaults}/{len(before)}",
        )
        check(
            "trial configurations kept",
            q(DB, "SELECT count(*) FROM catalog_product_trial_config")[0][0]
            == trials_before,
        )
        check(
            "integrations kept",
            q(
                DB,
                "SELECT product_code, status FROM ecosystem_product_integration ORDER BY 1",
            )
            == integrations_before,
        )
        check(
            "the media table exists and is empty",
            q(DB, "SELECT count(*) FROM catalog_product_media")[0][0] == 0,
        )

        print("== 4. the migration reverses and re-applies without loss")
        run(new, "migrate", "catalog", "0004", "--noinput")
        check("reversed: rows intact", snapshot() == before)
        run(new, "migrate", "--noinput")
        check("re-applied: rows intact", snapshot() == before)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print("\nREHEARSAL", "PASS" if not failures else f"FAIL: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
