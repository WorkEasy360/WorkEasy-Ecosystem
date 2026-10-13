"""SPIKE R-01 - PostgreSQL RLS under PgBouncer transaction pooling.

The question (15-risk-register.md, R-01):

    PgBouncer in transaction pooling mode returns a server connection to the
    pool at COMMIT. Does `set_config('app.organization_id', ..., is_local =>
    true)` stay confined to its transaction, or can it leak into the next
    tenant's transaction on the same reused server connection?

A leak here is a silent cross-tenant read with no error anywhere - the one
class of defect the business does not recover from. So this is measured
against a **real PgBouncer in transaction mode**, not assumed.

The pool is deliberately tiny (default_pool_size=2, see
infra/pgbouncer/pgbouncer.ini). With 2 server connections serving 24 workers,
reuse is guaranteed rather than incidental: if the variable could leak, this
harness will find it.

Run:
    python backend/tests/spikes/spike_r01_rls_pgbouncer.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

# --- connection coordinates ----------------------------------------------

PGBOUNCER = {
    "host": os.environ.get("W360_DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("W360_DB_PORT", 6442)),
    "dbname": os.environ.get("W360_DB_NAME", "workeasy360"),
    "user": os.environ.get("W360_DB_APP_USER", "w360_app"),
    "password": os.environ.get("W360_DB_APP_PASSWORD", "w360_app_pw"),
}
DIRECT = {
    **PGBOUNCER,
    "port": int(os.environ.get("W360_DB_DIRECT_PORT", 5442)),
}
MIGRATOR = {
    **DIRECT,
    "user": os.environ.get("W360_DB_MIGRATOR_USER", "w360_migrator"),
    "password": os.environ.get("W360_DB_MIGRATOR_PASSWORD", "w360_migrator_pw"),
}

WORKERS = 24
ITERATIONS_PER_WORKER = 40


@dataclass
class Findings:
    checks: list[dict] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append({"check": name, "passed": passed, "detail": detail})
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}: {detail}")

    @property
    def all_passed(self) -> bool:
        return all(c["passed"] for c in self.checks)


def dsn(cfg: dict) -> str:
    return (
        f"host={cfg['host']} port={cfg['port']} dbname={cfg['dbname']} "
        f"user={cfg['user']} password={cfg['password']}"
    )


def connect(cfg: dict, *, prepared_statements: bool = False, **kwargs):
    """Open a connection, mirroring the application's driver configuration.

    ``prepare_threshold=None`` disables psycopg3's automatic statement
    preparation. This is not a tuning preference - it is mandatory under
    transaction pooling, and the spike measures what happens without it
    (see ``check_prepared_statement_hazard``).
    """
    conn = psycopg.connect(dsn(cfg), **kwargs)
    if prepared_statements:
        # threshold=1 prepares on second use rather than sixth. This models
        # steady-state production, where every hot query has long since
        # crossed psycopg's default threshold of 5 - it makes the hazard
        # deterministic rather than dependent on how many iterations the
        # harness happens to run.
        conn.prepare_threshold = 1
    else:
        conn.prepare_threshold = None
    return conn


# --- fixtures -------------------------------------------------------------


def seed(org_a: uuid.UUID, org_b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Create two organizations with one distinctive membership each.

    Seeding runs as the migrator, which still has to set the tenant variable:
    FORCE ROW LEVEL SECURITY applies the policy to the table owner too. If
    this insert succeeded without setting the variable, FORCE would be missing
    and the whole scheme would be inert (R-02).
    """
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    mem_a, mem_b = uuid.uuid4(), uuid.uuid4()

    with connect(MIGRATOR, autocommit=False) as conn:
        # Cleaning up prior runs is itself an isolation test. A bare
        # `DELETE FROM organizations_membership` matches zero rows because no
        # tenant variable is set - fail-closed applies to the owner too, since
        # FORCE ROW LEVEL SECURITY is on. So each organization's rows must be
        # deleted inside its own tenant context.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM organizations_organization WHERE slug LIKE 'spike-%%'"
            )
            stale_orgs = [row[0] for row in cur.fetchall()]
        conn.commit()

        for stale in stale_orgs:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.organization_id', %s, true)", [str(stale)]
                )
                cur.execute("DELETE FROM organizations_membership")
            conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM organizations_organization WHERE slug LIKE 'spike-%%'"
            )
            cur.execute("DELETE FROM identity_user WHERE email LIKE 'spike-%%'")

            for user_id, email in ((user_a, "spike-a@test"), (user_b, "spike-b@test")):
                cur.execute(
                    "INSERT INTO identity_user "
                    "(id, password, email, email_original, full_name, status, "
                    " is_staff, is_superuser, failed_login_count, session_epoch, "
                    " mfa_failed_attempts, acquisition_source, created_at, updated_at) "
                    "VALUES (%s,'!',%s,%s,'Spike','active',false,false,0,0,0,'',"
                    "now(),now())",
                    [user_id, email, email],
                )
            for org_id, slug, creator in (
                (org_a, "spike-a", user_a),
                (org_b, "spike-b", user_b),
            ):
                cur.execute(
                    "INSERT INTO organizations_organization "
                    "(id, slug, legal_name, display_name, status, residency_region, "
                    " shard_key, created_by_id, created_at, updated_at, suspension_reason, "
                    " require_mfa, ip_allowlist_enabled, ip_allowlist, "
                    " security_policy_version) "
                    "VALUES (%s,%s,%s,%s,'active','eu-west-1',%s,%s,now(),now(),'',"
                    "false,false,'[]',1)",
                    [org_id, slug, slug, slug, str(org_id), creator],
                )
        conn.commit()

        # Membership is tenant-scoped, so each insert must run with its own
        # tenant variable set - which is itself a WITH CHECK test.
        for org_id, mem_id, user_id in (
            (org_a, mem_a, user_a),
            (org_b, mem_b, user_b),
        ):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.organization_id', %s, true)", [str(org_id)]
                )
                cur.execute(
                    "INSERT INTO organizations_membership "
                    "(id, organization_id, user_id, status, is_owner, member_type, "
                    " created_at, updated_at) "
                    "VALUES (%s,%s,%s,'active',true,'internal',now(),now())",
                    [mem_id, org_id, user_id],
                )
            conn.commit()

    return mem_a, mem_b


