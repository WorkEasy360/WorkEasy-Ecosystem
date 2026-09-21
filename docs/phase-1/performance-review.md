# Phase 1 Performance Review

**Date:** 2026-09-21
**Scope:** The decisions Phase 0 identifies as expensive to retrofit —
connection strategy, pagination, indexes, query counts, contention points.

Phase 1 has no traffic. This review therefore covers **measured hot paths**
and **structural choices**, not tuning. Per
[10-performance-concurrency.md](../architecture/10-performance-concurrency.md)
§1, every optimisation must reference a profile or a production metric, and
none is claimed here on speculation.

---

## 1. Measured

### Entitlement read path (R-14, full detail in [entitlement-benchmark.md](entitlement-benchmark.md))

| Scenario | p50 | p95 | p99 | Queries |
|---|---|---|---|---|
| Baseline `SELECT 1` through PgBouncer | 0.478 ms | 0.585 ms | 0.786 ms | 1 |
| Baseline `cache.get` through Redis | 0.451 ms | 0.560 ms | 0.674 ms | 0 |
| First check in a request | 1.586 ms | 2.340 ms | 3.143 ms | 1 |
| **Repeat check** | **0.0043 ms** | **0.0054 ms** | **0.0065 ms** | **0** |
| `feature()` / `quota()` in scope | 0.0015 ms | 0.0017 ms | 0.0017 ms | 0 |
| **Full request (6 checks)** | **1.559 ms** | **1.794 ms** | **2.077 ms** | **1** |
| Snapshot-missing fallback | 6.245 ms | 8.724 ms | 9.380 ms | 4 |

**Budget revised, in writing**: per request rather than per check, because
request-local caching makes checks non-independent. 2 ms p50 / 5 ms p95 /
10 ms p99, ≤ 2 queries. Measured 1.56 / 1.79 / 2.08 ms with 1 query.

The I/O floor is the finding worth carrying forward: **0.93 ms of any
storage-touching path is round trips**, so no amount of application
optimisation gets a cold check under 1 ms on this topology.

### RLS overhead

The R-01 spike ran 960 transactions at **124.4 tx/s** through PgBouncer with a
2-connection pool and deliberate 0.5–3 ms sleeps inside each transaction. The
sleeps dominate, so this is not an RLS cost measurement — it is evidence that
RLS plus transaction pooling sustains concurrent load without error.

