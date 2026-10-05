# 2026-10 architecture and performance audit

**Date:** 2026-10-05. **Scope:** an architecture, system-design, performance and resilience audit of the existing platform. Nothing was rebuilt and no technology was replaced. Every change below was made only after a measurement located its cause; ADR-040 records the three backend decisions.

**Method.** Same environment and harness as Phase 12 ([07-load-test.md](../phase-12/07-load-test.md)):
- **Stack:** the production-shaped staging stack (`w360stg`: Caddy TLS edge, gunicorn 3 × 4, PgBouncer transaction pooling, PostgreSQL 16, Redis, RabbitMQ, three worker pools) with its 500 seeded load organizations.
- **BEFORE and AFTER:** BEFORE is release candidate p12-rc5 (the tree before this audit); AFTER is `p12-audit1`. Both were measured on the same day, on the same laptop, with the same k6 script and data.
- **Disclosed deviations:**
  - The staff VU was left out of both runs: its password could not be reset.
  - The two per-address limits that one k6 source address trips were raised for both runs, as in Phase 12.
  - The AFTER run sends the launch request's new `Idempotency-Key`, as the browser now does (§3).
- **Treat these numbers as a single-host comparison, not capacity.** Another project's containers ran on the same host throughout, and repeated runs differ by up to ±15 %.

## 1. The system as found

```
Browser ─▶ Caddy edge (TLS, on-demand certs, /api/* routed straight to Django)
             ├─▶ web: Next.js 16 (client-rendered app shell, per-request CSP nonce)
             └─▶ api: Django 5.2, gunicorn gthread 3 × 4
                    ├─▶ PgBouncer (transaction pooling, pool 40) ─▶ PostgreSQL 16 (RLS, FORCE)
                    ├─▶ Redis (cache, rate limits; per-process breaker + in-process fallback)
                    └─▶ RabbitMQ ─▶ Celery pools: critical | default | heavy  (+ Beat)
External: four products (OIDC clients-to-be), SMTP provider, payment providers, AI providers, S3
```

The layering is enforced, not aspirational: import-linter holds 18 contracts, AST fitness tests forbid cross-module ORM access, product branching and stray writers, and RLS is verified live by the startup gate.

## 2. Findings

### Architecture

| Sev | Finding | Evidence | Action |
|---|---|---|---|
| HIGH | **Fixed per-request overhead outweighed the business logic.** Every request opened a new database connection (`CONN_MAX_AGE = 0`) and wrote the session row (`SESSION_SAVE_EVERY_REQUEST`). | Profile of 300 requests: 25 % of time in `connect()`, 12 % in the session save | Fixed (ADR-040) |
| MEDIUM | **A design decision rested on a false premise.** `CONN_MAX_AGE = 0` was justified by "idle connections pin PgBouncer server connections", which is true of session pooling, not the transaction pooling deployed. | `pgbouncer.ini`, PgBouncer docs | Corrected in 10-performance-concurrency.md §4 |
| MEDIUM | **No bulkhead on password hashing.** Every request thread could run Argon2 (100 MiB, 8 lanes) at once; the topology sets no memory limit. | 120-sign-in burst: 1,332–1,436 MiB peak | Fixed (bounded hasher) |
| MEDIUM | **JSON request-body parsing was duplicated in 13 views** with inconsistent error handling, and none caught `RecursionError`. The product-ingestion parsers (Phases 6/8) did. | §2 Security | Fixed (one loader, fitness test) |
| LOW | Large modules: `interfaces/api/v1/urls.py` (1,366 lines), `commercial/subscriptions/services.py` (1,466), `payments/orders.py` (1,272); frontend control-plane screens of 900–1,000 lines. They are cohesive and well-tested; splitting them now is churn without a measured benefit. | file survey | Not changed |

No circular dependency, upward import or cross-module ORM access exists (lint-imports 18/18, architecture suite green). Runtime dependencies are lean: 12 Python packages, 3 npm (next, react, react-dom).

### Performance: root causes (measured, not assumed)