# --- the checks -----------------------------------------------------------


def check_roles(findings: Findings) -> None:
    """R-02: the runtime role must not be able to bypass RLS."""
    with connect(PGBOUNCER) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT current_user, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )
        role, is_super, bypass = cur.fetchone()
    findings.record(
        "app role is not superuser",
        not is_super,
        f"current_user={role}, rolsuper={is_super}",
    )
    findings.record(
        "app role has NOBYPASSRLS",
        not bypass,
        f"current_user={role}, rolbypassrls={bypass}",
    )


def check_fail_closed(findings: Findings, org_a: uuid.UUID) -> None:
    """An unset variable must match zero rows, never all rows.

    `current_setting(..., true)` returns NULL when unset; `organization_id =
    NULL` evaluates to NULL, which is not TRUE, so no row matches. Fail-closed
    is the *default* outcome rather than an added check - but it is asserted
    here because the whole design rests on it.
    """
    with connect(PGBOUNCER, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM organizations_membership")
            without_context = cur.fetchone()[0]
        conn.rollback()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.organization_id', %s, true)", [str(org_a)]
            )
            cur.execute("SELECT count(*) FROM organizations_membership")
            with_context = cur.fetchone()[0]
        conn.rollback()

    findings.record(
        "missing tenant context returns zero rows",
        without_context == 0,
        f"rows visible with no app.organization_id = {without_context} (expected 0)",
    )
    findings.record(
        "tenant context returns own rows",
        with_context == 1,
        f"rows visible for tenant A = {with_context} (expected 1)",
    )


def check_empty_string_fail_closed(findings: Findings) -> None:
    """An empty-string variable must also match nothing.

    This is the case a naive policy gets wrong. `current_setting(x, true)`
    returns '' (not NULL) once the variable has been *set and reset* within a
    session, and `''::uuid` raises rather than yielding NULL - so the policy
    uses NULLIF(..., '')::uuid. Without that, a reset connection would throw
    22P02 on every query instead of returning zero rows.
    """
    with connect(PGBOUNCER, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.organization_id', '', true)")
            try:
                cur.execute("SELECT count(*) FROM organizations_membership")
                rows = cur.fetchone()[0]
                ok, detail = rows == 0, f"empty variable -> {rows} rows (expected 0)"
            except psycopg.errors.InvalidTextRepresentation as exc:
                ok, detail = False, f"empty variable raised {exc.__class__.__name__}"
        conn.rollback()
    findings.record("empty tenant context returns zero rows", ok, detail)


def check_cross_tenant_read(
    findings: Findings,
    org_a: uuid.UUID,
    org_b: uuid.UUID,
    mem_a: uuid.UUID,
    mem_b: uuid.UUID,
) -> None:
    """Neither tenant may read the other, even by primary key."""
    results = {}
    with connect(PGBOUNCER, autocommit=False) as conn:
        for label, org, foreign_mem in (("A->B", org_a, mem_b), ("B->A", org_b, mem_a)):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.organization_id', %s, true)", [str(org)]
                )
                cur.execute(
                    "SELECT count(*) FROM organizations_membership WHERE id = %s",
                    [foreign_mem],
                )
                results[label] = cur.fetchone()[0]
            conn.rollback()

    findings.record(
        "tenant A cannot read tenant B",
        results["A->B"] == 0,
        f"A querying B's membership by primary key returned {results['A->B']} rows",
    )
    findings.record(
        "tenant B cannot read tenant A",
        results["B->A"] == 0,
        f"B querying A's membership by primary key returned {results['B->A']} rows",
    )


