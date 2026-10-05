"""Phase 8 -> Phase 8 extension upgrade rehearsal, and a fresh-database check.

The extension (consoles, access policy, communication, payments) adds tables
*and columns on existing tables* (the organization's access policy, a member's
sign-out stamp, invitation fields, catalog captions). So "no existing row
changed" is proved over the columns each guarded table had **before** the
upgrade - byte for byte - and the new columns are checked for their defaults.

    python scripts/rehearse_extension_upgrade.py            # both rehearsals
    python scripts/rehearse_extension_upgrade.py --keep     # leave the databases

Scratch databases only (``w360_px_fresh``, ``w360_px_upgrade`` on the local
Docker PostgreSQL); never ``workeasy360`` or the test database.

FRESH:   migrate from empty, then check every extension guarantee in the
         database itself - RLS on each new tenant table, composite foreign keys,
         grants on the control-plane tables, the invoice-number sequence, the
         bundle-version freeze covering the new trial column, the new role
         grant, and the application's own system checks.

UPGRADE: build a Phase 8 database, seed it (customers, members, invitations,
         subscriptions, a partner, commissions, sessions), checksum the
         pre-existing columns of every guarded table, migrate to the extension,
         prove nothing changed and the new columns hold their defaults, then
         reverse every extension migration for real, prove the Phase 8 data is
         identical, and re-apply.
"""

from __future__ import annotations

import argparse
import sys
import uuid

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
    seed_phase7,
)

FRESH, UPGRADE = "w360_px_fresh", "w360_px_upgrade"

#: The migrations the extension added. Everything else is Phase 8 and earlier.
EXTENSION = {
    ("identity", "0003_session_registry_and_account_events"),
    ("organizations", "0005_console_access_policy_and_invitations"),
    ("notifications", "0002_sealed_context_and_communication"),
    ("authz", "0008_console_permissions"),
    ("catalog", "0002_plan_builder"),
    ("pricing", "0003_semi_annual_interval"),
    ("subscriptions", "0003_semi_annual_interval"),
    ("payments", "0001_payments"),
    ("tenancy", "0009_console_isolation"),
    ("tenancy", "0010_payments_isolation"),
}

TENANT_TABLES = [
    "organizations_member_sign_in",
    "payments_invoice",
    "payments_invoice_line",
    "payments_attempt",
    "payments_refund",
]
CONTROL_PLANE_TABLES = [
    "identity_session",
    "identity_account_event",
    "notifications_template_override",
    "notifications_communication_settings",
    "payments_provider",
    "payments_webhook_event",
]
COMPOSITE_FKS = [
    "fk_organizations_member_sign_in_membership_id_tenant",
    "fk_payments_invoice_subscription_id_tenant",
    "fk_payments_invoice_line_invoice_id_tenant",
    "fk_payments_attempt_invoice_id_tenant",
    "fk_payments_refund_attempt_id_tenant",
]
#: Existing tables whose pre-existing columns must survive unchanged.
GUARDED = [
    "identity_user",
    "organizations_organization",
    "organizations_membership",
    "organizations_invitation",
    "notifications_notification",
    "partners_account",
    "commissions_accrual",
    "commissions_payout",
    "subscriptions_subscription",
    "entitlements_entitlement",
    "events_routed",
    "audit_event",
    "authz_role",
    "authz_role_permission",
    "catalog_plan",
    "catalog_bundle",
    "catalog_bundle_version",
    "pricing_plan_price",
]

EXTENSION_ROLE_PERMISSIONS = ("platform.member.sign_out",)
#: An existing permission the extension grants to one more system role.
EXTENSION_ROLE_GRANTS = (("billing_manager", "commercial.subscription.view"),)


# --- verification -------------------------------------------------------------------------


