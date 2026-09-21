# Entitlement Latency Benchmark — Spike R-14

**Status:** Complete · **Verdict: budget revised, in writing**
**Date:** 2026-09-21
**Risk:** [R-14](../architecture/15-risk-register.md#r-14--entitlement-check-latency-on-the-hot-path) (P1)

---

## 1. The question

> Several entitlement and authorization checks per request, at a 1 ms p50
> budget, is aggressive. If the cache under-performs, every endpoint in the
> platform slows simultaneously. Does the check path meet **1 ms p50 / 5 ms
> p95** on realistic data?

Phase 0's budget
([10-performance-concurrency.md](../architecture/10-performance-concurrency.md)
§3) states it **per check**.

## 2. Answer, up front

**Per check, as originally stated: no — and it is not achievable on this
topology.** One database round trip alone costs 0.48 ms p50 and one cache
round trip 0.45 ms, so 0.93 ms of the 1 ms budget is pure I/O before any
entitlement logic runs.

**Per request, which is what a user experiences: yes, comfortably.** A
realistic six-check request completes in **1.56 ms p50 / 1.79 ms p95** with
**one** database query, because request-local caching makes every check after
the first a dictionary lookup at **0.004 ms**.

The budget is therefore **revised to be stated per request**, with the
measured numbers recorded below. This is written down rather than hidden, and
no distributed-cache complexity was added to chase the original figure.

## 3. Method

`backend/tests/spikes/spike_r14_entitlement_latency.py`

| Parameter | Value |
|---|---|
| Dataset | 6 products × 8 features = 48 feature entitlements, 6 product entitlements |
| Iterations | 2 000 per scenario (1 000 for full-request, 500 for the fallback), 100 warm-up |
| Cache | Redis 7 via `django.core.cache.backends.redis.RedisCache` |
| Database | PostgreSQL 16 via PgBouncer (transaction mode), loopback |
| Python | 3.10.11 |

A single product with a single feature would have flattered the result by
making the snapshot JSON trivially small, so the dataset is sized to something
a real customer would hold.

**Baselines are measured alongside**, because reporting a budget miss without
them would suggest the fix lies in the entitlement code when it may lie
entirely in the topology.

## 4. Results

| Scenario | p50 | p95 | p99 | Queries | Note |
|---|---|---|---|---|---|
| **baseline:** `SELECT 1` through PgBouncer | 0.4784 ms | 0.5849 ms | 0.7864 ms | 1 | Floor for any path touching the database |
| **baseline:** `cache.get` through Redis | 0.4514 ms | 0.5603 ms | 0.6736 ms | 0 | Floor for any path touching the cache |
| Cold — no request scope, cache cleared | 3.3626 ms | 4.3428 ms | 5.4667 ms | 2 | Snapshot read from PostgreSQL every call |
| Shared cache — no request scope | 1.5862 ms | 2.3396 ms | 3.1433 ms | 1 | Version from DB, snapshot from cache |
| **Request-local — 2nd+ check in a request** | **0.0043 ms** | **0.0054 ms** | **0.0065 ms** | **0** | Dictionary lookup |
| `feature()` / `quota()` in request scope | 0.0015 ms | 0.0017 ms | 0.0017 ms | 0 | |
| **Full request — 6 checks** | **1.5590 ms** | **1.7938 ms** | **2.0765 ms** | **1** | Total for all six |
| Snapshot missing — live fallback | 6.2449 ms | 8.7239 ms | 9.3801 ms | — | Self-healing path |

### Reading the numbers

**Request-local caching is extraordinarily effective.** A repeat check costs
0.0043 ms — roughly **370× faster** than the 1.59 ms first check, and about
**110× under** the original 1 ms per-check budget. This is the layer Phase 0
asked to be evaluated first, and it is sufficient on its own.

**The first check is dominated by I/O, not by our code.** 1.59 ms p50 against
a floor of 0.48 (DB) + 0.45 (cache) = 0.93 ms means roughly **0.66 ms** is
everything else: the ORM, snapshot deserialisation, resolution and the
decision object. That is the part we control, and it is already well under
1 ms.

**Six checks cost barely more than one.** 1.56 ms for a full request versus
1.59 ms for a single cold check. Amortised, that is **0.26 ms per check** —
inside the original budget even when stated per check, provided the request
scope is active.

## 5. Revised budget

| | Original (per check) | Revised (per request) | Measured |
|---|---|---|---|
| p50 | 1 ms | **2 ms** | 1.56 ms |
| p95 | 5 ms | **5 ms** | 1.79 ms |
| p99 | 20 ms | **10 ms** | 2.08 ms |
| Queries per request | — | **≤ 2** | 1 |
| Marginal cost per extra check | — | **≤ 0.05 ms** | 0.0043 ms |

**Why per request rather than per check.** A per-check budget implies checks
are independent, and with request-local caching they are not: the first pays
for all of them. Budgeting per check either over-provisions (every check
priced as though cold) or is unachievable (a cold check cannot beat one
round trip). Per request prices what the user actually waits for.

The p99 target is *tightened* from 20 ms to 10 ms, because the measured p99
is 2.08 ms and a budget five times looser than reality alerts on nothing.

`ENTITLEMENT_P50_BUDGET_MS` and `ENTITLEMENT_P95_BUDGET_MS` remain in settings
as per-check figures for the cached path, which request-local caching meets by
a wide margin.

## 6. Query counts

| Path | Queries | What they are |
|---|---|---|
| Full request (6 checks) | **1** | `SELECT version FROM entitlements_snapshot` |
| Repeat check in a request | **0** | Dictionary lookup |
| Cold, cache cleared | 2 | Version + snapshot row |
| Fallback, no snapshot | 4 | Version + entitlements + features + resolution |

Pinned by a permanent test
(`test_repeat_checks_in_one_request_issue_no_further_queries`) asserting
**zero** queries for 75 consecutive cached checks. A check that met its
latency budget by issuing a query per call would have moved the cost somewhere
the budget does not look.

## 7. The one remaining database query

Version-keyed caching requires reading `EntitlementSnapshot.version` to build
the cache key. That read is the entire remaining per-request cost.

**An optimisation exists and was deliberately not taken.** Caching the version
under a short TTL, or keying the snapshot under a stable key and deleting it
on write, would remove the query and take the first check to roughly 0.45 ms.

It was rejected for Phase 1 for three reasons:

1. **The instruction is explicit** — do not introduce distributed-cache
   complexity to chase a benchmark. Request-local caching was evaluated first
   and is sufficient.
2. **It trades correctness risk for 1.1 ms.** Version-keying means a stale key
   is never read again; a stable key plus delete-on-write means a *failed*
   delete serves stale entitlements until the TTL expires. For a system where
   a wrong answer is revenue loss or free access, that is a poor trade.
3. **The measured cost is 1.56 ms against a 2 ms budget.** There is no
   pressure to spend correctness on it.

**Revisit trigger:** p95 for a full request exceeds 4 ms in production, or
`entitlement_cache_hit_ratio` drops below 0.95.

## 8. Failure behaviour, measured

| Failure | Behaviour | Covered by |
|---|---|---|
| Redis unavailable | Reads the snapshot from PostgreSQL. Slower, still correct; never default-allow | `test_check_is_correct_when_cache_is_unavailable` |
| Snapshot row missing | Computes live from `Entitlement` rows, then self-heals. 6.24 ms p50 — slow but correct | `test_missing_snapshot_self_heals_from_entitlement_rows` |
| Unrecognised status | **Denies**, `reason_code=unknown` | `test_unknown_status_denies_rather_than_guessing` |
| No entitlement row | **Denies**, `reason_code=not_entitled` | `test_organization_with_no_entitlements_is_denied` |

The fallback at 6.24 ms is worth stating: if a reconciliation run cleared
every snapshot, reads would be ~4× slower but would stay **correct**. That is
the right direction for this failure to point.

## 9. Limitations

1. **Loopback networking.** Production adds cross-AZ latency to both round
   trips; the ~0.66 ms of application work is unaffected, and the request-local
   path (0.004 ms) touches neither.
2. **One organization's data.** Snapshot size grows with products and features
   held; 48 features is a realistic mid-size customer, not an enterprise
   bundle.
3. **Single-threaded.** No contention for the connection pool or the cache.
4. **A development machine**, not production hardware.

Re-run against staging before first production traffic. The shape of the
result — first check costs one round trip, subsequent checks are free — is a
property of the design and will hold; the absolute numbers will not.

## 10. Conclusion

| | |
|---|---|
| **R-14 status** | **Benchmarked and recorded** |
| **Original per-check budget** | Not achievable — 0.93 ms of the 1 ms is I/O floor |
| **Revised per-request budget** | 2 ms p50 / 5 ms p95 / 10 ms p99, ≤ 2 queries |
| **Measured** | **1.56 ms p50 / 1.79 ms p95 / 2.08 ms p99, 1 query** |
| **Marginal check cost** | **0.0043 ms** |
| **Distributed cache complexity added** | **None** |

Raw results: `backend/tests/spikes/r14_results.json`.
