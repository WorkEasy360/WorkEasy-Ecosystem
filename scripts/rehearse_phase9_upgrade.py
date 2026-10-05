"""Phase 8 extension -> Phase 9 upgrade rehearsal, and a fresh-database check.

Phase 9 (public site, self-service accounts, pay-first orders, Super Admin
roles) adds tables and columns on existing tables (the account's acquisition
source, the product's slug / tagline / marketing URL / listing, the invoice's
order, the customer summary's access class), relaxes one existing column
(``payments_invoice.subscription_id`` becomes nullable) and changes one stored
permission flag. So "no existing row changed" is proved over the columns each
guarded table had **before** the upgrade - byte for byte - and the new
columns and the two backfills are checked on their own.

    python scripts/rehearse_phase9_upgrade.py            # both rehearsals
    python scripts/rehearse_phase9_upgrade.py --keep     # leave the databases

Scratch databases only (``w360_p9_fresh``, ``w360_p9_upgrade`` on the local
Docker PostgreSQL); never ``workeasy360`` or the test database.
"""

from __future__ import annotations

import argparse
import sys

from rehearse_extension_upgrade import (
    EXTENSION,
    seed_phase8_extras,
    unseed_extension_grants,
)
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

FRESH, UPGRADE = "w360_p9_fresh", "w360_p9_upgrade"

#: The migrations Phase 9 added. Everything else is the extension and earlier.
PHASE9 = {
    ("identity", "0004_self_service_accounts"),
    ("administration", "0001_platform_settings"),
    ("catalog", "0003_product_presentation"),
    ("payments", "0002_orders"),
    ("provisioning", "0002_customer_access_class"),
    ("authz", "0009_billing_manage_not_dangerous"),
    ("tenancy", "0011_phase9_isolation"),
}

TENANT_TABLES = ["payments_order"]
CONTROL_PLANE_TABLES = [
    "payments_order_summary",
    "identity_account_token",
    "administration_platform_settings",
]
COMPOSITE_FKS = [
    "fk_payments_order_subscription_id_tenant",
    "fk_payments_invoice_order_id_tenant",
]
#: Existing tables whose pre-existing columns must survive unchanged.
GUARDED = [
    "identity_user",
    "organizations_organization",
    "organizations_membership",
    "subscriptions_subscription",
    "entitlements_entitlement",
    "provisioning_customer_summary",
    "catalog_product",
    "catalog_plan",
    "pricing_plan_price",
    "billing_account",
    "payments_invoice",
    "audit_event",
    "authz_role_permission",
]
#: Staff permissions Phase 9 declared (the registry seeds them into a
#: database built by today's code, so they are removed to look like 8x).
PHASE9_PERMISSIONS = ("platform.staff_settings.view", "platform.staff_settings.manage")
RELAXED = "platform.billing.manage"


# --- verification -------------------------------------------------------------------------