RLS cost is controlled structurally instead: **every index on a tenant table
leads with `organization_id`**, asserted for every tenant model by
`test_has_an_organization_leading_index`, with one documented exemption
(`ix_membership_user`, which serves the pre-context "which organizations does
this user belong to?" query at login).

### Query counts — pinned, not observed

| Path | Queries | Test |
|---|---|---|
| 75 consecutive cached entitlement checks | **0** | `test_repeat_checks_in_one_request_issue_no_further_queries` |
| Membership list, 11 members | **≤ 2** | `test_membership_list_has_no_n_plus_one` |
| `/healthz/live` | **0** | `test_liveness_issues_no_queries` |

The membership assertion is the highest-value one here: without
`select_related("user")` it is one query per row to fetch each email. That is
the single most common performance defect in Django, invisible in code
review, and trivially caught by pinning the count.

## 2. Structural decisions verified

### Connection strategy

| Setting | Value | Why |
|---|---|---|
| `CONN_MAX_AGE` | **0** | Persistent Django connections behind PgBouncer means two pools fighting, and idle Django connections pin server connections, defeating the pooler. Asserted by a production config check. |
| `DISABLE_SERVER_SIDE_CURSORS` | **True** | Broken under transaction pooling. Asserted. |
| `prepare_threshold` | **None** | Broken under transaction pooling — **measured**, not assumed (R-01 §4). |
| `ATOMIC_REQUESTS` | **False** | The tenancy middleware owns the transaction that carries the RLS variable. Asserted. |
| `statement_timeout` | 10 s (web) | A runaway query is killed by PostgreSQL rather than held by the application. |
| `lock_timeout` | 5 s | A blocked write fails fast instead of occupying a worker. |
| `idle_in_transaction_session_timeout` | 15 s | Backstop against a transaction left open across an external call. |
| `connect_timeout` | 2 s | Failover surfaces as a fast error rather than a hang. |

Timeouts are applied as connection `options`, so they reach PostgreSQL as
startup parameters and survive PgBouncer's reuse of the connection.

### Pagination

Keyset by default, in `organizations/selectors.py`:

- Cursor encodes `(created_at, id)`; **the tie-break on `id` is load-bearing** —
  a cursor on the timestamp alone overlaps or skips rows at page boundaries
  when timestamps collide.
- Page size: default 25, **hard maximum 100**. `limit=10000` is clamped, not
  honoured, and `test_membership_list_is_bounded_and_scoped` asserts it.
- `has_more` is derived by fetching one extra row. No `COUNT(*)`: it is
  expensive on a large filtered table and the answer is not needed.
- A malformed cursor yields the first page rather than a 400 — cursors are
  opaque, they get truncated in URLs, and an error is a worse experience than
  restarting the listing.

### Indexes — created only where justified

Every index has a query it serves. No speculative indexes were added.

| Table | Index | Serves |
|---|---|---|
| `organizations_membership` | `(organization, status)` | Active-member listing |
| | `(organization, user)` | Membership resolution at the request boundary |
| | `(user)` *(exempt)* | Login-time "which organizations?", before tenant context exists |
| `entitlements_snapshot` | unique `(organization)` | The one-row-per-org read the whole cache is built from |
| `entitlements_entitlement` | `(organization, product_code)` | Live-resolution fallback |
| `events_outbox` | **partial** `(status, available_at, id)` where status in (pending, failed) | The relay's claim query |
| `audit_event` | `(organization, -occurred_at)` and three more | Customer-facing audit browsing |

The outbox partial index matters disproportionately: without it the relay
degrades as published rows accumulate, because the planner scans them
repeatedly looking for the few pending ones.

### Contention points addressed

From 10-performance-concurrency.md §12, the ones Phase 1 touches:

| # | Point | Mechanism | Verified by |
|---|---|---|---|
| 6 | Outbox relay — duplicate publishing | `FOR UPDATE SKIP LOCKED` batching | `test_concurrent_relays_never_claim_the_same_event` — 8 workers, 40 events, **0 double-claims** |
| 7 | Entitlement recompute racing | `pg_advisory_xact_lock(hashtext(org_id))` | `test_concurrent_entitlement_recomputes_converge` — 8 concurrent applies, one consistent snapshot |
| 10 | Event handler races | `ProcessedEvent` unique constraint | `test_concurrent_consumers_apply_an_event_exactly_once` — 8 workers, **1 applied, 7 rejected** |

Points 1–5, 8, 9 and 11–13 concern models Phase 1 does not implement (stock,
ledger, invoice numbering, payouts, seat assignment service). The seat model
carries its partial unique constraint already; the assignment *service* with
`SELECT FOR UPDATE` arrives with commercial.

### Bulk operations

`bulk_create`, `bulk_update`, `QuerySet.update` and `QuerySet.delete` are all
overridden on `TenantScopedQuerySet`, because they bypass `save()` entirely.
Unguarded, the R-10 write block would be defeated by a one-line change that
looks entirely reasonable in review.

`prune_published` deletes in 1 000-row chunks rather than one statement: a
single `DELETE` over millions of rows exceeds the statement timeout and holds
locks for the duration.

## 3. Findings

| Severity | Finding | Status |
|---|---|---|
| **P1** | The first entitlement check costs one database query (the snapshot version read). Removing it needs either a cached version with a TTL or a stable cache key with delete-on-write. | **Carried, deliberately.** 1.56 ms against a 2 ms budget; the optimisation trades a correctness risk for 1.1 ms. Trigger: p95 > 4 ms in production, or cache hit ratio < 0.95. |
| **P2** | Metrics are an in-process registry, not Prometheus | Interface and cardinality discipline are what is expensive to retrofit, and both exist. Swapping the backend changes one class and no call sites. |
| **P2** | No load, soak or spike testing | There is no traffic to model and no deployed environment. Required before first production customer. |
| **P2** | Read replica not configured | No read load exists. `W360_DB_POOL_MODE` and the settings shape accommodate it. |
| **P2** | Audit `_latest_hash` is an extra query per audit write | Measured cost is one indexed lookup. Serialising it would make audit a contention point, which is the worse trade. |

## 4. Explicitly not done

Per 10-performance-concurrency.md §14, with the trigger that would change each:

| Not doing | Trigger |
|---|---|
| Distributed caching beyond the entitlement snapshot | A measured hot path, not a benchmark |
| Read replicas | Primary read load > 60 % |
| Search infrastructure | Postgres FTS p95 > 1 s |
| Materialised views | A specific aggregate in the slow log |
| Table partitioning | A single table > 500 M rows |
| Sharding | Single primary > 70 % after tuning |

None is justified by current evidence, and each would add operational surface
to maintain forever.

## 5. Conclusion

The expensive-to-retrofit decisions are made and verified: connection
strategy, keyset pagination, organization-leading indexes, query-count
assertions on hot paths, and a mechanism for each contention point Phase 1
reaches.

The one honest negative result — the entitlement per-check budget being
unachievable — is recorded with measurements and a revised budget rather than
quietly restated as a pass.
