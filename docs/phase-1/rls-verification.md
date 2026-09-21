# RLS Verification — Spike R-01

**Status:** Complete · **Verdict: PASS**
**Date:** 2026-09-21
**Risk:** [R-01](../architecture/15-risk-register.md#r-01--rls-session-variable-leaking-across-pooled-connections) (P0)

---

## 1. The question

> PgBouncer in transaction pooling mode returns a server connection to the
> pool at `COMMIT`. A session-level `SET app.organization_id` would persist on
> that connection and be inherited by the next transaction — belonging to a
> different tenant. Result: silent cross-tenant reads with no error.

Phase 0 resolved this **by decision**: use `set_config('app.organization_id',
…, is_local => true)` only, always inside an explicit transaction. Phase 1 had
to prove that decision holds against a **real PgBouncer in transaction mode**,
not assume it.

## 2. How it was measured

`backend/tests/spikes/spike_r01_rls_pgbouncer.py`, against the
docker-compose stack.

| Parameter | Value | Why |
|---|---|---|
| PgBouncer pool mode | `transaction` | The mode the risk is about |
| `default_pool_size` | **2** | With 24 clients on 2 server connections, reuse is *guaranteed* rather than incidental. If the variable could leak, this finds it. |
| `min_pool_size` | 0 | Pre-warmed connections are never released, which would prevent test-database teardown |
| `server_reset_query` | *empty* | `DISCARD ALL` would mask the very leak being tested. Safety must come from PostgreSQL's transaction semantics, not from a pooler cleanup step. |
| Workers | 24 threads | Synchronised on a barrier so transactions genuinely interleave |
| Transactions | 960 (24 × 40) | Each picks a tenant at random and holds the transaction open 0.5–3 ms |
| Database role | `w360_app` | Non-owner, `NOSUPERUSER`, `NOBYPASSRLS` |

Each iteration sets its tenant, sleeps briefly so other transactions overlap,
then reads back **both** the session variable and the visible rows. A leak
would show as either a foreign value in the variable or a foreign row in the
result.

## 3. Results

```
SPIKE R-01 RESULT: PASS
```

| Check | Result | Evidence |
|---|---|---|
| App role is not superuser | **PASS** | `current_user=w360_app, rolsuper=False` |
| App role has `NOBYPASSRLS` | **PASS** | `rolbypassrls=False` |
| Missing tenant context returns zero rows | **PASS** | 0 rows visible with no variable set |
| Tenant context returns own rows | **PASS** | 1 row for tenant A |
| Empty-string variable returns zero rows | **PASS** | 0 rows, no `22P02` error |
| Tenant A cannot read tenant B | **PASS** | 0 rows for B's membership by primary key |
| Tenant B cannot read tenant A | **PASS** | 0 rows |
| `WITH CHECK` blocks cross-tenant INSERT | **PASS** | Refused with SQLSTATE `42501` |
| Variable does not survive `ROLLBACK` | **PASS** | Setting `''`, 0 rows visible afterwards |
| Savepoint rollback restores the outer tenant | **PASS** | Inner B saw 1 row; after rollback the variable was A's again |
| **No leakage under interleaved transactions** | **PASS** | **960 transactions, 24 workers, 0 leaks, 0 driver errors, 7.72 s (124.4 tx/s)** |
| Server connections genuinely reused | **PASS** | PgBouncer reported **2** server connections for 24 clients |

Tenant split across the run was 479 / 481, so both tenants were exercised
roughly equally rather than one dominating.

### The result that matters

**Zero leaks across 960 interleaved transactions sharing two server
connections.** `set_config(…, is_local => true)` is reverted by PostgreSQL at
transaction end, including on `ROLLBACK`, so nothing survives to be inherited
by the next borrower of the connection.

**The Phase 0 decision is correct. The documented fallback (session pooling
for the web tier) is NOT required.** `W360_DB_POOL_MODE` remains
`transaction`.

## 4. A second finding: prepared statements are genuinely unsafe here

The first run of the spike failed — not on leakage, but with
`DuplicatePreparedStatement: prepared statement "_pg3_0" already exists`, 22
of 24 workers dead and only 190 of 960 transactions completed.

Cause: psycopg3 automatically prepares a statement after five executions.
Under transaction pooling, client A prepares `_pg3_0` on a server connection,
that connection is handed to client B, and B's attempt to prepare its own
`_pg3_0` collides.

[10-performance-concurrency.md](../architecture/10-performance-concurrency.md)
§4 lists prepared statements as "Broken" under transaction pooling with
"disable statement caching in the driver" as the mitigation. That is now
**measured rather than assumed**, and the spike keeps a permanent check for
it:

| Driver configuration | Transactions completed | Connection failures | Leaks |
|---|---|---|---|
| `prepare_threshold=None` (ours) | **960 / 960** | 0 | **0** |
| Auto-preparation enabled | **63 / 960** | 5 | **0** |

Two things worth stating:

- `prepare_threshold=None` in `config/settings/base.py` is a **correctness
  requirement**, not a tuning preference. A future change that removes it
  produces an outage, not a slowdown.
- Even while the driver was failing, **leakage remained zero**. The isolation
  guarantee does not depend on the driver behaving.

## 5. What else this verified

Two behaviours were discovered during implementation and are now covered by
permanent tests, because both are easy to get wrong in the same direction:

**Empty string, not NULL.** Once a variable has been set and reset within a
session, `current_setting('app.organization_id', true)` returns `''` rather
than `NULL`, and `''::uuid` raises `22P02`. A naive policy predicate would
error on every query from a reset connection instead of returning zero rows.
The policy therefore uses `NULLIF(current_setting(…), '')::uuid`, and
`test_empty_tenant_variable_returns_zero_rows` pins it.

**`unscoped_access()` relaxes Layer 2 only.** The ORM escape hatch does not
touch RLS. A cross-tenant writer must *also* enter the organization's
database scope, which is what `organization_db_scope()` exists for. This was
found when provisioning's writes were refused by `WITH CHECK` — the
defence-in-depth property working exactly as designed.

## 6. Permanent regression coverage

The spike is a one-off investigation; these run on every PR:

| File | Tests | Covers |
|---|---|---|
| `tests/isolation/test_rls_isolation.py` | 19 | Correct/wrong/missing tenant, `WITH CHECK`, commit, rollback, savepoint, connection reuse, ORM/RLS agreement |
| `tests/isolation/test_every_tenant_model.py` | 91 | Parametrised over **every** tenant model: scoped-manager raise, complete policy, cross-tenant invisibility, fail-closed, index prefix |
| `tests/security/test_security_baseline.py` | 5 | Role is non-owner, cannot bypass RLS, audit grants |

The parametrised suite is the important one: a tenant model added next year is
covered the moment it exists, rather than when someone remembers to write a
test for it.

**CI runs the suite as `w360_app`.** `backend/conftest.py` migrates as the
owner and then rebinds the connection to the non-owner role. A suite running
as the table owner would silently bypass every policy and go green while
production was open.

## 7. Conclusion

| | |
|---|---|
| **R-01 status** | **Resolved with evidence** |
| **Decision** | Transaction pooling retained; `set_config(…, true)` confirmed safe |
| **Fallback needed** | No |
| **New requirement discovered** | `prepare_threshold=None` is correctness-critical, now measured and regression-tested |
| **Residual risk** | The spike runs on one host with loopback networking. Cross-AZ latency changes timing but not semantics: `SET LOCAL` reversion is a PostgreSQL guarantee, not a race. Re-run against staging before first production traffic. |

Raw results: `backend/tests/spikes/r01_results.json` (regenerated by
`make spikes`, and uploaded as a CI artifact).
