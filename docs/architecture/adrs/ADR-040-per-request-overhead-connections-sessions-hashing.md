# ADR-040: Per-request overhead: persistent pooled connections, a throttled session window, bounded password hashing

**Status:** Accepted (2026-10 architecture and performance audit, 2026-10-05)
**Revises:** the `CONN_MAX_AGE = 0` decision in [10-performance-concurrency.md](../10-performance-concurrency.md) §4, and `SESSION_SAVE_EVERY_REQUEST = True` (Phase 4 / Phase 8 extension).
**Related:** ADR-009 (RLS variable under pooling), ADR-033 (session registry), [docs/audit-2026-10/](../../audit-2026-10/architecture-performance-audit.md) (measurements).

## Context

The Phase 12 load test found the API CPU-bound at about 110 requests/s, with PgBouncer at 60–75 % of its single core as the next ceiling. Both were attributed to load. The 2026-10 audit profiled single requests inside the staging api container and found most of the cost was fixed overhead paid on every request, before any business logic:

| Per request | Cost (staging, measured) | Cause |
|---|---|---|
| New PgBouncer connection: TCP, DNS lookup, SCRAM login | ~6 ms wall, ~2.8 ms API CPU, plus PgBouncer's side of the handshake | `CONN_MAX_AGE = 0` |
| `UPDATE identity_session` and a commit, on reads and the public catalog too | ~3 ms, one pooler transaction, one WAL write | `SESSION_SAVE_EVERY_REQUEST = True` |

A third finding was not latency but a resource bound: a burst of sign-ins took the api container from ~280 MiB to 750 MiB and past 16 cores. Every request thread could run Django's Argon2 (100 MiB, 8 lanes) at once, and the production topology sets no container memory limit.

## Decision

1. **`CONN_MAX_AGE = 300` with `CONN_HEALTH_CHECKS = True`** (`W360_DB_CONN_MAX_AGE`). The reason recorded for `0` was that idle Django connections pin PgBouncer server connections. That holds for session pooling, not for the transaction pooling actually deployed, where a server connection is lent only for the length of a transaction. Reuse is safe because nothing sets session-level state: the RLS variable is `set_config(..., true)` only (B6), and prepared statements and server-side cursors are off. Production refuses an unbounded (`None`) or out-of-range value, and refuses persistence without health checks.
2. **The sliding idle window moves at most once per `SESSION_REFRESH_INTERVAL_S` (60 s)**, through `platform.identity.middleware.SlidingSessionMiddleware`, with `SESSION_SAVE_EVERY_REQUEST = False`. A modified session still saves at once. The interval can only end an idle session *earlier* (by at most 60 s), never later. Revocation, the epoch and the absolute lifetime are enforced when the row loads, as before; `last_activity_at` only feeds the "where am I signed in" list.
3. **`BoundedArgon2PasswordHasher`:** Django's Argon2 hasher with the same algorithm name and costs, so nothing is re-hashed, with at most `PASSWORD_HASH_CONCURRENCY` (2) hashes in flight per process.

## Consequences

- **Measured on staging** (same harness, data and host, before and after): throughput 82–87 → 148–169 requests/s at 50–500 virtual users; p95 at 50 users 753 → 239 ms; PgBouncer CPU ~65 % → ~23 %; api memory peak in sign-in bursts 750 → ~300 MiB. Full tables are in the audit record.
- **Connections:** client connections are now held between requests. Peak counts are unchanged (a busy container held them anyway) and are bounded by PgBouncer's `max_client_conn` (500). That is 12 per api container at 3 × 4. The server side stays bounded by the pool (`max_db_connections` 80).
- **Sessions:** a session's expiry and `last_activity_at` lag real activity by up to 60 s, and the browser's cookie is refreshed at most once a minute.
- **Sign-in under a burst:** 120 simultaneous sign-ins took the api container to a peak of 1,332–1,436 MiB without the bound and 857–871 MiB with it (kernel `memory.peak`, two runs each). Sign-in and concurrent reader latency did not change beyond run-to-run noise on the staging host. On a smaller host, where the hashes would compete for fewer cores, they would queue for a slot instead of thrashing. `PASSWORD_HASH_CONCURRENCY` is the knob; production accepts 1–16.
