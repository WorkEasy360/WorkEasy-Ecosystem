"""Phase 9 -> Phase 10 upgrade rehearsal, and a fresh-database check.

Phase 10's only schema change is ``partners.0003_owner_invite_claim``: four
columns on ``partners_owner_invite`` (the hash of the emailed activation link, how
often it was sent, when, and when it was withdrawn), a partial unique index on
that hash, an index for the customer list, and one more status choice. It adds no
table and touches no other one. So "no existing row changed" is proved over the
columns the guarded tables had **before** the upgrade, byte for byte; the new
columns' defaults are checked on their own; and because a deployment may have to
roll back, the migration is also reversed and re-applied over populated data.

    python scripts/rehearse_phase10_upgrade.py            # both rehearsals
    python scripts/rehearse_phase10_upgrade.py --keep     # leave the databases

Scratch databases only (``w360_p10_fresh``, ``w360_p10_upgrade`` on the local
Docker PostgreSQL); never ``workeasy360`` or the test database. The fresh one
is what ``scripts/phase10_browser_check/`` seeds and runs against.
"""

from __future__ import annotations

import argparse
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

FRESH, UPGRADE = "w360_p10_fresh", "w360_p10_upgrade"

#: The migration Phase 10 added. Everything else is Phase 9 and earlier.
PHASE10 = {("partners", "0003_owner_invite_claim")}
#: column -> (type, nullable). Django applies a field's default in Python and
#: to the rows that exist while it adds the column, then drops the database
#: default - so the defaults are proved on the migrated rows, not on the column.
NEW_COLUMNS = {
    "token_hash": ("character varying", "NO"),
    "send_count": ("smallint", "NO"),
    "last_sent_at": ("timestamp with time zone", "YES"),
    "revoked_at": ("timestamp with time zone", "YES"),
}
OLD_COLUMNS = (
    "id, organization_id, user_id, partner_id, status, created_at, expires_at, resolved_at"
)
#: Existing tables whose pre-existing columns must survive unchanged.
GUARDED = {
    "partners_owner_invite": OLD_COLUMNS,
    "partners_account": "id, code, display_name, status, version, created_by_id",
    "partners_organization_link": "id, partner_id, organization_id, status, started_at",
    "identity_user": "id, email, status, password",
    "organizations_organization": "id, slug, display_name, status",
}

BEFORE_TARGETS = f"""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    P10 = set({sorted(PHASE10)!r})
    executor = MigrationExecutor(connection)
    graph = executor.loader.graph
    targets = []
    for app in sorted({{a for a, _ in graph.nodes}}):
        chain = [n for (a, n) in graph.nodes if a == app and (a, n) not in P10]
        targets.append((app, sorted(chain)[-1] if chain else None))
"""


# --- verification ----------------------------------------------------------------------------


