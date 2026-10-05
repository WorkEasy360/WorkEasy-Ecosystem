"""Phase 7 -> Phase 8 upgrade rehearsal, and a fresh-database verification.

Runs against the local Docker PostgreSQL (direct port) in two **scratch**
databases it creates and drops. It never touches ``workeasy360`` or the test
database.

    python scripts/rehearse_phase8_upgrade.py            # both rehearsals
    python scripts/rehearse_phase8_upgrade.py --keep     # leave the databases

Both scratch databases are created from ``template0`` - **without** pgvector -
because that is what an existing deployment's database looks like, and the
extension step is part of what is being rehearsed:

FRESH:   ``migrate`` from empty must stop at the retrieval tables with the
         operator instruction; after a superuser runs ``CREATE EXTENSION
         vector`` it must complete. Then every Phase 8 guarantee is checked in
         the database itself: RLS (enabled, forced, a policy) on every new
         tenant table, composite foreign keys, freeze triggers that refuse even
         the table owner, application-role privileges, the vector column, the
         seeded registry, and the application's own system checks (which verify
         RLS as the application role).

UPGRADE: build a database at the **Phase 7** schema, seed it with customers,
         memberships, a partner, a commission, a payout, a subscription,
         entitlements and routed integration events, checksum every existing
         table, run ``migrate`` to Phase 8, and prove **no row changed or was
         lost** - byte for byte. Then reverse every Phase 8 migration for real,
         prove the Phase 7 data is still identical, and re-apply.
"""

from __future__ import annotations

import argparse
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
FRESH, UPGRADE = "w360_p8_fresh", "w360_p8_upgrade"

#: The migrations Phase 8 added. Everything else is Phase 7 and earlier.
PHASE8 = {
    ("ai", "0001_initial"),
    ("ai", "0002_seed_registry"),
    ("analytics", "0001_initial"),
    ("rag", "0001_initial"),
    ("authz", "0007_intelligence_permissions"),
    ("tenancy", "0008_intelligence_isolation"),
}

TENANT_TABLES = [
    "ai_org_policy",
    "ai_budget_period",
    "ai_execution",
    "ai_usage_line",
    "ai_execution_content",
    "rag_source",
    "rag_document",
    "rag_document_version",
    "rag_chunk",
    "analytics_fact",
    "analytics_aggregate",
]
CONTROL_PLANE_TABLES = [
    "ai_provider",
    "ai_model",
    "ai_model_price",
    "ai_skill",
    "ai_skill_version",
    "analytics_metric",
]
FROZEN_TABLES = [
    "ai_model_price",
    "ai_skill_version",
    "analytics_metric",
    "ai_execution",
    "ai_usage_line",
    "rag_document_version",
    "rag_chunk",
    "analytics_fact",
]
COMPOSITE_FKS = [
    "fk_ai_usage_line_execution_id_tenant",
    "fk_ai_execution_content_execution_id_tenant",
    "fk_rag_document_source_id_tenant",
    "fk_rag_document_version_document_id_tenant",
    "fk_rag_chunk_document_id_tenant",
    "fk_rag_chunk_version_id_tenant",
]
#: Phase 7 tables whose rows must survive the upgrade unchanged, byte for byte.
GUARDED = [
    "identity_user",
    "organizations_organization",
    "organizations_membership",
    "partners_account",
    "commissions_accrual",
    "commissions_payout",
    "subscriptions_subscription",
    "entitlements_entitlement",
    "entitlements_snapshot",
    "events_routed",
    "audit_event",
    "authz_role",
    "authz_role_permission",
]

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
        # template0: no pgvector, as on a database created before Phase 8.
        admin.execute(f'CREATE DATABASE "{name}" OWNER w360_migrator TEMPLATE template0')
    with connect(name, **SUPER) as db:
        db.execute("ALTER SCHEMA public OWNER TO w360_migrator")
        db.execute("GRANT USAGE ON SCHEMA public TO w360_app")
        for grant in (
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO w360_app",
            "GRANT USAGE, SELECT ON SEQUENCES TO w360_app",
            "GRANT EXECUTE ON FUNCTIONS TO w360_app",
        ):
            db.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public {grant}")


def create_vector_extension(name: str) -> None:
    """The one superuser step of the upgrade runbook."""
    with connect(name, **SUPER) as db:
        db.execute("CREATE EXTENSION IF NOT EXISTS vector")


def drop_database(name: str) -> None:
    with connect("postgres", **SUPER) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _env(db: str, settings: str) -> dict[str, str]:
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
    return env


def django(db: str, code: str, *, settings: str = "config.settings.migration"):
    script = "import django; django.setup()\n" + textwrap.dedent(code)
    proc = subprocess.run(
        [PY, "-c", script], env=_env(db, settings), cwd=ROOT, capture_output=True, text=True, timeout=900
    )
    return proc.returncode, proc.stdout, proc.stderr