def verify_phase9_schema(db: str, label: str) -> None:
    tables = {
        r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    wanted = set(TENANT_TABLES) | set(CONTROL_PLANE_TABLES)
    record(
        f"{label}: every Phase 9 table exists",
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
        f"{label}: RLS enabled, forced and with a policy on the order table",
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
    for name in ("ck_invoice_for_subscription_or_order", "ck_order_amounts"):
        record(f"{label}: constraint {name} exists", name in constraints)
    # A partial unique constraint is stored as a unique index.
    pending = q(
        db, "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_order_one_pending'"
    )
    record(
        f"{label}: one order per organization may await payment (partial unique index)",
        bool(pending)
        and "UNIQUE" in pending[0][0]
        and "pending_payment" in pending[0][0],
        str(pending),
    )
    unique_slug = q(
        db,
        "SELECT count(*) FROM pg_indexes WHERE tablename='catalog_product' AND indexdef ILIKE '%%UNIQUE%%(slug)%%'",
    )[0][0]
    record(f"{label}: product slugs are unique", unique_slug >= 1)

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
        "SELECT has_sequence_privilege('w360_app', 'payments_order_number_seq', 'USAGE')",
    )[0][0]
    record(f"{label}: the app role can draw order numbers", sequence)

    flag = q(db, "SELECT dangerous FROM authz_permission WHERE key = %s", (RELAXED,))
    record(
        f"{label}: paying an invoice is no longer a dangerous permission",
        flag == [(False,)],
        str(flag),
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
    verify_phase9_schema(FRESH, "fresh")


def _targets(excluded: set) -> str:
    """Code that migrates to the latest migration of every app outside
    ``excluded``."""
    return f"""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    NEW = set({sorted(excluded)!r})
    executor = MigrationExecutor(connection)
    graph = executor.loader.graph
    targets = []
    for app in sorted({{a for a, _ in graph.nodes}}):
        chain = [n for (a, n) in graph.nodes if a == app and (a, n) not in NEW]
        targets.append((app, sorted(chain)[-1] if chain else None))
"""


PREVIOUS_TARGETS = _targets(PHASE9)
PHASE8_TARGETS = _targets(PHASE9 | EXTENSION)


def look_like_the_extension(db: str) -> None:
    """``authz.0002`` seeds permissions from the live registry, so a database
    built by today's code already knows Phase 9's staff permissions and the
    relaxed flag. Put them back the way the extension left them."""
    with connect(db, **SUPER) as conn:
        conn.execute(
            "DELETE FROM authz_role_permission WHERE permission_id = ANY(%s)",
            (list(PHASE9_PERMISSIONS),),
        )
        conn.execute(
            "DELETE FROM authz_permission WHERE key = ANY(%s)",
            (list(PHASE9_PERMISSIONS),),
        )
        conn.execute(
            "UPDATE authz_permission SET dangerous = true WHERE key = %s", (RELAXED,)
        )


def seed_extension_commerce(db: str) -> dict[str, str]:
    """What an extension database holds that Phase 9 reshapes: billing
    accounts and subscription invoices (the invoice gains a nullable order and
    loses its NOT NULL subscription), and customer summaries in every
    commercial state (the access-class backfill). Returns org slug -> the
    access class the backfill must give it."""
    expected: dict[str, str] = {}
    with connect(db, **SUPER) as conn:
        orgs = conn.execute(
            "SELECT o.id, o.slug, s.id FROM organizations_organization o "
            "JOIN subscriptions_subscription s ON s.organization_id = o.id ORDER BY o.slug"
        ).fetchall()
        states = [
            ("trialing", ["crm"], "trial"),
            ("active", ["crm"], "customer"),
            ("suspended", ["crm"], ""),
        ]
        for n, ((org_id, slug, subscription_id), (status, products, klass)) in enumerate(
            zip(orgs, states, strict=False)
        ):
            conn.execute(
                "INSERT INTO billing_account (id, created_at, updated_at, currency, collection_mode, billing_email, "
                "tax_reference, billing_region, organization_id) VALUES (gen_random_uuid(), now(), now(), 'GBP', "
                "'automatic', '', '', '', %s)",
                (org_id,),
            )
            conn.execute(
                "INSERT INTO payments_invoice (id, created_at, updated_at, number, status, currency, total_minor, "
                "amount_paid_minor, amount_refunded_minor, billing_interval, collection_mode, period_start, period_end, "
                "issued_at, due_at, void_reason, dunning_stage, version, organization_id, subscription_id) VALUES "
                "(gen_random_uuid(), now(), now(), %s, 'open', 'GBP', 1999, 0, 0, 'monthly', 'automatic', now(), "
                "now() + interval '30 days', now(), now(), '', '', 1, %s, %s)",
                (f"INV-P9-{n}", org_id, subscription_id),
            )
            conn.execute(
                "INSERT INTO provisioning_customer_summary (organization_id, subscription_status, product_codes, "
                "entitlement_version, computed_at) VALUES (%s, %s, %s, 1, now())",
                (org_id, status, f'["{products[0]}"]'),
            )
            expected[slug] = klass
    return expected


def upgrade() -> None:
    print("\n== EXTENSION -> PHASE 9 UPGRADE ==")
    create_database(UPGRADE)
    create_vector_extension(UPGRADE)
    # Phase 8 first, seeded with the rows the earlier rehearsals use, then the
    # extension on top - so the database holds rows of every age.
    rc, out, err = django(
        UPGRADE, PHASE8_TARGETS + "    executor.migrate(targets)\n    print('P8-BUILT')\n"
    )
    record(
        "upgrade: built a Phase 8-shaped database",
        rc == 0 and "P8-BUILT" in out,
        last_line(err) if rc else "",
    )
    if rc:
        return
    unseed_extension_grants(UPGRADE)
    seed_phase7(UPGRADE)
    seed_phase8_extras(UPGRADE)
    rc, out, err = django(
        UPGRADE,
        PREVIOUS_TARGETS + "    executor.migrate(targets)\n    print('EXT-BUILT')\n",
    )
    record(
        "upgrade: migrated it to the extension",
        rc == 0 and "EXT-BUILT" in out,
        last_line(err) if rc else "",
    )
    if rc:
        return
    tables = {
        r[0]
        for r in q(UPGRADE, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    record(
        "upgrade: the extension database has no Phase 9 table",
        not (tables & set(TENANT_TABLES + CONTROL_PLANE_TABLES)),
    )
    look_like_the_extension(UPGRADE)
    expected_classes = seed_extension_commerce(UPGRADE)
    columns = columns_of(UPGRADE)
    before = checksums(UPGRADE, columns)
    print("  seeded:", {t: c for t, (c, _) in before.items()})

    rc, out, err = manage(UPGRADE, "migrate", "--noinput")
    record("upgrade: migrate extension -> Phase 9", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    after = checksums(UPGRADE, columns)
    changed = {t: (before[t], after[t]) for t in GUARDED if before[t] != after[t]}
    record(
        "upgrade: no pre-existing column of any row changed or was lost",
        not changed,
        str(changed)[:300],
    )
    defaults = {
        "identity_user": "acquisition_source = ''",
        "catalog_product": "listing = 'listed' AND tagline = '' AND marketing_url = ''",
        "payments_invoice": "order_id IS NULL AND subscription_id IS NOT NULL",
    }
    problems = []
    for table, check in defaults.items():
        bad = q(UPGRADE, f"SELECT count(*) FROM {table} WHERE NOT ({check})")[0][0]  # noqa: S608 - fixed text
        if bad:
            problems.append(f"{table}: {bad} row(s)")
    record(
        "upgrade: every new column on an existing row holds its safe default",
        not problems,
        str(problems),
    )
    slugs = q(UPGRADE, "SELECT code, slug FROM catalog_product ORDER BY code")
    record(
        "upgrade: every product got its slug from its code",
        slugs and all(slug == code.replace("_", "-") for code, slug in slugs),
        str(slugs),
    )
    classes = dict(
        q(
            UPGRADE,
            "SELECT o.slug, s.access_class FROM provisioning_customer_summary s "
            "JOIN organizations_organization o ON o.id = s.organization_id",
        )
    )
    record(
        "upgrade: customer summaries are classified trial / customer / neither",
        classes == expected_classes,
        f"{classes} != {expected_classes}",
    )
    record(
        "upgrade: no account is pending and no order exists",
        q(UPGRADE, "SELECT count(*) FROM identity_user WHERE status = 'pending'")[0][0]
        == 0
        and q(UPGRADE, "SELECT count(*) FROM payments_order")[0][0] == 0,
    )
    rc, out, err = manage(UPGRADE, "makemigrations", "--check", "--dry-run")
    record("upgrade: models match migrations after the upgrade", rc == 0)
    verify_phase9_schema(UPGRADE, "upgrade")
    roll_back(UPGRADE, before, columns)


def roll_back(db: str, before: dict, columns: dict) -> None:
    rc, out, err = django(
        db, PREVIOUS_TARGETS + "    executor.migrate(targets)\n    print('ROLLED-BACK')\n"
    )
    record(
        "rollback: every Phase 9 migration reverses",
        rc == 0 and "ROLLED-BACK" in out,
        last_line(err) if rc else "",
    )
    if rc:
        return
    tables = {
        r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    left = sorted(tables & set(TENANT_TABLES + CONTROL_PLANE_TABLES))
    record("rollback: every Phase 9 table is gone", not left, str(left))
    record(
        "rollback: the extension data is still identical",
        checksums(db, columns) == before,
    )
    flag = q(db, "SELECT dangerous FROM authz_permission WHERE key = %s", (RELAXED,))
    record("rollback: paying an invoice is dangerous again", flag == [(True,)], str(flag))
    rc, out, err = manage(db, "migrate", "--noinput")
    record(
        "rollback: re-applying Phase 9 afterwards succeeds",
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