| Symptom (Phase 12 / BEFORE) | Measurement | Root cause | Fix |
|---|---|---|---|
| API CPU-bound at 82–87 req/s; ~24 ms API CPU per request in production against ~7–11 ms in-process | Connect + SCRAM + DNS: **~6 ms wall, 2.8 ms API CPU per request**; a statement on an open connection: 0.15 ms | `CONN_MAX_AGE = 0` behind PgBouncer | Persistent, bounded connections (300 s) with health checks |
| PgBouncer at 60–75 % of its single core: "the next ceiling" | ~7.3 transactions per request, a full SCRAM login per request | Same, plus one commit per request for the session | Same, plus the session refresh |
| Every read wrote | `UPDATE identity_session` + commit on every request, the public catalog included: **~3 ms** | `SESSION_SAVE_EVERY_REQUEST = True` | Refresh the idle window at most once per 60 s |
| Sign-in bursts: api memory 280 → 750 MiB (5 s samples), CPU > 16 cores | Exact kernel peak in a 120-sign-in burst: **1,332–1,436 MiB** | Unbounded concurrent Argon2 | `BoundedArgon2PasswordHasher` (2 per process) |
| 0.02–0.08 % of launches failed (502) under sustained load (LOAD-2) | 9 of 11,734 launch POSTs in the 10-minute soak | A POST sent down a keep-alive connection a recycling gunicorn worker had just closed; the edge's Go transport replays only idempotent requests | Launch sends `Idempotency-Key` (it writes nothing), so the transport replays it |
| Dashboard: a 3-deep request waterfall | session → access → page data; `/auth/session` fetched twice | The shell waited for the session before asking for access; the page re-fetched the session for a first name | Parallel session + access; name from the shell |

Not changed because the measurement did not justify it: the app directory's per-product `launch_target` read (3 extra queries at 4 products, ~1 ms; pinned by the slope test), PostgreSQL itself (≤ 60 % CPU, ≤ 4 active connections), and the frontend bundle (173 KB transferred, 13 files; no heavy library).

### Reliability and scaling: single points of failure (status after this audit)

| Component | Failure impact | Current protection | Recommended mitigation | Priority |
|---|---|---|---|---|
| One api container | Total outage; about 10 s of 502 per deploy | Health checks, stop grace, startup gate | Two or more instances behind the edge (OPS-1) | P2 |
| One PgBouncer | Total outage | `server_login_retry`, health checks; a reconnect now costs nothing visible (`CONN_HEALTH_CHECKS`) | A second pooler, or RDS Proxy on the AWS target | P2 |
| One PostgreSQL | Total outage | Backups, PITR proven; **not scheduled** (OPS-2) | Schedule plus off-host copy; Multi-AZ before GA | **P1** |
| Redis | Degraded, not down | Per-process breaker, DB fallbacks, in-process limiter | none needed now | — |
| RabbitMQ | Async deferred, sign-in and browsing work | Outbox, idempotent tasks (Phase 12: 14/14) | none needed now | — |
| One Beat | Sweeps stop | Every job is idempotent, so a duplicate is safe | Supervision and alerting (OPS-3) | P2 |
| External products | Launch shows unavailable | Health probes on `heavy`, egress timeouts | none needed now | — |

### Security (found by this audit's gate)

| Sev | Finding | Status |
|---|---|---|
| **CRITICAL** (dependency) | `next` 16.3.5: RCE in `next/og` ImageResponse (GHSA-vcvr-r3jv-pc5j, published after Phase 12). The app does not import `next/og`, but the shipped framework contained the vulnerable module. | **Fixed:** 16.3.8 |
| **HIGH** (dependency) | `PyJWT` 2.14.0 (CVE-2026-101918): a deeply nested payload escaped the unverified decode as `RecursionError`. The token endpoint's peek at `client_assertion` caught only `PyJWTError`, so one unauthenticated ~50 KB request was a 500 and could trigger the 5xx page alert. | **Fixed:** 2.15.1; the regression test fails on 2.14.0 |
| **MEDIUM** | Same class in the platform's own code: any JSON endpoint, unauthenticated sign-in included, answered a deeply nested body with a 500 (verified on staging rc5). | **Fixed:** `contracts.values.json_input.loads` at 19 external-input call sites, plus an AST test |
| HIGH (dev-only) | `braces` ≤ 3.0.3 (GHSA-vfj7-8cjw-p6xm), through `eslint-config-next` → `fast-glob` → `micromatch`. It is lint tooling and never shipped; exploiting it needs attacker-controlled glob patterns, which come from the repository's own config. **No fixed version exists.** | **Open:** CI's `npm audit --audit-level=moderate` fails until upstream ships a fix; production dependencies audit clean |

## 3. Fixes implemented