def check_with_check_on_insert(
    findings: Findings,
    org_a: uuid.UUID,
    org_b: uuid.UUID,
    user_b: uuid.UUID | None = None,
) -> None:
    """WITH CHECK must reject writing a row into another tenant.

    USING filters reads and the old row on UPDATE; WITH CHECK validates the
    *new* row. Without it, code in tenant A could insert a row owned by tenant
    B even though it could not read one back.
    """
    blocked = False
    detail = ""
    with connect(PGBOUNCER, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.organization_id', %s, true)", [str(org_a)]
            )
            try:
                cur.execute(
                    "INSERT INTO organizations_membership "
                    "(id, organization_id, user_id, status, is_owner, member_type, "
                    " created_at, updated_at) "
                    "VALUES (%s,%s,%s,'active',false,'internal',now(),now())",
                    [uuid.uuid4(), org_b, user_b],
                )
                detail = "INSERT into tenant B from tenant A context SUCCEEDED"
            except psycopg.errors.InsufficientPrivilege as exc:
                blocked = True
                detail = f"blocked by RLS WITH CHECK ({exc.diag.sqlstate})"
            except psycopg.Error as exc:
                blocked = True
                detail = f"blocked ({exc.__class__.__name__}, {exc.diag.sqlstate})"
        conn.rollback()
    findings.record("WITH CHECK blocks cross-tenant INSERT", blocked, detail)


def check_rollback_isolation(findings: Findings, org_a: uuid.UUID) -> None:
    """The variable must not survive a ROLLBACK either.

    SET LOCAL is reverted at transaction *end*, which includes an aborted
    transaction. If it survived a rollback, an errored request would leave its
    tenant on the connection for the next borrower.
    """
    with connect(PGBOUNCER, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.organization_id', %s, true)", [str(org_a)]
            )
        conn.rollback()

        with conn.cursor() as cur:
            cur.execute("SELECT current_setting('app.organization_id', true)")
            after = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM organizations_membership")
            rows = cur.fetchone()[0]
        conn.rollback()

    findings.record(
        "tenant variable does not survive ROLLBACK",
        rows == 0,
        f"after rollback: setting={after!r}, visible rows={rows} (expected 0)",
    )


def check_nested_transaction(
    findings: Findings, org_a: uuid.UUID, org_b: uuid.UUID
) -> None:
    """A SAVEPOINT rollback must not restore a different tenant's context.

    Django uses savepoints for nested atomic blocks, so this is the real
    behaviour of a nested service call that fails and is caught.
    """
    with connect(PGBOUNCER, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.organization_id', %s, true)", [str(org_a)]
            )
            cur.execute("SAVEPOINT nested")
            cur.execute(
                "SELECT set_config('app.organization_id', %s, true)", [str(org_b)]
            )
            cur.execute("SELECT count(*) FROM organizations_membership")
            inner = cur.fetchone()[0]
            cur.execute("ROLLBACK TO SAVEPOINT nested")
            cur.execute("SELECT current_setting('app.organization_id', true)")
            restored = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM organizations_membership")
            outer = cur.fetchone()[0]
        conn.rollback()

    findings.record(
        "savepoint rollback restores the outer tenant",
        restored == str(org_a) and inner == 1 and outer == 1,
        f"inner(B)={inner} row, after rollback setting={restored[:8]}..., rows={outer}",
    )


# --- the concurrency test -------------------------------------------------