def verify_extension_schema(db: str, label: str) -> None:
    tables = {
        r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    wanted = set(TENANT_TABLES) | set(CONTROL_PLANE_TABLES)
    record(
        f"{label}: every extension table exists",
        wanted <= tables,
        str(sorted(wanted - tables)),
    )

    rls = {
        r[0]: (r[1], r[2])
        for r in q(
            db,
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = ANY(%s)",
            (TENANT_TABLES + CONTROL_PLANE_TABLES,),
        )
    }
    policies = {
        r[0] for r in q(db, "SELECT tablename FROM pg_policies WHERE schemaname='public'")
    }
    bad = [t for t in TENANT_TABLES if rls.get(t) != (True, True) or t not in policies]
    record(
        f"{label}: RLS enabled, forced and with a policy on every new tenant table",
        not bad,
        str(bad),
    )
    open_cp = [t for t in CONTROL_PLANE_TABLES if rls.get(t, (False, False))[0]]
    record(
        f"{label}: the control-plane tables carry no tenant policy",
        not open_cp,
        str(open_cp),
    )

    constraints = {r[0] for r in q(db, "SELECT conname FROM pg_constraint")}
    missing = [c for c in COMPOSITE_FKS if c not in constraints]
    record(f"{label}: every composite foreign key exists", not missing, str(missing))

    for table in CONTROL_PLANE_TABLES + TENANT_TABLES:
        grants = {
            p: q(db, "SELECT has_table_privilege('w360_app', %s, %s)", (table, p))[0][0]
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        }
        ok = (
            grants["SELECT"]
            and grants["INSERT"]
            and grants["UPDATE"]
            and not grants["TRUNCATE"]
        )
        record(f"{label}: app role DML, not TRUNCATE, on {table}", ok, str(grants))

    sequence = q(
        db,
        "SELECT has_sequence_privilege('w360_app', 'payments_invoice_number_seq', 'USAGE')",
    )[0][0]
    record(f"{label}: the app role can draw invoice numbers", sequence)

    (source,) = q(
        db,
        "SELECT prosrc FROM pg_proc WHERE proname = 'catalog_bundle_version_reject_published_change'",
    )[0]
    record(
        f"{label}: a published bundle version's trial length is frozen",
        "trial_days" in source,
    )

    granted = q(
        db,
        "SELECT r.key FROM authz_role_permission rp JOIN authz_role r ON r.id = rp.role_id "
        "WHERE r.organization_id IS NULL AND rp.permission_id = 'platform.member.sign_out' ORDER BY 1",
    )
    record(
        f"{label}: owner and admin hold platform.member.sign_out",
        [g[0] for g in granted] == ["admin", "owner"],
        str(granted),
    )
    billing = q(
        db,
        "SELECT rp.permission_id FROM authz_role_permission rp JOIN authz_role r ON r.id = rp.role_id "
        "WHERE r.organization_id IS NULL AND r.key = 'billing_manager' AND rp.permission_id LIKE 'commercial.%%'",
    )
    record(
        f"{label}: the billing manager reads, and only reads, the subscription",
        [b[0] for b in billing] == ["commercial.subscription.view"],
        str(billing),
    )

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


def columns_of(db: str) -> dict[str, list[str]]:
    return {
        table: [
            r[0]
            for r in q(
                db,
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=%s ORDER BY ordinal_position",
                (table,),
            )
        ]
        for table in GUARDED
    }


def checksums(db: str, columns: dict[str, list[str]]) -> dict[str, tuple[int, str]]:
    """Row count and an md5 over the given columns of every row, per guarded
    table - as the superuser, so RLS hides nothing."""
    out = {}
    for table, cols in columns.items():
        row = "row(" + ", ".join(f'"{c}"' for c in cols) + ")::text"
        (count, digest) = q(
            db,
            f"SELECT count(*), coalesce(md5(string_agg({row}, '|' ORDER BY {row})), '') FROM {table}",  # noqa: S608 - fixed identifiers
        )[0]
        out[table] = (count, digest)
    return out


# --- the rehearsals -----------------------------------------------------------------------


def fresh() -> None:
    print("\n== FRESH DATABASE ==")
    create_database(FRESH)
    create_vector_extension(FRESH)
    rc, out, err = manage(FRESH, "migrate", "--noinput")
    record("fresh: migrate completes", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    rc, out, err = manage(FRESH, "makemigrations", "--check", "--dry-run")
    record("fresh: models match migrations (makemigrations --check)", rc == 0)
    verify_extension_schema(FRESH, "fresh")


PHASE8_TARGETS = f"""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    EXT = set({sorted(EXTENSION)!r})
    executor = MigrationExecutor(connection)
    graph = executor.loader.graph
    targets = []
    for app in sorted({{a for a, _ in graph.nodes}}):
        chain = [n for (a, n) in graph.nodes if a == app and (a, n) not in EXT]
        targets.append((app, sorted(chain)[-1] if chain else None))
"""


def unseed_extension_grants(db: str) -> None:
    """Make the built database what Phase 8 code left behind: ``authz.0002``
    seeds roles from the live registry, so remove the extension's grant."""
    with connect(db, **SUPER) as conn:
        conn.execute(
            "DELETE FROM authz_role_permission WHERE permission_id = ANY(%s)",
            (list(EXTENSION_ROLE_PERMISSIONS),),
        )
        conn.execute(
            "DELETE FROM authz_permission WHERE key = ANY(%s)",
            (list(EXTENSION_ROLE_PERMISSIONS),),
        )
        for role_key, permission in EXTENSION_ROLE_GRANTS:
            conn.execute(
                "DELETE FROM authz_role_permission rp USING authz_role r WHERE r.id = rp.role_id "
                "AND r.organization_id IS NULL AND r.key = %s AND rp.permission_id = %s",
                (role_key, permission),
            )


def seed_phase8_extras(db: str) -> None:
    """What Phase 8 databases also hold that the extension touches: pending
    invitations (a Phase 1 table the extension now writes), a notification, a
    catalog plan with a published bundle version and a price, and a live
    session in Django's old session table (which the new engine stops reading
    - every browser signs in again once, as documented)."""
    with connect(db, **SUPER) as conn:
        org_id, membership_id = conn.execute(
            "SELECT organization_id, id FROM organizations_membership ORDER BY created_at LIMIT 1"
        ).fetchone()
        conn.execute(
            "INSERT INTO organizations_invitation (id, created_at, updated_at, email, token_hash, expires_at, "
            "organization_id, invited_by_id) VALUES (gen_random_uuid(), now(), now(), 'invitee@px.example', %s, "
            "now() + interval '7 days', %s, %s)",
            (uuid.uuid4().hex, org_id, membership_id),
        )
        conn.execute(
            "INSERT INTO notifications_notification (id, created_at, updated_at, template_key, recipient_email, context, "
            "idempotency_key, created_at_utc, organization_id) VALUES (gen_random_uuid(), now(), now(), "
            "'platform.organization.suspended', 'x@px.example', '{}', 'px:1', now(), %s)",
            (org_id,),
        )
        product = uuid.uuid4()
        conn.execute(
            "INSERT INTO catalog_product (id, code, name, description, status, licensing_mode, sort_order, created_at, updated_at) "
            "VALUES (%s, 'pxprod', 'PX Product', '', 'available', 'per_seat', 1, now(), now())",
            (product,),
        )
        plan = uuid.uuid4()
        conn.execute(
            "INSERT INTO catalog_plan (id, code, name, description, status, is_public, sort_order, created_at, updated_at, product_id) "
            "VALUES (%s, 'px_plan', 'PX Plan', '', 'available', true, 1, now(), now(), %s)",
            (plan, product),
        )
        version = uuid.uuid4()
        conn.execute(
            "INSERT INTO catalog_plan_version (id, version, published_at, effective_from, included_seats, trial_days, "
            "created_at, updated_at, plan_id) VALUES (%s, 1, now(), now(), 1, 0, now(), now(), %s)",
            (version, plan),
        )
        bundle = uuid.uuid4()
        conn.execute(
            "INSERT INTO catalog_bundle (id, code, name, description, kind, status, is_public, created_at, updated_at) "
            "VALUES (%s, 'px_bundle', 'PX Bundle', '', 'predefined', 'available', true, now(), now())",
            (bundle,),
        )
        conn.execute(
            "INSERT INTO catalog_bundle_version (id, version, published_at, effective_from, created_at, updated_at, bundle_id) "
            "VALUES (gen_random_uuid(), 1, now(), now(), now(), now(), %s)",
            (bundle,),
        )
        book = uuid.uuid4()
        conn.execute(
            "INSERT INTO pricing_price_book (id, code, name, channel, region, currency, priority, valid_from, is_active, "
            "created_at, updated_at) VALUES (%s, 'px_book', 'PX', 'direct', '', 'GBP', 100, now(), true, now(), now())",
            (book,),
        )
        conn.execute(
            "INSERT INTO pricing_plan_price (id, interval, model, amount_minor, min_quantity, tax_behaviour, valid_from, "
            "published_at, created_at, updated_at, price_book_id, plan_version_id) VALUES (gen_random_uuid(), 'monthly', "
            "'flat', 1999, 1, 'exclusive', now(), now(), now(), now(), %s, %s)",
            (book, version),
        )
        conn.execute(
            "INSERT INTO django_session (session_key, session_data, expire_date) "
            "VALUES ('pxsessionkey00000000000000000001', 'e30:old', now() + interval '1 day')"
        )


def new_column_defaults(db: str) -> list[str]:
    problems = []
    for table, checks in {
        "organizations_organization": (
            "require_mfa = false AND ip_allowlist_enabled = false AND ip_allowlist = '[]'::jsonb "
            "AND security_policy_version = 1 AND session_max_age_minutes IS NULL AND password_min_length IS NULL"
        ),
        "organizations_membership": "sessions_valid_after IS NULL",
        "organizations_invitation": "role_key = 'member' AND send_count = 1",
        "notifications_notification": "sealed_context = ''",
        "catalog_plan": "caption = '' AND billing_type = 'paid' AND kind = 'standard' AND addon_for_id IS NULL",
        "catalog_bundle": "caption = '' AND billing_type = 'paid'",
        "catalog_bundle_version": "trial_days = 0",
    }.items():
        bad = q(db, f"SELECT count(*) FROM {table} WHERE NOT ({checks})")[0][0]  # noqa: S608 - fixed text
        if bad:
            problems.append(f"{table}: {bad} row(s)")
    return problems


def upgrade() -> None:
    print("\n== PHASE 8 -> EXTENSION UPGRADE ==")
    create_database(UPGRADE)
    create_vector_extension(UPGRADE)
    rc, out, err = django(
        UPGRADE,
        PHASE8_TARGETS + "    executor.migrate(targets)\n    print('PHASE8-BUILT')\n",
    )
    record(
        "upgrade: built a Phase 8-shaped database",
        rc == 0 and "PHASE8-BUILT" in out,
        last_line(err) if rc else "",
    )
    if rc:
        return
    tables = {
        r[0]
        for r in q(UPGRADE, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    record(
        "upgrade: the Phase 8 database has no extension table",
        not (tables & set(TENANT_TABLES + CONTROL_PLANE_TABLES)),
    )
    unseed_extension_grants(UPGRADE)
    seed_phase7(UPGRADE)
    seed_phase8_extras(UPGRADE)
    columns = columns_of(UPGRADE)
    before = checksums(UPGRADE, columns)
    print("  seeded:", {t: c for t, (c, _) in before.items()})

    rc, out, err = manage(UPGRADE, "migrate", "--noinput")
    record("upgrade: migrate Phase 8 -> extension", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    after = checksums(UPGRADE, columns)
    changed = {
        t: (before[t], after[t])
        for t in GUARDED
        if before[t] != after[t] and t != "authz_role_permission"
    }
    record(
        "upgrade: no pre-existing column of any row changed or was lost",
        not changed,
        str(changed)[:300],
    )
    grown = after["authz_role_permission"][0] - before["authz_role_permission"][0]
    record(
        "upgrade: the only additions to existing tables are the three new role grants",
        grown == 3,
        f"+{grown} role grants",
    )
    problems = new_column_defaults(UPGRADE)
    record(
        "upgrade: every new column on an existing row holds its safe default",
        not problems,
        str(problems),
    )
    old_sessions = q(UPGRADE, "SELECT count(*) FROM django_session")[0][0]
    new_sessions = q(UPGRADE, "SELECT count(*) FROM identity_session")[0][0]
    record(
        "upgrade: old sessions are left in place and not carried over (everyone signs in once)",
        old_sessions == 1 and new_sessions == 0,
        f"django_session={old_sessions}, identity_session={new_sessions}",
    )
    rc, out, err = manage(UPGRADE, "makemigrations", "--check", "--dry-run")
    record("upgrade: models match migrations after the upgrade", rc == 0)
    verify_extension_schema(UPGRADE, "upgrade")
    roll_back(UPGRADE, before, columns)


def roll_back(db: str, before: dict, columns: dict) -> None:
    rc, out, err = django(
        db, PHASE8_TARGETS + "    executor.migrate(targets)\n    print('ROLLED-BACK')\n"
    )
    record(
        "rollback: every extension migration reverses",
        rc == 0 and "ROLLED-BACK" in out,
        last_line(err) if rc else "",
    )
    if rc:
        return
    tables = {
        r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    left = sorted(tables & set(TENANT_TABLES + CONTROL_PLANE_TABLES))
    record("rollback: every extension table is gone", not left, str(left))
    now = checksums(db, columns)
    record("rollback: the Phase 8 data is still identical", now == before, "")
    (source,) = q(
        db,
        "SELECT prosrc FROM pg_proc WHERE proname = 'catalog_bundle_version_reject_published_change'",
    )[0]
    record(
        "rollback: the Phase 2 bundle-version freeze is restored",
        "trial_days" not in source and "effective_from" in source,
    )
    rc, out, err = manage(db, "migrate", "--noinput")
    record(
        "rollback: re-applying the extension afterwards succeeds",
        rc == 0,
        last_line(err) if rc else "",
    )


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