def manage(db: str, *args: str, settings: str = "config.settings.migration"):
    proc = subprocess.run(
        [PY, str(ROOT / "backend" / "manage.py"), *args],
        env=_env(db, settings),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return proc.returncode, proc.stdout, proc.stderr


def q(db: str, sql: str, params=()):
    with connect(db, **SUPER) as conn:
        return conn.execute(sql, params).fetchall()


def last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:240] if lines else ""


# --- verification ------------------------------------------------------------------------------


def verify_phase8_schema(db: str, label: str) -> None:
    tables = {r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    wanted = set(TENANT_TABLES) | set(CONTROL_PLANE_TABLES)
    record(f"{label}: every Phase 8 table exists", wanted <= tables, str(sorted(wanted - tables)))

    rls = dict(
        (r[0], (r[1], r[2]))
        for r in q(
            db,
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(%s)",
            (TENANT_TABLES,),
        )
    )
    policies = {r[0] for r in q(db, "SELECT tablename FROM pg_policies WHERE schemaname='public'")}
    bad = [t for t in TENANT_TABLES if rls.get(t) != (True, True) or t not in policies]
    record(f"{label}: RLS enabled, forced and with a policy on every tenant table", not bad, str(bad))
    open_cp = [t for t in CONTROL_PLANE_TABLES if rls.get(t, (False, False))[0]]
    record(f"{label}: the control-plane tables carry no tenant policy", not open_cp, str(open_cp))

    constraints = {r[0] for r in q(db, "SELECT conname FROM pg_constraint")}
    missing = [c for c in COMPOSITE_FKS if c not in constraints]
    record(f"{label}: every composite foreign key exists", not missing, str(missing))

    triggers = {
        r[0]
        for r in q(
            db,
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relname = ANY(%s)",
            (FROZEN_TABLES,),
        )
    }
    record(f"{label}: freeze triggers on every immutable table", triggers == set(FROZEN_TABLES), str(sorted(set(FROZEN_TABLES) - triggers)))

    (vector_type,) = q(
        db,
        "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = 'rag_chunk' AND a.attname = 'embedding'",
    )[0]
    record(f"{label}: chunk embeddings are pgvector columns", vector_type == "vector", vector_type)

    for table in ("ai_model", "ai_skill_version", "analytics_metric"):
        grants = {
            p: q(db, "SELECT has_table_privilege('w360_app', %s, %s)", (table, p))[0][0]
            for p in ("SELECT", "INSERT", "UPDATE", "TRUNCATE")
        }
        ok = grants["SELECT"] and grants["INSERT"] and grants["UPDATE"] and not grants["TRUNCATE"]
        record(f"{label}: app role DML, not TRUNCATE, on {table}", ok, str(grants))

    seeded = q(db, "SELECT key, status FROM ai_model ORDER BY key")
    record(
        f"{label}: the registry is seeded (generation + embeddings active, cheaper models off)",
        ("anthropic.claude-opus-5", "active") in seeded
        and ("openai.text-embedding-3-small", "active") in seeded
        and ("anthropic.claude-haiku-4-5", "disabled") in seeded,
        str(seeded),
    )
    granted = q(
        db,
        "SELECT r.key, rp.permission_id FROM authz_role_permission rp JOIN authz_role r ON r.id = rp.role_id "
        "WHERE r.organization_id IS NULL AND rp.permission_id LIKE 'platform.a%%' ORDER BY 1, 2",
    )
    record(
        f"{label}: owner and admin hold the AI and analytics permissions",
        {("admin", "platform.ai.manage"), ("owner", "platform.analytics.view")} <= set(granted),
        str(granted),
    )

    # Immutability, attempted for real as the table owner inside a rolled-back
    # transaction: a published price version is history.
    with connect(db, **SUPER) as conn:
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                ok = False
                try:
                    cur.execute("SAVEPOINT s")
                    cur.execute("UPDATE ai_model_price SET input_micros_per_mtok = 0")
                except psycopg.Error as exc:
                    ok = "cannot be changed" in str(exc).lower()
                    cur.execute("ROLLBACK TO SAVEPOINT s")
                record(f"{label}: even the owner cannot re-price history", ok)
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
        last_line(err or out) if rc else "",
    )


def checksums(db: str) -> dict[str, tuple[int, str]]:
    """Row count and an md5 over every row's full text, per guarded table.
    As the superuser, so RLS does not hide anything."""
    out = {}
    for table in GUARDED:
        (count, digest) = q(
            db,
            f"SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '') "  # noqa: S608 - fixed table list
            f"FROM {table} t",
        )[0]
        out[table] = (count, digest)
    return out


# --- the rehearsals -------------------------------------------------------------------------------