def check_interleaved_concurrency(
    findings: Findings,
    org_a: uuid.UUID,
    org_b: uuid.UUID,
    mem_a: uuid.UUID,
    mem_b: uuid.UUID,
    *,
    prepared_statements: bool = False,
    label: str = "no tenant context leakage under interleaved transactions",
    record: bool = True,
) -> dict:
    """The core R-01 test: many workers, interleaved tenants, tiny pool.

    Each iteration opens a transaction, sets its tenant, reads back both the
    variable and the visible rows, and asserts it saw exactly its own tenant.
    With default_pool_size=2 and 24 workers, every worker is repeatedly handed
    a server connection that a *different* tenant just used.

    A leak manifests in one of two ways, and both are checked:
      - the variable reads back as another tenant's id, or
      - a row belonging to another tenant becomes visible.
    """
    expected = {org_a: mem_a, org_b: mem_b}
    leaks: list[dict] = []
    observations = Counter()
    errors: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def worker(worker_id: int) -> None:
        rng = random.Random(worker_id)
        try:
            with connect(
                PGBOUNCER, prepared_statements=prepared_statements, autocommit=False
            ) as conn:
                # Start together so transactions genuinely interleave rather
                # than running as a staggered sequence.
                barrier.wait(timeout=30)

                for _ in range(ITERATIONS_PER_WORKER):
                    org = rng.choice([org_a, org_b])
                    own_membership = expected[org]

                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT set_config('app.organization_id', %s, true)",
                            [str(org)],
                        )
                        # Hold the transaction open briefly so that other
                        # workers' transactions overlap this one.
                        time.sleep(rng.uniform(0.0005, 0.003))

                        cur.execute("SELECT current_setting('app.organization_id', true)")
                        seen_setting = cur.fetchone()[0]

                        cur.execute(
                            "SELECT id, organization_id FROM organizations_membership"
                        )
                        rows = cur.fetchall()

                    conn.commit()

                    with lock:
                        observations[str(org)] += 1

                    if seen_setting != str(org):
                        with lock:
                            leaks.append(
                                {
                                    "kind": "variable_leak",
                                    "worker": worker_id,
                                    "expected": str(org),
                                    "observed": seen_setting,
                                }
                            )
                    for row_id, row_org in rows:
                        if str(row_org) != str(org):
                            with lock:
                                leaks.append(
                                    {
                                        "kind": "row_leak",
                                        "worker": worker_id,
                                        "expected_org": str(org),
                                        "observed_org": str(row_org),
                                        "row_id": str(row_id),
                                    }
                                )
                    if len(rows) != 1 or str(rows[0][0]) != str(own_membership):
                        with lock:
                            leaks.append(
                                {
                                    "kind": "unexpected_row_set",
                                    "worker": worker_id,
                                    "expected_org": str(org),
                                    "rows": [str(r[0]) for r in rows],
                                }
                            )
        except Exception as exc:  # pragma: no cover - harness failure
            with lock:
                errors.append(f"worker {worker_id}: {type(exc).__name__}: {exc}")

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(worker, i) for i in range(WORKERS)]
        for future in as_completed(futures):
            future.result()
    duration = time.perf_counter() - started

    total = sum(observations.values())
    if record:
        findings.record(
            label,
            not leaks and not errors,
            (
                f"{total} transactions across {WORKERS} workers through PgBouncer "
                f"(transaction mode, pool_size=2) in {duration:.2f}s; "
                f"{len(leaks)} leaks, {len(errors)} driver errors"
            ),
        )
    if leaks:
        print("\n  LEAK DETAIL (first 5):")
        for leak in leaks[:5]:
            print(f"    {leak}")
    if errors:
        print("\n  HARNESS ERRORS (first 5):")
        for err in errors[:5]:
            print(f"    {err}")

    return {
        "workers": WORKERS,
        "transactions": total,
        "duration_s": round(duration, 3),
        "transactions_per_second": round(total / duration, 1) if duration else 0,
        "leaks": len(leaks),
        "errors": errors[:5],
        "by_tenant": dict(observations),
    }


def check_prepared_statement_hazard(
    findings: Findings,
    org_a: uuid.UUID,
    org_b: uuid.UUID,
    mem_a: uuid.UUID,
    mem_b: uuid.UUID,
) -> dict:
    """Measure what happens when the driver's default preparation is left on.

    10-performance-concurrency.md section 4 lists prepared statements as
    "Broken" under transaction pooling, with "disable statement caching in the
    driver" as the mitigation. This check *measures* that rather than taking
    it on trust, because the failure is a correctness-adjacent outage: once a
    connection has auto-prepared a statement, another client borrowing that
    server connection raises 42P05 and its request fails.

    A failure here is the **expected and desired** result. It is what makes
    ``prepare_threshold=None`` in config/settings/base.py a measured
    requirement rather than cargo cult.
    """
    result = check_interleaved_concurrency(
        findings,
        org_a,
        org_b,
        mem_a,
        mem_b,
        prepared_statements=True,
        record=False,
    )
    errors = result.get("errors", [])
    duplicate_prepared = any("PreparedStatement" in e or "42P05" in e for e in errors)

    findings.record(
        "prepared statements are confirmed unsafe under transaction pooling",
        duplicate_prepared,
        (
            f"with psycopg auto-preparation enabled: {result['transactions']} of "
            f"{WORKERS * ITERATIONS_PER_WORKER} transactions completed, "
            f"{len(errors)} connection(s) failed with DuplicatePreparedStatement. "
            f"This is why prepare_threshold=None is mandatory."
        )
        if duplicate_prepared
        else (
            f"expected DuplicatePreparedStatement errors but saw none "
            f"({result['transactions']} transactions, {len(errors)} errors). "
            f"The hazard may have been mitigated elsewhere - verify before "
            f"relaxing prepare_threshold."
        ),
    )
    findings.record(
        "no tenant leakage even while the driver misbehaves",
        result["leaks"] == 0,
        f"{result['leaks']} leaks observed during the prepared-statement failure mode",
    )
    return result


