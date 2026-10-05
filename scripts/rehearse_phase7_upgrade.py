"""Phase 6 -> Phase 7 upgrade rehearsal, and a fresh-database verification.

Runs against the local Docker PostgreSQL (direct port), in two **scratch**
databases it creates and drops itself. It never touches ``workeasy360`` or the
test database.

    python scripts/rehearse_phase7_upgrade.py            # both rehearsals
    python scripts/rehearse_phase7_upgrade.py --keep     # leave the databases

What it does
------------
FRESH:   ``migrate`` from an empty database, then verify the database-level
         guarantees Phase 7 depends on (append-only ledger, freeze triggers,
         constraints, indexes, application-role privileges, RLS verification by
         the application's own system check).

UPGRADE: build a database at the **Phase 6** schema (every migration except the
         Phase 7 set), seed it with Phase 6-shaped data (including a price book
         of the kind Phase 7's data migration must handle), run ``migrate``
         forward to Phase 7, and verify: nothing was lost, the data migration
         did its work, the new tables exist, the same guarantees hold, and the
         projection Phase 7 adds (the customer summary) is filled by the normal
         reconciliation.

It also records whether Phase 7 migrations can be reversed, because the honest
rollback answer depends on it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():  # POSIX virtualenv
    PY = str(ROOT / ".venv" / "bin" / "python")

HOST, PORT = "127.0.0.1", 5442
SUPER = dict(user="postgres", password="postgres")
MIGRATOR = dict(user="w360_migrator", password="w360_migrator_pw")
FRESH, UPGRADE = "w360_p7_fresh", "w360_p7_upgrade"

#: The migrations Phase 7 added or changed. Everything else is Phase 6 and earlier.
PHASE7 = {
    ("audit", "0003_alter_auditevent_actor_kind"),
    ("authz", "0005_staffgrant"),
    ("authz", "0006_staffgrant_expires_at"),
    ("commissions", "0001_initial"),
    ("commissions", "0002_initial"),
    ("commissions", "0003_payout_list_keyset_index"),
    ("organizations", "0004_directory_keyset_index"),
    ("partners", "0001_initial"),
    ("partners", "0002_partner_list_keyset_index"),
    ("pricing", "0002_pricebook_partner_id_pricebook_ix_pricebook_partner_and_more"),
    ("provisioning", "0001_initial"),
    ("tenancy", "0006_partner_channel_controls"),
    ("tenancy", "0007_final_audit_controls"),
}

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


# --- plumbing ------------------------------------------------------------------------------


def connect(dbname: str, **who) -> psycopg.Connection:
    return psycopg.connect(host=HOST, port=PORT, dbname=dbname, autocommit=True, **who)


def create_database(name: str) -> None:
    with connect("postgres", **SUPER) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}" OWNER w360_migrator')
    with connect(name, **SUPER) as db:
        # Exactly what infra/postgres/init.sql does for the main database.
        db.execute("ALTER SCHEMA public OWNER TO w360_migrator")
        db.execute("GRANT USAGE ON SCHEMA public TO w360_app")
        db.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO w360_app"
        )
        db.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO w360_app"
        )
        db.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public "
            "GRANT EXECUTE ON FUNCTIONS TO w360_app"
        )


def drop_database(name: str) -> None:
    with connect("postgres", **SUPER) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def django(db: str, code: str, *, settings: str = "config.settings.migration", app_role: bool = False):
    """Run ``code`` inside Django configured for ``db``. Returns (rc, stdout, stderr)."""
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(ROOT / "backend"),
        DJANGO_SETTINGS_MODULE=settings,
        W360_DB_NAME=db,
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),  # straight to PostgreSQL: the scratch DBs are not in PgBouncer
        W360_DB_POOL_MODE="direct",
    )
    if app_role:
        env["W360_DB_POOL_MODE"] = "direct"
    script = "import django; django.setup()\n" + textwrap.dedent(code)
    proc = subprocess.run(
        [PY, "-c", script], env=env, cwd=ROOT, capture_output=True, text=True, timeout=900
    )
    return proc.returncode, proc.stdout, proc.stderr


def manage(db: str, *args: str, settings: str = "config.settings.migration"):
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(ROOT / "backend"),
        DJANGO_SETTINGS_MODULE=settings,
        W360_DB_NAME=db,
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),
        W360_DB_POOL_MODE="direct",
    )
    proc = subprocess.run(
        [PY, str(ROOT / "backend" / "manage.py"), *args],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return proc.returncode, proc.stdout, proc.stderr


# --- verification shared by both rehearsals -------------------------------------------------------


def q(db: str, sql: str, params=(), **who):
    with connect(db, **(who or SUPER)) as conn:
        return conn.execute(sql, params).fetchall()


def verify_phase7_schema(db: str, label: str) -> None:
    tables = {r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    wanted = {
        "authz_staff_grant",
        "provisioning_customer_summary",
        "partners_account",
        "partners_capability_grant",
        "partners_member",
        "partners_organization_link",
        "partners_product_grant",
        "partners_brand",
        "partners_domain",
        "partners_owner_invite",
        "partners_logo_upload",
        "commissions_referral_code",
        "commissions_referral",
        "commissions_rule",
        "commissions_accrual",
        "commissions_payout",
        "commissions_payout_line",
    }
    record(f"{label}: every Phase 7 table exists", wanted <= tables, str(sorted(wanted - tables)))

    indexes = {r[0] for r in q(db, "SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
    for name in ("ix_org_created_id", "ix_payout_created_id", "ix_partner_created_id"):
        record(f"{label}: index {name}", name in indexes)

    constraints = {r[0] for r in q(db, "SELECT conname FROM pg_constraint")}
    for name in (
        "ck_payout_approver_is_not_the_preparer",
        "ck_payout_paid_is_confirmed",
        "ck_item_quantity_positive",
    ):
        record(f"{label}: constraint {name}", name in constraints)

    # The application role can read and write the ledger but never alter or erase it.
    for privilege, expected in (("SELECT", True), ("INSERT", True), ("UPDATE", False), ("DELETE", False), ("TRUNCATE", False)):
        (has,) = q(
            db, "SELECT has_table_privilege('w360_app', 'commissions_accrual', %s)", (privilege,)
        )[0]
        record(f"{label}: app role {privilege} on the ledger is {expected}", has is expected)

    # A trigger blocks mutation even for the table owner (the migrator).
    triggers = q(
        db,
        "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "WHERE c.relname='commissions_accrual' AND NOT t.tgisinternal",
    )
    record(f"{label}: ledger has an append-only trigger", len(triggers) >= 1, str([t[0] for t in triggers]))

    frozen = q(
        db,
        "SELECT c.relname, count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "WHERE NOT t.tgisinternal AND c.relname IN "
        "('commissions_payout_line','commissions_rule','subscriptions_item') GROUP BY 1",
    )
    record(f"{label}: freeze triggers on payout lines, rules, subscription items", len(frozen) == 3, str(frozen))

    # Try the forbidden thing for real, as the owner, inside a rolled-back transaction.
    with connect(db, **SUPER) as conn:
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                pid, part = uuid.uuid4(), uuid.uuid4()
                cur.execute(
                    "INSERT INTO identity_user (id, email, email_original, password, full_name, status, is_staff, is_superuser, "
                    "failed_login_count, session_epoch, mfa_failed_attempts, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), %s, %s, '', '', 'active', false, false, 0, 0, 0, now(), now()) RETURNING id",
                    (f"rehearsal-{str(pid)[:8]}@example.com", f"rehearsal-{str(pid)[:8]}@example.com"),
                )
                (uid,) = cur.fetchone()
                cur.execute(
                    "INSERT INTO partners_account (id, code, display_name, legal_name, contact_name, contact_email, payout_currency, status, version, created_by_id, created_at, updated_at, status_reason) "
                    "VALUES (%s, %s, 'P', '', '', '', 'GBP', 'active', 0, %s, now(), now(), '')",
                    (part, f"r-{str(pid)[:8]}", uid),
                )
                acc = uuid.uuid4()
                cur.execute(
                    "INSERT INTO commissions_accrual (id, partner_id, kind, amount_minor, currency, source_ref, event_at, clears_at, "
                    "basis_currency, attribution_kind, attribution_ref, reason_code, reason, created_at, idempotency_key) "
                    "VALUES (%s, %s, 'accrual', 100, 'GBP', 'r', now(), now(), '', '', '', '', '', now(), %s)",
                    (acc, part, f"rehearsal:{acc}"),
                )
                ok = False
                try:
                    cur.execute("SAVEPOINT s")
                    cur.execute("UPDATE commissions_accrual SET amount_minor = 999 WHERE id = %s", (acc,))
                except psycopg.Error as exc:
                    ok = "append" in str(exc).lower() or "immutable" in str(exc).lower() or "cannot" in str(exc).lower()
                    cur.execute("ROLLBACK TO SAVEPOINT s")
                record(f"{label}: even the owner cannot UPDATE a ledger row", ok)
                ok = False
                try:
                    cur.execute("SAVEPOINT d")
                    cur.execute("DELETE FROM commissions_accrual WHERE id = %s", (acc,))
                except psycopg.Error:
                    ok = True
                    cur.execute("ROLLBACK TO SAVEPOINT d")
                record(f"{label}: even the owner cannot DELETE a ledger row", ok)
                ok = False
                try:
                    cur.execute("SAVEPOINT t")
                    cur.execute("TRUNCATE commissions_accrual")
                except psycopg.Error as exc:
                    ok = "truncate" in str(exc).lower()
                    cur.execute("ROLLBACK TO SAVEPOINT t")
                record(f"{label}: even the owner cannot TRUNCATE the ledger", ok)
                # A staff grant's expiry is one of its frozen terms (final audit).
                cur.execute(
                    "INSERT INTO authz_staff_grant (id, user_id, permission, granted_by_id, granted_at, reason, expires_at, revoke_reason) "
                    "VALUES (gen_random_uuid(), %s, 'platform.staff_audit.view', %s, now(), 'rehearsal grant', now() + interval '1 day', '') RETURNING id",
                    (uid, uid),
                )
                (grant,) = cur.fetchone()
                ok = False
                try:
                    cur.execute("SAVEPOINT g")
                    cur.execute(
                        "UPDATE authz_staff_grant SET expires_at = now() + interval '10 years' WHERE id = %s",
                        (grant,),
                    )
                except psycopg.Error as exc:
                    ok = "cannot be changed" in str(exc).lower()
                    cur.execute("ROLLBACK TO SAVEPOINT g")
                record(f"{label}: a staff grant's expiry cannot be extended in place", ok)
        finally:
            conn.rollback()

    rc, out, err = django(
        db,
        """
        from django.core.management import call_command
        call_command("check", "--fail-level", "WARNING")
        print("SYSTEM-CHECKS-OK")
        """,
        settings="config.settings.development",
    )
    record(
        f"{label}: system checks (incl. RLS verification as the application role)",
        rc == 0 and "SYSTEM-CHECKS-OK" in out,
        (err or out).strip().splitlines()[-1][:160] if rc != 0 else "",
    )


# --- the two rehearsals -----------------------------------------------------------------------------


def fresh() -> None:
    print("\n== FRESH DATABASE ==")
    create_database(FRESH)
    rc, out, err = manage(FRESH, "migrate", "--noinput")
    record("fresh: migrate from an empty database", rc == 0, err.strip().splitlines()[-1][:200] if rc else "")
    if rc:
        return
    rc, out, err = manage(FRESH, "makemigrations", "--check", "--dry-run")
    record("fresh: models match migrations (makemigrations --check)", rc == 0)
    verify_phase7_schema(FRESH, "fresh")


PHASE6_TARGETS = f"""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    P7 = set({sorted(PHASE7)!r})
    executor = MigrationExecutor(connection)
    graph = executor.loader.graph
    targets = []
    for app in sorted({{a for a, _ in graph.nodes}}):
        chain = [n for (a, n) in graph.nodes if a == app and (a, n) not in P7]
        # The highest-numbered non-Phase-7 migration of the app; an app that is
        # entirely Phase 7 (partners, commissions, provisioning) goes to zero.
        targets.append((app, sorted(chain)[-1] if chain else None))