| Change | Where |
|---|---|
| `CONN_MAX_AGE` 0 → 300 (`W360_DB_CONN_MAX_AGE`); production refuses `None`, values outside 0–3600, and persistence without health checks | `config/settings/base.py`, `production.py` |
| `SlidingSessionMiddleware`: the idle window slides at most once per `SESSION_REFRESH_INTERVAL_S` (60), a modified session saves at once, production allows 0–300 | `platform/identity/middleware.py`, `sessions.py` |
| `BoundedArgon2PasswordHasher`: Django's Argon2 (same name, same costs), `PASSWORD_HASH_CONCURRENCY` (2) per process; production allows 1–16 | `platform/identity/hashers.py` |
| `json_input.loads`: too-deep JSON is a `JSONDecodeError`, used by every request-body parser, the cursors, analytics, and payment and AI provider parsing | `contracts/values/json_input.py` + 18 modules |
| `PyJWT` 2.14.0 → 2.15.1; `next` / `eslint-config-next` 16.3.5 → 16.3.8 | `requirements.txt`, `frontend/package*.json` |
| Shell: session and access in parallel; dashboard greeting from the shell's session | `features/launcher/Shell.tsx`, `console/OrgAccess.tsx`, `CommerceScreens.tsx` |
| Launch sends `Idempotency-Key` (replay-safe at the edge) | `lib/api/launcher.ts` |
| Load harness: a 90-second spike level, staff VU optional, launch mirrors the browser | `scripts/phase12_staging/load.js`, `run_load.sh` |
| **Regression protection:** a read writes nothing; the window still slides; a reused connection carries no tenant; hasher compatibility and the concurrency bound; nested JSON at sign-in, the password change, `/token` and `/userinfo`; no direct `json.loads` in `interfaces/`; new production-settings refusals; Shell parallelism and sign-out; no duplicate session fetch; the launch header | `tests/performance/test_request_overhead.py`, `tests/security/test_password_hash_bulkhead.py`, `test_nested_json.py`, `test_ecosystem_security.py`, `test_security_baseline.py`, `Shell.test.tsx`, `commerce.test.tsx`, `AppCard.test.tsx` |

## 4. Before and after

### Per request, in-process on staging (median of 40, connection closed per request as `request_finished` does)

| Endpoint | Wall before | Wall after | API CPU before → after | Statements | New DB connections | Transactions |
|---|---:|---:|---:|---:|---:|---:|
| session | 13.1 ms | **4.5 ms** | 6.7 → 3.3 ms | 5 → 4 | 1 → 0 | 5 → 4 |
| My Products | 31.7 ms | **19.4 ms** | 19.5 → 13.7 ms | 23 → 22 | 1 → 0 | 6 → 5 |
| public catalog | 14.2 ms | **3.5 ms** | 7.5 → 2.6 ms | 4 → 3 | 1 → 0 | 4 → 3 |
| subscription read | 18.3 ms | **7.1 ms** | 10.1 → 5.1 ms | 9 → 8 | 1 → 0 | 6 → 5 |
| launch | 20.2 ms | **8.5 ms** | 11.8 → 6.0 ms | 11 → 10 | 1 → 0 | 6 → 5 |

cProfile over the same 300-request customer loop: 8.08 s → 4.44 s. The new connection health check (`SELECT 1`, ~0.15 ms) is in the AFTER wall times; it bypasses Django's statement counter.

### Load (k6 over TLS through the edge; customers sign in once, then loop session, My Products, catalog, subscriptions and launch with 1 s think time)

| Level | RPS before → after | p50 | p95 | p99 | Failed |
|---|---|---|---|---|---|
| 50 VUs | 87 → **169** (+94 %) | 364 → 75 ms | 753 → 239 ms | 1,143 → 442 ms | 0 → 0 |
| 100 VUs | 82 → **160** (+95 %) | 974 → 388 ms | 2,185 → 889 ms | 2,684 → 1,543 ms | 0 → 0 |
| 250 VUs | 83 → **152** (+83 %) | 2,783 → 1,302 ms | 5,286 → 2,532 ms | 9,638 → 6,444 ms | 0 → 0 |
| 500 VUs | 84 → **159** (+89 %) | 4,805 → 2,271 ms | 10,938 → 5,400 ms | 13,387 → 15,638 ms | 0.02 % → 0 |
| spike 50 → 400 → 50 | 83 → **148** (+78 %) | 2,587 → 1,368 ms | 6,297 → 3,161 ms | 7,795 → 3,765 ms | 0 → 0 |
| soak 100 VUs, 10 min | 99 → **136** (+37 %) | 776 → 517 ms | 1,673 → 1,110 ms | 2,029 → 1,408 ms | **0.02 % → 0.00 %** |

**About 500 VUs and the soak.**
- **The 500-VU p99** is dominated by 500 simultaneous sign-ins and swings by run: two later back-to-back runs gave 20.0 s (bulkhead on) and 22.0 s (bulkhead off). It is not a reliable comparison; p50 and p95 improved in every run.
- **The soak's smaller gain** against the 90-second levels is host noise: the BEFORE soak was itself above its own level runs.

**Launch failures:** 9 of 11,734 before, 0 of 16,274 after (soak).

