# Phase 1 Sprint 0 — Verification Spikes

**Status:** All three complete
**Date:** 2026-09-21

---

Phase 0 made three decisions it could not prove without code, and made their
verification an entry condition for Phase 1
([PHASE-0-STATUS.md](../architecture/PHASE-0-STATUS.md) §5). None of them
changed the architecture; each proved a mechanism it depends on.

| Spike | Risk | Question | Verdict |
|---|---|---|---|
| [R-01 — RLS under PgBouncer](rls-verification.md) | P0 | Does `set_config(…, is_local => true)` stay confined to its transaction under transaction pooling? | **PASS.** 960 interleaved transactions, 2 shared server connections, **0 leaks**. Fallback not needed. |
| [R-09 — Broker capability](broker-verification.md) | P1 | SQS or AmazonMQ, given ordering, priority and visibility-timeout needs? | **PASS.** AmazonMQ (RabbitMQ) chosen on operability. Correctness depends on neither. |
| [R-14 — Entitlement latency](entitlement-benchmark.md) | P1 | Does the check path meet 1 ms p50 / 5 ms p95 on realistic data? | **Measured. Budget revised in writing.** Per-check target unachievable (I/O floor). Per request: **1.56 ms p50 / 1.79 ms p95, 1 query.** |

Each spike is a runnable script under `backend/tests/spikes/`, re-runnable
with `make spikes`, and executed in CI so that a regression is caught rather
than rediscovered.

---

## R-01 — RLS under PgBouncer transaction pooling

**The single most dangerous operational detail in the design.** A leak here
is a silent cross-tenant read with no error anywhere.

Measured against a real PgBouncer in transaction mode with `default_pool_size
= 2` and 24 concurrent workers, so that server-connection reuse is guaranteed
rather than incidental.

**Result: 960 transactions, 0 leaks, 0 driver errors.** PgBouncer confirmed
2 server connections serving 24 clients, so the sharing the test depends on
genuinely occurred.

Also verified: fail-closed with no variable set (0 rows, never all rows),
fail-closed with an empty-string variable, `WITH CHECK` blocking cross-tenant
INSERT, no survival across `COMMIT` or `ROLLBACK`, and correct restoration
after a savepoint rollback.

**Second finding.** The first run failed — not on leakage, but on
`DuplicatePreparedStatement`, with only 190 of 960 transactions completing.
psycopg3 auto-prepares statements, and under transaction pooling two clients
collide on the same statement name. Phase 0 listed this as "Broken" with a
documented mitigation; it is now *measured*, and the spike keeps a permanent
check proving the mitigation is both necessary and sufficient:

| Driver configuration | Transactions completed | Leaks |
|---|---|---|
| `prepare_threshold=None` (ours) | 960 / 960 | 0 |
| Auto-preparation enabled | 63 / 960 | 0 |

Leakage stayed at zero even while the driver was failing. The isolation
guarantee does not depend on the driver behaving.

**Outcome:** transaction pooling retained. `W360_DB_POOL_MODE=transaction`.
The session-pooling fallback is not required.

---

## R-09 — Broker capability

Measured against RabbitMQ 3.13, the engine behind AmazonMQ. SQS is a hosted
service and could not be executed; its rows are read from the kombu
transport's declared capabilities or from documentation, and **every such row
is labelled** so measured and documented are never confused.

All sixteen checks passed: three queue groups configured, six of six tasks
carrying explicit routing, priority honoured (`low, high, mid` published →
`high, mid, low` consumed), unacked messages redelivered, acked messages not,
dead-letter routing working, duplicate delivery absorbed by the
`ProcessedEvent` constraint, retries bounded at 5 with full jitter (200
distinct values in 200 samples), and the visibility timeout (900 s) exceeding
the longest task time limit (600 s).

**Decision: AmazonMQ (RabbitMQ).** This is an operability choice, not a
correctness one — consumers are idempotent and order-tolerant by construction,
so either broker would be correct. RabbitMQ wins on two rows: broker-side
priority (SQS has none) and the Celery observability toolchain (`celery
inspect` and worker events do not work on SQS).

FIFO order was observed and is **recorded as an observation, not relied
upon.** Nothing in the design depends on it.

---

## R-14 — Entitlement read-path latency

**The honest answer is a revised budget, not a passing grade against the
original one.**

Phase 0 set 1 ms p50 / 5 ms p95 **per check**. Measurement shows that is not
achievable on this topology for any path that touches storage: one database
round trip costs 0.48 ms p50 and one cache round trip 0.45 ms, so 0.93 ms of
the 1 ms is I/O floor before any entitlement logic runs.

What request-local caching achieves instead:

| Scenario | p50 | p95 | Queries |
|---|---|---|---|
| Baseline `SELECT 1` through PgBouncer | 0.478 ms | 0.585 ms | 1 |
| First check in a request | 1.586 ms | 2.340 ms | 1 |
| **Repeat check in a request** | **0.0043 ms** | **0.0054 ms** | **0** |
| **Full request (6 checks)** | **1.559 ms** | **1.794 ms** | **1** |

A repeat check is ~370× faster than the first and ~110× under the original
per-check budget. Six checks cost barely more than one, because only the
first can be cold.

**Budget revised to be stated per request:** 2 ms p50 / 5 ms p95 / 10 ms p99,
≤ 2 queries. Measured: 1.56 / 1.79 / 2.08 ms, 1 query. The p99 target is
*tightened* from 20 ms, because a budget five times looser than reality
alerts on nothing.

**No distributed-cache complexity was added.** An optimisation exists that
would remove the final query and take the first check to ~0.45 ms; it was
deliberately not taken, for reasons recorded in
[entitlement-benchmark.md](entitlement-benchmark.md) §7, with a revisit
trigger.

---

## What the spikes changed

| Finding | Where it landed |
|---|---|
| `prepare_threshold=None` is correctness-critical | `config/settings/base.py`, with a comment; regression-tested by the spike |
| `unscoped_access()` relaxes Layer 2 only — RLS still applies | `platform/tenancy/db.py::organization_db_scope`, used by provisioning and fixtures |
| Empty-string variable must not raise | `NULLIF(current_setting(…), '')::uuid` in the RLS policy |
| Entitlement budget is per request, not per check | This document and `entitlement-benchmark.md`; settings comment updated |
| Broker decided | `W360_BROKER_URL` defaults to `amqp://`; CI runs RabbitMQ |

## Residual risk

All three spikes ran on a single developer machine with loopback networking.
The *shapes* of the results are properties of the design and will hold
elsewhere; the absolute latencies will not. Re-run against staging before
first production traffic — `make spikes` is the whole procedure.