"""


def build_phase6(db: str) -> bool:
    """Migrate every app to its last non-Phase-7 migration."""
    rc, out, err = django(db, PHASE6_TARGETS + """    executor.migrate(targets)
    print('PHASE6-BUILT', len(targets))
""")
    record("upgrade: built a Phase 6-shaped database", rc == 0 and "PHASE6-BUILT" in out, err.strip().splitlines()[-1][:200] if rc else "")
    return rc == 0


def roll_back_to_phase6(db: str) -> None:
    """Actually reverse every Phase 7 migration, then check the schema and data."""
    before = counts(db)
    rc, out, err = django(db, PHASE6_TARGETS + """    executor.migrate(targets)
    print('ROLLED-BACK')
""")
    record("rollback: Phase 7 migrations reverse cleanly", rc == 0 and "ROLLED-BACK" in out, err.strip().splitlines()[-1][:300] if rc else "")
    if rc:
        return
    tables = {r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    left = sorted(t for t in tables if t.startswith(("partners_", "commissions_")) or t in ("authz_staff_grant", "provisioning_customer_summary"))
    record("rollback: every Phase 7 table is gone", not left, str(left))
    record("rollback: pre-Phase-7 data is intact", counts(db) == before, f"{before} -> {counts(db)}")
    rc, out, err = manage(db, "migrate", "--noinput")
    record("rollback: re-applying Phase 7 afterwards succeeds", rc == 0, err.strip().splitlines()[-1][:300] if rc else "")


def seed_phase6(db: str) -> dict:
    """Phase 6-shaped data, inserted with plain SQL because the Phase 7 models
    describe columns this schema does not have yet."""
    with connect(db, **SUPER) as conn:
        user_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO identity_user (id, email, email_original, password, full_name, status, is_staff, is_superuser, "
            "failed_login_count, session_epoch, mfa_failed_attempts, created_at, updated_at) "
            "VALUES (%s, 'owner@rehearsal.example', 'owner@rehearsal.example', '', '', 'active', false, false, 0, 0, 0, now(), now())",
            (user_id,),
        )
        orgs = []
        for i in range(3):
            oid = uuid.uuid4()
            conn.execute(
                "INSERT INTO organizations_organization (id, slug, legal_name, display_name, status, residency_region, shard_key, "
                "suspension_reason, created_at, updated_at, created_by_id) "
                "VALUES (%s, %s, %s, %s, 'active', 'eu-west-1', %s, '', now(), now(), %s)",
                (oid, f"reh-{i}", f"Reh {i}", f"Reh {i}", str(oid), user_id),
            )
            orgs.append(oid)
        books = {}
        for code, channel in (("direct_gbp", "direct"), ("orphan_partner", "partner"), ("reseller_gold", "partner")):
            bid = uuid.uuid4()
            conn.execute(
                "INSERT INTO pricing_price_book (id, code, name, channel, region, currency, priority, valid_from, is_active, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, '', 'GBP', 100, now(), true, now(), now())",
                (bid, code, code, channel),
            )
            books[code] = bid
        events = 0
        for i in range(5):
            conn.execute(
                "INSERT INTO audit_event (id, created_at, updated_at, occurred_at, actor_kind, action, resource_type, resource_id, outcome, "
                "reason, ip_hash, prev_hash, entry_hash, organization_id) "
                "VALUES (gen_random_uuid(), now(), now(), now(), 'user', 'rehearsal.seed', 'test', %s, 'success', '', '', '', %s, %s)",
                (str(i), uuid.uuid4().hex, orgs[0]),
            )
            events += 1
        return {"user": user_id, "orgs": orgs, "books": books, "audit": events}


def counts(db: str) -> dict[str, int]:
    return {
        t: q(db, f"SELECT count(*) FROM {t}")[0][0]
        for t in ("identity_user", "organizations_organization", "pricing_price_book", "audit_event")
    }


def upgrade() -> None:
    print("\n== PHASE 6 -> PHASE 7 UPGRADE ==")
    create_database(UPGRADE)
    if not build_phase6(UPGRADE):
        return
    tables = {r[0] for r in q(UPGRADE, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    record(
        "upgrade: the Phase 6 database has no Phase 7 tables",
        not ({"partners_account", "commissions_accrual", "authz_staff_grant"} & tables),
    )
    seeded = seed_phase6(UPGRADE)
    before = counts(UPGRADE)
    print("  seeded:", before)

    rc, out, err = manage(UPGRADE, "migrate", "--noinput")
    record("upgrade: migrate Phase 6 -> Phase 7", rc == 0, err.strip().splitlines()[-1][:300] if rc else "")
    if rc:
        return
    after = counts(UPGRADE)
    record("upgrade: no existing row was lost", all(after[t] >= before[t] for t in before), f"{before} -> {after}")

    orphan = q(UPGRADE, "SELECT channel, is_active FROM pricing_price_book WHERE id = %s", (seeded["books"]["orphan_partner"],))
    record(
        "upgrade: the orphan partner price book was retired (internal, inactive), not deleted",
        orphan == [("internal", False)],
        str(orphan),
    )
    direct = q(UPGRADE, "SELECT channel, is_active FROM pricing_price_book WHERE id = %s", (seeded["books"]["direct_gbp"],))
    record("upgrade: the direct price book is untouched", direct == [("direct", True)], str(direct))

    rc, out, err = manage(UPGRADE, "makemigrations", "--check", "--dry-run")
    record("upgrade: models match migrations after the upgrade", rc == 0)
    verify_phase7_schema(UPGRADE, "upgrade")

    # The summary projection is empty until the scheduled reconciliation runs; run it once.
    (empty,) = q(UPGRADE, "SELECT count(*) FROM provisioning_customer_summary")[0]
    record("upgrade: the customer summary starts empty (filled by reconciliation)", empty == 0, str(empty))
    rc, out, err = django(
        UPGRADE,
        """
        from workeasy360.commercial.provisioning.tasks import reconcile_all
        print("RECONCILED", reconcile_all())
        """,
        settings="config.settings.development",
    )
    record("upgrade: reconcile_all runs against the upgraded database", rc == 0 and "RECONCILED" in out, (err or out).strip().splitlines()[-1][:200])
    (filled,) = q(UPGRADE, "SELECT count(*) FROM provisioning_customer_summary")[0]
    record("upgrade: the customer summary is filled for every organization", filled >= 3, str(filled))

    # Rollback: which Phase 7 migrations can be reversed?
    print("  rollback probe (each Phase 7 migration reversed on a throw-away copy):")
    reversible(UPGRADE)
    roll_back_to_phase6(UPGRADE)


def reversible(db: str) -> None:
    """Report, per Phase 7 migration, whether Django considers it reversible."""
    code = f"""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    for app, name in {sorted(PHASE7)!r}:
        migration = executor.loader.get_migration(app, name)
        irreversible = [type(op).__name__ for op in migration.operations if not op.reversible]
        print("REV", app, name, "irreversible:" + ",".join(irreversible) if irreversible else "reversible")
    """
    rc, out, err = django(db, code)
    for line in out.splitlines():
        if line.startswith("REV"):
            print("   ", line[4:])
    record("upgrade: rollback reversibility reported", rc == 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--only", choices=("fresh", "upgrade"))
    args = parser.parse_args()
    try:
        if args.only in (None, "fresh"):
            fresh()
        if args.only in (None, "upgrade"):
            upgrade()
    finally:
        if not args.keep:
            for name in (FRESH, UPGRADE):
                drop_database(name)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