def check_pool_reuse(findings: Findings) -> dict:
    """Confirm server connections really were shared.

    Without this the concurrency result would be meaningless: if PgBouncer
    handed every client its own server connection, no leak could occur and the
    test would pass for the wrong reason.
    """
    stats = {}
    try:
        with connect(
            {
                **PGBOUNCER,
                "dbname": "pgbouncer",
                "user": MIGRATOR["user"],
                "password": MIGRATOR["password"],
            },
            autocommit=True,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SHOW POOLS")
                cols = [d.name for d in cur.description]
                for row in cur.fetchall():
                    entry = dict(zip(cols, row))
                    if entry.get("database") == "workeasy360":
                        stats = entry
                        break
    except Exception as exc:
        findings.record(
            "pgbouncer pool statistics readable", False, f"{type(exc).__name__}: {exc}"
        )
        return {}

    server_conns = (
        int(stats.get("sv_active", 0))
        + int(stats.get("sv_idle", 0))
        + int(stats.get("sv_used", 0))
    )
    findings.record(
        "server connections were pooled and reused",
        0 < server_conns <= 4,
        f"pgbouncer reports {server_conns} server connections for "
        f"{WORKERS} clients (pool_size=2)",
    )
    return {k: str(v) for k, v in stats.items()}


# --- entry point ----------------------------------------------------------


def main() -> int:
    print("=" * 74)
    print("SPIKE R-01 - PostgreSQL RLS under PgBouncer transaction pooling")
    print("=" * 74)
    print(
        f"  pgbouncer : {PGBOUNCER['host']}:{PGBOUNCER['port']} (transaction mode, pool_size=2)"
    )
    print(f"  direct    : {DIRECT['host']}:{DIRECT['port']}")
    print(f"  app role  : {PGBOUNCER['user']}")
    print(f"  load      : {WORKERS} workers x {ITERATIONS_PER_WORKER} transactions")
    print()

    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    mem_a, mem_b = seed(org_a, org_b)

    with connect(MIGRATOR) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM identity_user WHERE email='spike-b@test'")
        user_b = cur.fetchone()[0]

    findings = Findings()

    print("Role configuration (R-02)")
    check_roles(findings)

    print("\nFail-closed behaviour")
    check_fail_closed(findings, org_a)
    check_empty_string_fail_closed(findings)

    print("\nCross-tenant isolation")
    check_cross_tenant_read(findings, org_a, org_b, mem_a, mem_b)
    check_with_check_on_insert(findings, org_a, org_b, user_b)

    print("\nTransaction semantics")
    check_rollback_isolation(findings, org_a)
    check_nested_transaction(findings, org_a, org_b)

    print("\nDriver configuration hazard (prepared statements)")
    hazard = check_prepared_statement_hazard(findings, org_a, org_b, mem_a, mem_b)

    print("\nConcurrency under connection reuse (the R-01 question)")
    concurrency = check_interleaved_concurrency(findings, org_a, org_b, mem_a, mem_b)
    pool_stats = check_pool_reuse(findings)

    print("\n" + "=" * 74)
    verdict = "PASS" if findings.all_passed else "FAIL"
    print(f"SPIKE R-01 RESULT: {verdict}")
    print("=" * 74)

    report = {
        "spike": "R-01",
        "question": "Does set_config(..., is_local=>true) stay confined to its "
        "transaction under PgBouncer transaction pooling?",
        "verdict": verdict,
        "pgbouncer": {"pool_mode": "transaction", "default_pool_size": 2},
        "prepared_statement_hazard": hazard,
        "checks": findings.checks,
        "concurrency": concurrency,
        "pgbouncer_pools": pool_stats,
    }
    out = Path(__file__).parent / "r01_results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nResults written to {out}")

    return 0 if findings.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
