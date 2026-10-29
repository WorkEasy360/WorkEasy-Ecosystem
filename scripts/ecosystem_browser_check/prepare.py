"""Migration rehearsal + seed for the ecosystem browser check (ADR-053).

    CHECK_PASSWORD=... python scripts/ecosystem_browser_check/prepare.py
    python scripts/ecosystem_browser_check/prepare.py --drop

1. **The committed code** (``git archive HEAD``) migrates ``w360_eco_fresh``,
   bootstraps the Super Admin, registers the four live products and writes the
   history production holds (``seed_history.py``: partners, members, grants, a
   referral code and captured visits, a provisioned customer, a sale, a ledger
   row, a commission rule).
2. Every existing row of the tables these migrations touch is fingerprinted.
3. **The working tree** applies its migrations (``catalog.0005``,
   ``payments.0003/0004``, ``commissions.0004/0005``, ``partners.0010``); the
   fingerprints must be identical and the new columns must hold their defaults.
4. The new migrations are reversed and re-applied; the fingerprints again.
5. ``seed.py`` adds the acceptance data the browser journeys use.

Scratch only (the local Docker PostgreSQL on 5442): never ``workeasy360`` or
the test database. The journeys buy, sell and upgrade, so re-run before each run.
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

DB = "w360_eco_fresh"
STAFF = "staff@eco.test"
HERE = Path(__file__).resolve().parent

#: (table, the columns that existed before these migrations) - the rows the
#: rehearsal fingerprints. Every column listed is one the migrations must not move.
FINGERPRINTS = {
    "commissions_accrual": "id, partner_id, organization_id, kind, amount_minor, currency, source_ref, event_at, rule_id, rule_version, rate_bp, basis_minor, idempotency_key",
    "commissions_referral_code": "id, partner_id, code, label, status, created_at",
    "commissions_referral": "id, code_id, partner_id, token_hash, status, captured_at, expires_at, converted_at, organization_id",
    "commissions_rule": "id, code, version, partner_id, product_code, rate_bp, effective_from, retired_at",
    "partners_account": "id, code, display_name, status, payout_currency, default_experience, version",
    "partners_member": "id, partner_id, user_id, role, status",
    "partners_capability_grant": "id, partner_id, capability, status, granted_at, revoked_at",
    "partners_product_grant": "id, partner_id, product_id, plan_id, granted_at, revoked_at",
    "partners_organization_link": "id, partner_id, organization_id, status, started_at",
    "partners_upgrade_request": "id, partner_id, to_experience, status, commercial_reference",
    "subscriptions_subscription": "id, organization_id, status, currency, activated_at",
    "subscriptions_item": "id, organization_id, product_code, plan_code, total_amount_minor, currency, added_at",
    "catalog_product": "code, name, slug, status, listing",
    "identity_user": "id, email, status, password",
    "organizations_organization": "id, slug, status",
}
NEW_DEFAULTS = {
    "commissions_accrual": "earning = 'commission' AND product_code = '' AND plan_code = '' AND margin_rule_id IS NULL AND cost_minor IS NULL",
    "commissions_referral": "visitor_hash = '' AND registered_at IS NULL",
    "commissions_referral_code": "product_code = '' AND plan_code = '' AND campaign_id IS NULL",
}


def env_for(backend: Path, settings: str = "config.settings.migration") -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{backend}{os.pathsep}{HERE}",
        DJANGO_SETTINGS_MODULE=settings,
        W360_DB_NAME=DB,
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_POOL_MODE="direct",
        PLATFORM_SUPERADMIN_EMAIL=STAFF,
        PLATFORM_SUPERADMIN_PASSWORD=os.environ["CHECK_PASSWORD"],
        PYTHONUNBUFFERED="1",
    )
    return env


def run(backend: Path, *args: str, script: Path | None = None) -> str:
    command = [PY, str(script)] if script else [PY, str(backend / "manage.py"), *args]
    proc = subprocess.run(
        command,
        env=env_for(backend),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    label = script.name if script else f"manage.py {' '.join(args)}"
    if proc.returncode:
        print(proc.stdout[-2000:], proc.stderr[-4000:])
        raise SystemExit(f"{label} failed")
    tail = [line for line in proc.stdout.strip().splitlines() if line.strip()][-3:]
    print(f"  {label}: ok", *(f"\n    {line}" for line in tail))
    return proc.stdout


def committed_backend(into: Path) -> Path:
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


def fingerprints() -> dict[str, tuple[int, str]]:
    out = {}
    for table, columns in FINGERPRINTS.items():
        rows = q(
            DB,
            f"SELECT count(*), coalesce(md5(string_agg(r::text, '|' ORDER BY r::text)), '') "
            f"FROM (SELECT {columns} FROM {table}) r",
        )[0]
        out[table] = (rows[0], rows[1])
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true")
    parser.add_argument("--skip-rehearsal", action="store_true")
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

    drop_database(DB)
    create_database(DB)
    create_vector_extension(DB)
    new = ROOT / "backend"
    scratch = Path(tempfile.mkdtemp(prefix="w360-eco-head-"))
    try:
        if args.skip_rehearsal:
            run(new, "migrate", "--noinput")
            run(new, "bootstrap_superadmin")
            run(new, "register_products", "--actor", STAFF, "--activate-integrations")
        else:
            print("== 1. the committed code builds today's data")
            old = committed_backend(scratch)
            run(old, "migrate", "--noinput")
            run(old, "bootstrap_superadmin")
            run(old, "register_products", "--actor", STAFF, "--activate-integrations")
            run(old, script=HERE / "seed_history.py")
            before = fingerprints()
            for table, (count, _digest) in before.items():
                print(f"    {table}: {count} rows")
            check(
                "history exists before the upgrade", before["commissions_accrual"][0] >= 1
            )

            print("== 2. the working tree applies its migrations")
            run(new, "migrate", "--noinput")
            plan = run(new, "showmigrations", "--plan")
            check("no migration left unapplied", "[ ]" not in plan)
            for name in (
                "catalog.0005_product_manager",
                "payments.0003_order_cart",
                "payments.0004_order_seller",
                "commissions.0004_rule_recurring",
                "commissions.0005_ecosystem_margins_campaigns",
                "partners.0010_program_fee",
            ):
                check(f"{name} applied", f"[X]  {name}" in plan)

            print("== 3. every existing row is unchanged")
            after = fingerprints()
            for table in FINGERPRINTS:
                check(
                    f"{table} identical",
                    after[table] == before[table],
                    f"{after[table][0]} rows",
                )
            for table, condition in NEW_DEFAULTS.items():
                total = q(DB, f"SELECT count(*) FROM {table}")[0][0]
                defaulted = q(DB, f"SELECT count(*) FROM {table} WHERE {condition}")[0][0]
                check(
                    f"{table}: new columns hold their defaults",
                    defaulted == total,
                    f"{defaulted}/{total}",
                )
            check(
                "the ledger is still append-only (UPDATE refused)",
                _update_refused(),
            )

            print("== 4. the migrations reverse and re-apply without loss")
            for app, target in (
                ("commissions", "0003"),
                ("partners", "0009"),
                ("payments", "0002"),
                ("catalog", "0004"),
            ):
                run(new, "migrate", app, target, "--noinput")
            check("reversed: rows intact", fingerprints() == before)
            run(new, "migrate", "--noinput")
            check("re-applied: rows intact", fingerprints() == before)

        print("== 5. the acceptance data")
        out = run(new, script=HERE / "seed.py")
        for line in out.splitlines():
            if line.startswith(("SEED ", "ORG ", "PARTNER ")):
                print("   ", line)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print("\nREHEARSAL", "PASS" if not failures else f"FAIL: {failures}")
    return 1 if failures else 0


def _update_refused() -> bool:
    """The ledger trigger raises on any UPDATE (even as a superuser): an
    exception here is the pass; a successful UPDATE is the failure."""
    try:
        q(DB, "UPDATE commissions_accrual SET amount_minor = amount_minor")
    except Exception:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