def verify_phase10_schema(db: str, label: str) -> None:
    columns = {
        r[0]: (r[1], r[2])
        for r in q(
            db,
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'partners_owner_invite'",
        )
    }
    missing = [c for c in NEW_COLUMNS if c not in columns]
    record(f"{label}: the four new invitation columns exist", not missing, str(missing))
    wrong = [f"{c}: {columns[c]}" for c, want in NEW_COLUMNS.items() if c in columns and columns[c] != want]
    record(f"{label}: they have the expected types and nullability", not wrong, str(wrong))

    unique = q(
        db,
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_owner_invite_token_hash'",
    )
    record(
        f"{label}: a link's hash may name at most one invitation (partial unique index)",
        bool(unique) and "UNIQUE" in unique[0][0] and "token_hash" in unique[0][0] and "''" in unique[0][0],
        unique[0][0] if unique else "missing",
    )
    latest = q(db, "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_ownerinvite_org_latest'")
    record(f"{label}: the customer-list index exists", bool(latest))

    rls = q(db, "SELECT relrowsecurity FROM pg_class WHERE relname = 'partners_owner_invite'")
    record(
        f"{label}: the invitation table is control-plane data (no tenant policy)",
        bool(rls) and rls[0][0] is False,
    )
    tables = {r[0] for r in q(db, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'partners_%%'")}
    expected = {
        "partners_account",
        "partners_capability_grant",
        "partners_member",
        "partners_organization_link",
        "partners_product_grant",
        "partners_brand",
        "partners_domain",
        "partners_owner_invite",
        "partners_logo_upload",
    }
    record(
        f"{label}: Phase 10 added no partner table (no new brand, domain, identity or ledger table)",
        tables == expected,
        str(sorted(tables ^ expected)),
    )
    applied = q(db, "SELECT 1 FROM django_migrations WHERE app = 'partners' AND name = '0003_owner_invite_claim'")
    record(f"{label}: partners.0003 is applied", bool(applied))


def checksums(db: str) -> dict[str, tuple[int, str]]:
    """Row count and an md5 over each guarded table's *old* columns. As the
    superuser, so RLS does not hide anything."""
    out = {}
    for table, cols in GUARDED.items():
        (count, digest) = q(
            db,
            f"SELECT count(*), coalesce(md5(string_agg(row({cols})::text, '|' ORDER BY id::text)), '') "  # noqa: S608 - fixed table list
            f"FROM {table}",
        )[0]
        out[table] = (count, digest)
    return out


# --- the rehearsals ---------------------------------------------------------------------------------


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
    verify_phase10_schema(FRESH, "fresh")


def seed_legacy_invitations(db: str) -> list[str]:
    """Owner invitations as Phase 9 left them: everything around them is made
    by the models (which did not change in Phase 10), the invitations by plain
    SQL over only the columns that existed then - one of each state a customer
    can be in."""
    org_states = ("pending", "accepted", "declined")
    rc, out, err = django(
        db,
        """
        from django.utils import timezone
        from workeasy360.commercial.partners.models import PartnerAccount, PartnerOrganizationLink
        from workeasy360.platform.identity.models import User
        from workeasy360.platform.organizations.models import Organization
        from workeasy360.platform.tenancy.unscoped import unscoped_access

        with unscoped_access(reason="Rehearsal seeds legacy invitations", module="rehearsal"):
            owner = User.objects.create_user(email="owner@p10.example", password=None)
            creator = User.objects.create_user(email="creator@p10.example", password="a long passphrase 1234")
            partner = PartnerAccount.objects.create(
                code="p10-partner", display_name="Partner", legal_name="Partner Ltd",
                payout_currency="GBP", status="active", created_by=creator,
            )
            for i in range(3):
                org = Organization.objects.create(
                    slug=f"p10-{i}", legal_name=f"P10 {i}", display_name=f"P10 {i}",
                    status="active", created_by=owner,
                )
                PartnerOrganizationLink.objects.create(
                    partner=partner, organization=org, started_at=timezone.now(), created_by=creator,
                )
        print("SEEDED")
        """,
    )
    if rc or "SEEDED" not in out:
        raise RuntimeError(f"could not seed the Phase 9 data: {last_line(err)}")
    with connect(db, **SUPER) as conn:
        owner_id = conn.execute("SELECT id FROM identity_user WHERE email = 'owner@p10.example'").fetchone()[0]
        partner_id = conn.execute("SELECT id FROM partners_account WHERE code = 'p10-partner'").fetchone()[0]
        for i, state in enumerate(org_states):
            org_id = conn.execute("SELECT id FROM organizations_organization WHERE slug = %s", (f"p10-{i}",)).fetchone()[0]
            conn.execute(
                "INSERT INTO partners_owner_invite (id, organization_id, user_id, partner_id, status, created_at, expires_at, resolved_at) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, now(), now() + interval '14 days', "
                + ("now()" if state != "pending" else "NULL")
                + ")",
                (org_id, owner_id, partner_id, state),
            )
    return list(org_states)


def upgrade() -> None:
    print("\n== PHASE 9 -> PHASE 10 UPGRADE ==")
    create_database(UPGRADE)
    create_vector_extension(UPGRADE)
    rc, out, err = django(UPGRADE, BEFORE_TARGETS + "    executor.migrate(targets)\n    print('P9-BUILT')\n")
    record("upgrade: built a Phase 9-shaped database", rc == 0 and "P9-BUILT" in out, last_line(err) if rc else "")
    if rc:
        return
    cols = {r[0] for r in q(UPGRADE, "SELECT column_name FROM information_schema.columns WHERE table_name='partners_owner_invite'")}
    record("upgrade: it has none of the Phase 10 columns", not (cols & set(NEW_COLUMNS)))

    states = seed_legacy_invitations(UPGRADE)
    before = checksums(UPGRADE)
    print("  seeded:", {t: c for t, (c, _) in before.items()})
    record("upgrade: legacy invitations of every state are present", before["partners_owner_invite"][0] == len(states))

    rc, out, err = manage(UPGRADE, "migrate", "--noinput")
    record("upgrade: migrate Phase 9 -> Phase 10", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    after = checksums(UPGRADE)
    changed = {t: (before[t], after[t]) for t in GUARDED if before[t] != after[t]}
    record("upgrade: no pre-existing column of any row changed or was lost", not changed, str(changed)[:300])

    rows = q(UPGRADE, "SELECT status, token_hash, send_count, last_sent_at, revoked_at FROM partners_owner_invite ORDER BY status")
    safe = all(r[1] == "" and r[2] == 0 and r[3] is None and r[4] is None for r in rows)
    record(
        "upgrade: a legacy invitation has no redeemable link and no send history (nothing can claim it until it is sent one)",
        safe and len(rows) == len(states),
        str(rows)[:200],
    )
    verify_phase10_schema(UPGRADE, "upgrade")

    # Rows with no link coexist (the unique index ignores empty hashes), yet a
    # real hash can name only one invitation.
    with connect(UPGRADE, **SUPER) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM partners_owner_invite ORDER BY id").fetchall()]
        conn.execute("UPDATE partners_owner_invite SET token_hash = 'dup' WHERE id = %s", (ids[0],))
        refused = False
        try:
            conn.execute("UPDATE partners_owner_invite SET token_hash = 'dup' WHERE id = %s", (ids[1],))
        except Exception as exc:  # psycopg UniqueViolation
            refused = "uq_owner_invite_token_hash" in str(exc)
        conn.execute("UPDATE partners_owner_invite SET token_hash = ''")
    record(
        "upgrade: the unique index refuses one hash on two invitations, and allows any number without one",
        refused and len(rows) == len(states),
    )
    restored = checksums(UPGRADE)
    record("upgrade: the probe left the guarded columns as they were", restored == before)

    # A real deployment may have to roll back: reverse, then apply again.
    rc, out, err = manage(UPGRADE, "migrate", "partners", "0002", "--noinput")
    record("upgrade: partners.0003 reverses over populated data", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    rolled = checksums(UPGRADE)
    record("upgrade: the rollback leaves every old column of every row intact", rolled == before, str({t: (before[t], rolled[t]) for t in GUARDED if before[t] != rolled[t]})[:300])
    cols = {r[0] for r in q(UPGRADE, "SELECT column_name FROM information_schema.columns WHERE table_name='partners_owner_invite'")}
    record("upgrade: the rollback removed the Phase 10 columns", not (cols & set(NEW_COLUMNS)))
    rc, out, err = manage(UPGRADE, "migrate", "--noinput")
    record("upgrade: it applies again", rc == 0, last_line(err) if rc else "")
    if rc:
        return
    again = checksums(UPGRADE)
    record("upgrade: and the rows are still the same", again == before)
    rc, out, err = manage(UPGRADE, "makemigrations", "--check", "--dry-run")
    record("upgrade: models match migrations (makemigrations --check)", rc == 0)


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