def fresh() -> None:
    print("\n== FRESH DATABASE ==")
    create_database(FRESH)
    rc, out, err = manage(FRESH, "migrate", "--noinput")
    refused = rc != 0 and "CREATE EXTENSION vector" in (err + out)
    record("fresh: migrate refuses without pgvector and names the operator step", refused, last_line(err))
    create_vector_extension(FRESH)
    rc, out, err = manage(FRESH, "migrate", "--noinput")
    record("fresh: migrate completes once the extension exists", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    rc, out, err = manage(FRESH, "makemigrations", "--check", "--dry-run")
    record("fresh: models match migrations (makemigrations --check)", rc == 0)
    verify_phase8_schema(FRESH, "fresh")


PHASE7_TARGETS = f"""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    P8 = set({sorted(PHASE8)!r})
    executor = MigrationExecutor(connection)
    graph = executor.loader.graph
    targets = []
    for app in sorted({{a for a, _ in graph.nodes}}):
        chain = [n for (a, n) in graph.nodes if a == app and (a, n) not in P8]
        targets.append((app, sorted(chain)[-1] if chain else None))
"""


PHASE8_ROLE_PERMISSIONS = ("platform.ai.view", "platform.ai.manage", "platform.analytics.view")


def unseed_phase8_grants(db: str) -> None:
    """Make the built database what Phase 7 code actually left behind.

    ``authz.0002`` seeds the system roles from the *live* registry, so
    building "Phase 7" with this checkout already grants owner and admin the
    Phase 8 permissions - which a real Phase 7 database does not have, and
    which would hide what ``authz.0007`` adds (and make its reversal look
    like data loss). Remove them, and the catalog rows, before seeding."""
    with connect(db, **SUPER) as conn:
        conn.execute(
            "DELETE FROM authz_role_permission WHERE permission_id = ANY(%s)",
            (list(PHASE8_ROLE_PERMISSIONS),),
        )
        conn.execute(
            "DELETE FROM authz_permission WHERE key = ANY(%s)",
            (list(PHASE8_ROLE_PERMISSIONS),),
        )


def seed_phase7(db: str) -> None:
    """Phase 7-shaped data, in plain SQL: customers and members, a partner, a
    commission and a payout, a subscription, entitlements, routed events."""
    with connect(db, **SUPER) as conn:
        user_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO identity_user (id, email, email_original, password, full_name, status, is_staff, is_superuser, "
            "failed_login_count, session_epoch, mfa_failed_attempts, created_at, updated_at) "
            "VALUES (%s, 'owner@p8.example', 'owner@p8.example', '', 'Owner', 'active', false, false, 0, 0, 0, now(), now())",
            (user_id,),
        )
        partner_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO partners_account (id, code, display_name, legal_name, contact_name, contact_email, payout_currency, "
            "status, version, created_at, updated_at, status_reason, created_by_id) "
            "VALUES (%s, 'p8-partner', 'Partner', 'Partner Ltd', 'Pat', 'pat@partner.example', 'GBP', 'active', 1, now(), now(), '', %s)",
            (partner_id, user_id),
        )
        conn.execute(
            "INSERT INTO commissions_accrual (id, partner_id, kind, amount_minor, currency, source_ref, event_at, clears_at, "
            "basis_currency, attribution_kind, attribution_ref, reason_code, reason, created_at, idempotency_key) "
            "VALUES (gen_random_uuid(), %s, 'accrual', 1250, 'GBP', 'sale:1', now(), now(), 'GBP', 'link', 'l1', '', '', now(), 'p8:accrual:1')",
            (partner_id,),
        )
        conn.execute(
            "INSERT INTO commissions_payout (id, currency, period_end, status, gross_minor, adjustments_minor, payable_minor, "
            "line_count, version, created_at, approval_reason, paid_reference, cancel_reason, idempotency_key, created_by_id, partner_id) "
            "VALUES (gen_random_uuid(), 'GBP', current_date, 'draft', 1250, 0, 1250, 1, 1, now(), '', '', '', 'p8:payout:1', %s, %s)",
            (user_id, partner_id),
        )
        for i in range(3):
            org_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO organizations_organization (id, slug, legal_name, display_name, status, residency_region, shard_key, "
                "suspension_reason, created_at, updated_at, created_by_id) "
                "VALUES (%s, %s, %s, %s, 'active', 'eu-west-1', %s, '', now(), now(), %s)",
                (org_id, f"p8-{i}", f"P8 {i}", f"P8 {i}", str(org_id), user_id),
            )
            conn.execute(
                "INSERT INTO organizations_membership (id, created_at, updated_at, status, is_owner, member_type, joined_at, "
                "user_id, organization_id) VALUES (gen_random_uuid(), now(), now(), 'active', true, 'internal', now(), %s, %s)",
                (user_id, org_id),
            )
            conn.execute(
                "INSERT INTO subscriptions_subscription (id, created_at, updated_at, status, currency, billing_interval, "
                "collection_mode, renewal_policy, current_period_start, current_period_end, trial_source, version, organization_id) "
                "VALUES (gen_random_uuid(), now(), now(), 'active', 'GBP', 'monthly', 'manual', 'auto', now(), now() + interval '30 days', '', 1, %s)",
                (org_id,),
            )
            conn.execute(
                "INSERT INTO entitlements_entitlement (id, created_at, updated_at, product_code, status, source_kind, source_ref, "
                "effective_from, computed_at, computation_id, organization_id) "
                "VALUES (gen_random_uuid(), now(), now(), 'crm', 'active', 'subscription', %s, now(), now(), gen_random_uuid(), %s)",
                (f"subscriptions.subscription:{org_id}", org_id),
            )
            conn.execute(
                "INSERT INTO entitlements_snapshot (id, created_at, updated_at, version, products, features, computed_at, organization_id) "
                "VALUES (gen_random_uuid(), now(), now(), 1, %s, '{}', now(), %s)",
                ('{"crm": {"status": "active", "seats": null, "write": true}}', org_id),
            )
            for n in range(2):
                conn.execute(
                    "INSERT INTO events_routed (id, created_at, updated_at, event_id, source, event_type, event_version, occurred_at, "
                    "correlation_id, chain_depth, payload, payload_bytes, fingerprint, routing_status, matched_deliveries, "
                    "matched_rules, organization_id) VALUES (gen_random_uuid(), now(), now(), gen_random_uuid(), 'product.crm', "
                    "'product.crm.deal.won', 1, now(), gen_random_uuid(), 0, '{\"amount_minor\": 100}', 21, %s, 'routed', 0, 0, %s)",
                    (f"fp{n}", org_id),
                )
            conn.execute(
                "INSERT INTO audit_event (id, created_at, updated_at, occurred_at, actor_kind, action, resource_type, resource_id, "
                "outcome, reason, ip_hash, prev_hash, entry_hash, organization_id) VALUES (gen_random_uuid(), now(), now(), now(), "
                "'user', 'p8.seed', 'test', 'x', 'success', '', '', '', %s, %s)",
                (uuid.uuid4().hex, org_id),
            )


def upgrade() -> None:
    print("\n== PHASE 7 -> PHASE 8 UPGRADE ==")
    create_database(UPGRADE)
    rc, out, err = django(UPGRADE, PHASE7_TARGETS + "    executor.migrate(targets)\n    print('PHASE7-BUILT')\n")
    record("upgrade: built a Phase 7-shaped database", rc == 0 and "PHASE7-BUILT" in out, last_line(err) if rc else "")
    if rc:
        return
    tables = {r[0] for r in q(UPGRADE, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    record("upgrade: the Phase 7 database has no Phase 8 table", not (tables & set(TENANT_TABLES + CONTROL_PLANE_TABLES)))
    unseed_phase8_grants(UPGRADE)
    seed_phase7(UPGRADE)
    before = checksums(UPGRADE)
    print("  seeded:", {t: c for t, (c, _) in before.items()})

    create_vector_extension(UPGRADE)
    rc, out, err = manage(UPGRADE, "migrate", "--noinput")
    record("upgrade: migrate Phase 7 -> Phase 8", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    after = checksums(UPGRADE)
    changed = {
        t: (before[t], after[t])
        for t in GUARDED
        if before[t] != after[t] and t not in ("authz_role_permission",)
    }
    record("upgrade: no existing row changed or was lost (byte-level checksums)", not changed, str(changed)[:300])
    grown = after["authz_role_permission"][0] - before["authz_role_permission"][0]
    record("upgrade: the only additions to existing tables are the six new role grants", grown == 6, f"+{grown} role grants")

    rc, out, err = manage(UPGRADE, "makemigrations", "--check", "--dry-run")
    record("upgrade: models match migrations after the upgrade", rc == 0)
    verify_phase8_schema(UPGRADE, "upgrade")
    roll_back(UPGRADE, before)


def roll_back(db: str, before: dict) -> None:
    rc, out, err = django(db, PHASE7_TARGETS + "    executor.migrate(targets)\n    print('ROLLED-BACK')\n")
    record("rollback: every Phase 8 migration reverses", rc == 0 and "ROLLED-BACK" in out, last_line(err) if rc else "")
    if rc:
        return
    tables = {r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    left = sorted(tables & set(TENANT_TABLES + CONTROL_PLANE_TABLES))
    record("rollback: every Phase 8 table is gone", not left, str(left))
    now = checksums(db)
    record("rollback: the Phase 7 data is still identical", now == before, "")
    rc, out, err = manage(db, "migrate", "--noinput")
    record("rollback: re-applying Phase 8 afterwards succeeds", rc == 0, last_line(err) if rc else "")


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