### Resources

| | Before | After |
|---|---|---|
| PgBouncer CPU (median) | 59–68 % of one core | **20–26 %**, at nearly twice the throughput |
| DB transactions per request | ~7.3 | **~5.3** |
| PostgreSQL connections (peak total / active) | 13–16 / 2–5 | 13–16 / 1–3 |
| api memory, 10-minute soak | 286 → 283 MiB (flat) | 295 → 287 MiB (flat) |
| api CPU | 230–280 % (saturated) | 230–350 % (saturated, more work done) |
| queue depth | 0 | 0 |

### Sign-in burst (120 simultaneous sign-ins plus 10 catalog readers; a fresh container per run; kernel `memory.peak`)

| | Peak memory | Login p95 | Catalog p95 during the burst |
|---|---|---|---|
| Bulkhead off (16) | 1,436 / 1,332 MiB | 5.5 / 3.2 s | 29 / 13 ms |
| Bulkhead on (2) | **871 / 857 MiB** | 4.3 / 4.3 s | 42 / 16 ms |

The bulkhead bounds memory by about 500 MiB, with latency unchanged beyond noise on this host.

### Frontend: org dashboard, headless Chrome, frontend-only A/B against the same backend

| | rc5 web | audit web |
|---|---|---|
| API calls / waterfall depth | 8 / 3 | **7 / 2** |
| Content ready, cold, 100 ms emulated RTT (two rounds) | 847 / 840 ms | **703 / 718 ms** (−15 to −17 %) |
| Content ready, warm, 100 ms emulated RTT | 533 / 538 ms | **408 / 451 ms** (−16 to −23 %) |
| Content ready on loopback (≈20 ms RTT) | 247–258 ms | 256–311 ms: no measurable difference |
| JS transferred (cold) | 173 KB, 13 files | 173 KB, 13 files |

## 5. Security regression

Nothing was weakened, and no new header trust, wildcard or bypass was added:
- **RLS:** the RLS variable rules are unchanged, and a new test proves a reused connection carries no tenant between requests.
- **Sessions:** revocation, epoch, absolute lifetime and the org session-age policy are unchanged; the idle window can only end earlier, by at most 60 s.
- **Passwords:** hash costs are unchanged.
- **CSRF and CSP:** unchanged.

Gates on the final tree:

| Gate | Result |
|---|---|
| Backend suite (`pytest`, both testpaths) | **3,326 tests pass**: Phase 12's 3,306 plus 20 new. Run as `backend/tests` (2,597 passed, 2 failed) then `backend/workeasy360` + the two repaired files (744 passed). Both failures were timing flakes in the tests, not the product: a rate-limit loop that straddled a fixed-window boundary, and a 10 ms margin under Windows' 15.6 ms clock tick. Each test was made boundary-proof without weakening its assertion. |
| Frontend (clean `npm ci`, CI order) | lint 0, typecheck 0, vitest **390/390**, `next build` OK, assert-dynamic-rendering OK |
| ruff / format / mypy (`contracts/`, strict) / lint-imports | pass / 664 files / 30 files clean / 18 kept, 0 broken |
| bandit -ll / semgrep (Phase 12 packs) / gitleaks (tree) | 0 / 0 / no leaks |
| pip-audit --strict | clean after PyJWT 2.15.1 |
| npm audit | production dependencies: 0. All dependencies: 5 high, dev-only `braces` (§2, open) |
| `check --deploy` / `makemigrations --check` | only the known W360.W010 `sslip.io` warnings / no changes |
| Docker images | backend and frontend build (`p12-audit2`); deployed to staging, startup gate passed; nested-JSON sign-in 401 and `/token` 401 (were 500); k6 probe 1,459 requests, 0 failed |

## 6. Remaining risks

- **API compute is still the ceiling** (3 gunicorn processes, GIL-bound). Raise `GUNICORN_WORKERS` to about 2 per core and add instances (LOAD-1, OPS-1). PgBouncer is no longer the next limit at this load.
- **Dev-only `braces` advisory**, with no upstream fix (§2). The CI audit step stays red until one is published, or until the owner accepts a dev-only exception.
- **Connections:** client connections are now held between requests. Size PgBouncer's `max_client_conn` (500) against instances × workers × threads + Celery processes (23 per stack today).
- **Unchanged from Phase 12** (outside the code): OPS-2 backups unscheduled, OPS-3 no production monitoring, the product-side blockers (X-01…X-05).

## 7. Reproduce

The scripts are in `scripts/phase12_staging/` (`run_load.sh` with `LEVELS="50 100 250 500 spike"`, then a soak with `LEVELS=100 DURATION=10m`).
