"""Migration rehearsal and seed for the Admin Panel browser check (2026-11-09).

    CHECK_PASSWORD=... python scripts/admin_panel_check/prepare.py
    CHECK_PASSWORD=... python scripts/admin_panel_check/prepare.py --skip-rehearsal
    python scripts/admin_panel_check/prepare.py --drop

1. **The live release** (``PREVIOUS_RELEASE``, from ``git archive``) migrates a scratch
   database and writes the history production holds.
2. Every row of the tables these migrations touch is fingerprinted.
3. **The working tree** applies its migrations. The fingerprints must be identical, the
   existing partners must still be active with KYC in draft (no forged approvals) and the
   projections must hold what the tenant tables hold.
4. The new migrations are reversed and re-applied. The fingerprints again.
5. ``seed.py`` adds the data the browser journeys use.

Scratch only (the local Docker PostgreSQL on 5442): never ``workeasy360`` or the test
database. The journeys change data, so re-run before each run.
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

DB = "w360_admin_panel_fresh"
STAFF = "staff@eco.test"
HERE = Path(__file__).resolve().parent
ECOSYSTEM = HERE.parent / "ecosystem_browser_check"

#: The release live before this programme (docs/admin-panel/00-audit-and-plan.md).
PREVIOUS_RELEASE = "b2eb178"

FINGERPRINTS = {
    "commissions_accrual": "id, partner_id, organization_id, kind, amount_minor, currency, source_ref, event_at, rule_id",
    "commissions_rule": "id, code, version, partner_id, product_code, rate_bp, effective_from, retired_at",
    "partners_account": "id, code, display_name, status, payout_currency, default_experience, version",
    "partners_member": "id, partner_id, user_id, role, status",
    "partners_capability_grant": "id, partner_id, capability, status, granted_at, revoked_at",
    "subscriptions_subscription": "id, organization_id, status, currency, activated_at",
    "subscriptions_item": "id, organization_id, product_code, plan_code, total_amount_minor, currency, added_at",
    "catalog_product": "code, name, slug, status, listing",
    "catalog_plan": "id, code, name, status",
    "identity_user": "id, email, status, password",
    "organizations_organization": "id, slug, status",
}

#: (app, the last migration of the live release) in the order that reverses safely.
REVERSE_TO = (
    ("authz", "0009"),
    ("tenancy", "0012"),
    ("wallets", "zero"),
    ("payments", "0006"),
    ("subscriptions", "0004"),
    ("partners", "0016"),
    ("administration", "0001"),
    ("provisioning", "0002"),
    ("catalog", "0011"),
)


#: What this release adds to the Admin role. An Admin who upgrades must hold all of them.
NEW_STAFF_KEYS = (
    "platform.staff_kyc.review",
    "platform.staff_kyc.view",
    "platform.staff_report.view",
    "platform.staff_support.manage",
    "platform.staff_wallet.manage",
    "platform.staff_wallet.view",
)


def live_new_grants() -> int:
    """How many of the new permissions the Admin from the live release holds right now."""
    keys = ", ".join(f"'{k}'" for k in NEW_STAFF_KEYS)
    return q(
        DB,
        "SELECT count(*) FROM authz_staff_grant g JOIN identity_user u ON u.id = g.user_id "
        f"WHERE u.email = '{STAFF}' AND g.revoked_at IS NULL AND g.permission IN ({keys})",
    )[0][0]


def env_for(backend: Path, settings: str = "config.settings.migration") -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{backend}{os.pathsep}{HERE}{os.pathsep}{ECOSYSTEM}",
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


def run(
    backend: Path,
    *args: str,
    script: Path | None = None,
    settings: str = "config.settings.migration",
    extra_env: dict[str, str] | None = None,
) -> str:
    command = [PY, str(script)] if script else [PY, str(backend / "manage.py"), *args]
    proc = subprocess.run(
        command,
        env={**env_for(backend, settings), **(extra_env or {})},
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    label = script.name if script else f"manage.py {' '.join(args)}"
    if proc.returncode:
        print(proc.stdout[-2000:], proc.stderr[-4000:])
        raise SystemExit(f"{label} failed")
    tail = [line for line in proc.stdout.strip().splitlines() if line.strip()][-2:]
    print(f"  {label}: ok", *(f"\n    {line}" for line in tail))
    return proc.stdout


def release_backend(into: Path) -> Path:
    archive = into / "release.zip"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            "-o",
            str(archive),
            PREVIOUS_RELEASE,
            "backend",
            "infra/ecosystem",
        ],
        cwd=ROOT,
        check=True,
    )
    with zipfile.ZipFile(archive) as z:
        z.extractall(into)
    return into / "backend"


def truncate_guards() -> int:
    """tenancy.0016: the wallet ledger and the KYC logs refuse TRUNCATE (2026-11-10)."""
    return q(
        DB,
        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN ("
        "'wallets_entry_reject_truncate', 'partners_kyc_decision_reject_truncate', "
        "'partners_kyc_document_access_reject_truncate')",
    )[0][0]


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
    scratch = Path(tempfile.mkdtemp(prefix="w360-admin-panel-"))
    try:
        if args.skip_rehearsal:
            run(new, "migrate", "--noinput")
            run(new, "bootstrap_superadmin")
            run(new, "register_products", "--actor", STAFF, "--activate-integrations")
        else:
            print(f"== 1. the live release ({PREVIOUS_RELEASE}) builds today's data")
            old = release_backend(scratch)
            run(old, "migrate", "--noinput")
            run(old, "bootstrap_superadmin")
            run(old, "register_products", "--actor", STAFF, "--activate-integrations")
            run(old, script=ECOSYSTEM / "seed_history.py")
            before = fingerprints()
            for table, (count, _digest) in before.items():
                print(f"    {table}: {count} rows")
            check("history exists before the upgrade", before["partners_account"][0] >= 1)
            active_before = q(
                DB, "SELECT count(*) FROM partners_account WHERE status = 'active'"
            )[0][0]

            print("== 2. the working tree applies its migrations")
            run(new, "migrate", "--noinput")
            plan = run(new, "showmigrations", "--plan")
            check("no migration left unapplied", "[ ]" not in plan)
            for name in (
                "wallets.0001_admin_panel",
                "payments.0008_wallet_orders",
                "partners.0018_ticket_assignment",
                "administration.0003_help_and_company",
                "tenancy.0015_support_setup_help",
                "tenancy.0016_ledger_truncate_guard",
            ):
                check(f"{name} applied", f"[X]  {name}" in plan)
            check(
                "the ledgers refuse TRUNCATE",
                truncate_guards() == 3,
                f"{truncate_guards()} of 3",
            )
            check(
                "the Admin from the live release holds every new permission",
                live_new_grants() == len(NEW_STAFF_KEYS),
                f"{live_new_grants()} of {len(NEW_STAFF_KEYS)}",
            )

            print("== 3. every existing row is unchanged")
            after = fingerprints()
            for table in FINGERPRINTS:
                check(
                    f"{table} identical",
                    after[table] == before[table],
                    f"{after[table][0]} rows",
                )
            active = q(
                DB, "SELECT count(*) FROM partners_account WHERE status = 'active'"
            )[0][0]
            check(
                "every active partner stays active", active == active_before, f"{active}"
            )
            approved = q(
                DB, "SELECT count(*) FROM partners_kyc WHERE status = 'approved'"
            )[0][0]
            check("no KYC approval was forged", approved == 0, f"{approved}")
            wallets = q(DB, "SELECT count(*) FROM wallets_wallet")[0][0]
            check("no wallet appeared by itself", wallets == 0, f"{wallets}")

            print("== 4. the migrations reverse and re-apply without loss")
            for app, target in REVERSE_TO:
                run(new, "migrate", app, target, "--noinput")
            check("reversed: rows intact", fingerprints() == before)
            check("reversed: the new permissions are withdrawn", live_new_grants() == 0)
            check(
                "reversed: the TRUNCATE guards are gone",
                truncate_guards() == 0,
                truncate_guards(),
            )
            run(new, "migrate", "--noinput")
            check("re-applied: rows intact", fingerprints() == before)
            check(
                "re-applied: the Admin holds every new permission again",
                live_new_grants() == len(NEW_STAFF_KEYS),
            )
            check(
                "re-applied: the ledgers refuse TRUNCATE again",
                truncate_guards() == 3,
                truncate_guards(),
            )

        print("== 5. the acceptance data")
        # The ecosystem seed's partners predate KYC: active with KYC in draft, as the
        # migration leaves the partners production already has.
        out = run(
            new,
            script=ECOSYSTEM / "seed.py",
            extra_env={"W360_PARTNER_KYC_REQUIRED": "false"},
        )
        for line in out.splitlines():
            if line.startswith(("ORG ", "PARTNER ")):
                print("   ", line)
        out = run(new, script=HERE / "seed.py", settings="w360_admin_panel_settings")
        for line in out.splitlines():
            if line.startswith("SEED "):
                print("   ", line)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print("\nREHEARSAL", "PASS" if not failures else f"FAIL: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
