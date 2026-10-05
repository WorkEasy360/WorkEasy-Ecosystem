"""Phase 12 migration safety: fresh database, every-migration round trip, and a
previous-production -> current upgrade over real data (Steps 17, 61, 62).

    python scripts/rehearse_phase12_upgrade.py --only fresh
    python scripts/rehearse_phase12_upgrade.py --only roundtrip
    python scripts/rehearse_phase12_upgrade.py --only upgrade --dump <staging db.dump>

Scratch databases only (``w360_p12_*`` on the local Docker PostgreSQL), never
``workeasy360`` or the test database.

- **fresh**: migrate from zero, then the application's own deploy checks as
  the runtime role (RLS on every tenant table, FORCE, role, timeouts) and a
  smoke through the services (organization, trial, entitlement decision).
- **roundtrip**: every migration unapplied one at a time, newest first, down
  to zero, then all re-applied. A migration that cannot be reversed is
  reported as irreversible, never skipped silently.
- **upgrade**: the staging database - populated by real signups, trials,
  orders, partners and commissions - restored, stepped back to the Phase 11
  schema (Phase 12 adds exactly one migration), given a Phase-11-era
  authorization code, checksummed, upgraded, and compared: no pre-existing
  value of any authoritative table may change. Then rolled back and
  re-applied over the same data.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from rehearse_phase8_upgrade import (
    SUPER,
    connect,
    create_database,
    create_vector_extension,
    django,
    drop_database,
    last_line,
    manage,
    q,
    record,
    results,
)

FRESH, ROUNDTRIP, UPGRADE = "w360_p12_fresh", "w360_p12_roundtrip", "w360_p12_upgrade"
PHASE12 = ("ecosystem", "0004_authorizationcode_session_facts")
PREVIOUS = ("ecosystem", "0003_integration_health")
LOCAL_PG = "workeasy360-postgres-1"

#: Authoritative tables and the columns whose values must survive an upgrade.
GUARDED = {
    "identity_user": "id, email, status, password, is_staff",
    "organizations_organization": "id, slug, display_name, status",
    "organizations_membership": "id, organization_id, user_id, status, is_owner",
    "subscriptions_subscription": "id, organization_id, status",
    "subscriptions_item": "id, subscription_id, plan_version_id, quantity",
    "subscriptions_product_trial": "id, organization_id, product_code, status, ends_at",
    "entitlements_entitlement": "id, organization_id, product_code, status",
    "entitlements_snapshot": "organization_id, version, products",
    "entitlements_seat_assignment": "id, organization_id, membership_id",
    "catalog_product": "id, code, status",
    "catalog_plan_version": "id, plan_id, version, published_at, trial_days",
    "payments_order": "id, organization_id, status",
    "payments_invoice": "id, organization_id, status, total_minor",
    "partners_account": "id, code, status",
    "partners_product_grant": "id, partner_id, product_id, plan_id, status",
    "partners_organization_link": "id, partner_id, organization_id, status",
    "commissions_accrual": "id, partner_id, kind, amount_minor",
    "commissions_payout": "id, status, payable_minor",
    "ecosystem_product_integration": "id, product_code, client_id, status",
    "ecosystem_authorization_code": "id, code_hash, integration_id, organization_id, redeemed_at",
}


def checksums(db: str) -> dict[str, tuple[int, str]]:
    out = {}
    for table, cols in GUARDED.items():
        rows = q(db, f"SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '') FROM (SELECT {cols} FROM {table}) t")  # noqa: S608
        out[table] = (rows[0][0], rows[0][1])
    return out


def deploy_checks(db: str, label: str) -> None:
    """The application's own checks, as the runtime role (development settings,
    so Django's transport warnings are expected; W360 errors are not)."""
    rc, out, err = manage(db, "check", "--deploy", settings="config.settings.development")
    text = out + err
    w360 = [line for line in text.splitlines() if "W360.E" in line]
    record(f"{label}: W360 deploy checks (RLS + FORCE on every tenant table, role, ownership, timeouts)", not w360, "; ".join(w360)[:300])


def fresh() -> None:
    print("\n== FRESH DATABASE ==")
    drop_database(FRESH)
    create_database(FRESH)
    create_vector_extension(FRESH)
    rc, out, err = manage(FRESH, "migrate", "--noinput")
    record("fresh: migrate from zero", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    rc, out, err = manage(FRESH, "makemigrations", "--check", "--dry-run", settings="config.settings.development")
    record("fresh: no model change without a migration", rc == 0, last_line(out + err))
    deploy_checks(FRESH, "fresh")
    forced = q(
        FRESH,
        "SELECT c.relname FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid "
        "WHERE c.relkind = 'r' AND a.attname = 'organization_id' AND c.relnamespace = 'public'::regnamespace "
        "AND c.relrowsecurity AND NOT c.relforcerowsecurity",
    )
    record("fresh: no table has RLS enabled without FORCE", not forced, str(forced))
    rc, out, err = django(
        FRESH,
        """
        import uuid
        from django.core.management import call_command
        from workeasy360.platform.identity.models import User
        from workeasy360.platform.tenancy.unscoped import unscoped_access
        from workeasy360.platform.identity import lifecycle
        from workeasy360.platform.onboarding import services as onboarding
        from workeasy360.platform.entitlements.selectors import decision_for_organization
        import os
        os.environ["PLATFORM_SUPERADMIN_EMAIL"] = "root@p12-fresh.net"
        os.environ["PLATFORM_SUPERADMIN_PASSWORD"] = "P12-Fresh-" + uuid.uuid4().hex
        call_command("bootstrap_superadmin")
        call_command("register_products", actor="root@p12-fresh.net", activate_integrations=True)
        with unscoped_access(reason="p12 fresh smoke", module="rehearsal"):
            u = User.objects.create_user(email="smoke@p12-fresh.net", password=None)
            lifecycle.complete_with_password(u, "P12-Smoke-" + uuid.uuid4().hex, full_name="Smoke", activate=True)
        org = onboarding.create_organization(user_id=u.id, name="Smoke Ltd")
        oid = getattr(org, "organization_id", None) or org.id
        d = decision_for_organization(oid, "easy_crm")
        print("SMOKE", d.allowed, d.reason_code.value)
        """,
    )
    record(
        "fresh: seed (Super Admin, the four products) and smoke through the services",
        rc == 0 and "SMOKE False not_entitled" in out,
        last_line(err) if rc else last_line(out),
    )


def roundtrip() -> None:
    print("\n== EVERY MIGRATION: FORWARD, BACKWARD, FORWARD ==")
    drop_database(ROUNDTRIP)
    create_database(ROUNDTRIP)
    create_vector_extension(ROUNDTRIP)
    rc, out, err = manage(ROUNDTRIP, "migrate", "--noinput")
    record("roundtrip: forward (all)", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    rc, out, err = django(
        ROUNDTRIP,
        """
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        with connection.cursor() as cur:
            cur.execute("SELECT app, name FROM django_migrations ORDER BY id")
            applied = cur.fetchall()
        irreversible = []
        reversed_ok = 0
        for app, name in reversed(applied):
            executor = MigrationExecutor(connection)
            graph = executor.loader.graph
            if (app, name) not in graph.nodes:
                continue
            parents = [p for p in graph.node_map[(app, name)].parents if p.key[0] == app]
            target = (app, parents[0].key[1]) if parents else (app, None)
            try:
                executor.migrate([target])
                reversed_ok += 1
            except Exception as exc:
                irreversible.append(f"{app}.{name}: {type(exc).__name__}: {str(exc)[:120]}")
                break
        print("REVERSED", reversed_ok, "OF", len(applied))
        for line in irreversible:
            print("IRREVERSIBLE", line)
        """,
    )
    reversed_line = next((l for l in out.splitlines() if l.startswith("REVERSED")), "")
    irreversible = [l for l in out.splitlines() if l.startswith("IRREVERSIBLE")]
    record("roundtrip: every migration reverses down to zero", rc == 0 and not irreversible, reversed_line + " " + "; ".join(irreversible))
    rc, out, err = manage(ROUNDTRIP, "migrate", "--noinput")
    record("roundtrip: re-forward (all)", rc == 0, last_line(err) if rc else "")
    deploy_checks(ROUNDTRIP, "roundtrip (after re-forward)")


def restore_dump(db: str, dump: str) -> bool:
    drop_database(db)
    create_database(db)
    create_vector_extension(db)
    with open(dump, "rb") as fh:
        proc = subprocess.run(
            ["docker", "exec", "-i", LOCAL_PG, "pg_restore", "-U", "postgres", "-d", db, "--exit-on-error", "--single-transaction"],
            stdin=fh,
            capture_output=True,
        )
    record("upgrade: restored the staging database into a scratch database", proc.returncode == 0, proc.stderr.decode(errors="replace")[-300:])
    return proc.returncode == 0


def upgrade(dump: str) -> None:
    print("\n== PREVIOUS PRODUCTION (PHASE 11 SCHEMA) -> PHASE 12 ==")
    if not restore_dump(UPGRADE, dump):
        return
    with connect(UPGRADE, **SUPER) as conn:
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO w360_app")
        conn.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO w360_app")
    rc, out, err = manage(UPGRADE, "migrate", PREVIOUS[0], PREVIOUS[1], "--noinput")
    record("upgrade: stepped back to the Phase 11 schema (ecosystem 0003)", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    cols = {r[0] for r in q(UPGRADE, "SELECT column_name FROM information_schema.columns WHERE table_name='ecosystem_authorization_code'")}
    record("upgrade: the Phase 11 schema has no session_facts column", "session_facts" not in cols)
    with connect(UPGRADE, **SUPER) as conn:
        integration = conn.execute("SELECT id FROM ecosystem_product_integration ORDER BY product_code LIMIT 1").fetchone()
        org = conn.execute("SELECT o.id, m.id, m.user_id FROM organizations_organization o JOIN organizations_membership m ON m.organization_id = o.id LIMIT 1").fetchone()
        conn.execute(
            "INSERT INTO ecosystem_authorization_code (id, code_hash, integration_id, user_id, organization_id, membership_id, aal, "
            "redirect_uri, scope, code_challenge, code_challenge_method, nonce, expires_at, created_at) "
            "VALUES (gen_random_uuid(), md5(random()::text) || md5(random()::text), %s, %s, %s, %s, 1, "
            "'https://product.example.net/cb', 'openid', repeat('c', 43), 'S256', 'n', now() + interval '1 minute', now())",
            (integration[0], org[2], org[0], org[1]),
        )
    before = checksums(UPGRADE)
    print("  guarded rows:", {t: c for t, (c, _) in before.items()})
    record("upgrade: real data present in every guarded table that staging exercised",
           all(before[t][0] > 0 for t in ("identity_user", "organizations_organization", "subscriptions_product_trial", "entitlements_snapshot", "payments_order", "partners_account", "catalog_product")),
           str({t: c for t, (c, _) in before.items()}))
    rc, out, err = manage(UPGRADE, "migrate", "--noinput")
    record("upgrade: migrate Phase 11 -> Phase 12", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    after = checksums(UPGRADE)
    changed = {t: (before[t], after[t]) for t in GUARDED if before[t] != after[t]}
    record("upgrade: no pre-existing value of any authoritative table changed", not changed, str(changed)[:300])
    legacy = q(UPGRADE, "SELECT count(*) FROM ecosystem_authorization_code WHERE session_facts IS NULL")
    record("upgrade: a code minted by the previous release has no recorded facts (judged least-trusted)", legacy[0][0] >= 1)
    deploy_checks(UPGRADE, "upgrade")
    rc, out, err = manage(UPGRADE, "migrate", PREVIOUS[0], PREVIOUS[1], "--noinput")
    record("upgrade: Phase 12's migration reverses over populated data", rc == 0, last_line(err) if rc else "")
    rolled = checksums(UPGRADE)
    record("upgrade: the rollback leaves every authoritative value intact", rolled == before, str({t: (before[t], rolled[t]) for t in GUARDED if before[t] != rolled[t]})[:300])
    rc, out, err = manage(UPGRADE, "migrate", "--noinput")
    record("upgrade: re-applied after the rollback", rc == 0, last_line(err) if rc else "")
    record("upgrade: and still unchanged", checksums(UPGRADE) == before)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=("fresh", "roundtrip", "upgrade"), action="append")
    parser.add_argument("--dump")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    wanted = set(args.only or ("fresh", "roundtrip", "upgrade"))
    try:
        if "fresh" in wanted:
            fresh()
        if "roundtrip" in wanted:
            roundtrip()
        if "upgrade" in wanted:
            if not args.dump:
                record("upgrade: a staging dump was given (--dump)", False)
            else:
                upgrade(args.dump)
    finally:
        if not args.keep:
            for db in (FRESH, ROUNDTRIP, UPGRADE):
                drop_database(db)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for name, ok, detail in results:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
