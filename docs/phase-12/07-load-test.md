# Load test (Phase 12, Steps 25–27)

## 1. What was measured, and what it is not

**Setup.**
- **Stack:** the production-shaped staging stack on the final release candidate (rc5): `deploy/compose/production.yml` + the staging overlay.
  - API: one container, gunicorn gthread, **3 workers × 4 threads**.
  - PgBouncer (transaction pooling, pool 40), PostgreSQL 16, Redis, RabbitMQ, three worker pools, the Caddy TLS edge.
- **Load generator:** k6 in a container on the same Docker network, over HTTPS through the edge, verifying the staging CA.
- **Host:** everything, k6 included, ran on **one laptop**: Docker Desktop, WSL2 VM with 24 vCPUs and 7.6 GiB of memory, i7-13700HX.

**Read the numbers as a single-host lower bound and a map of where the platform saturates first, not as production capacity.** The load generator competes with the system under test for the same CPUs.

**Flows** (`scripts/phase12_staging/load.js`, non-destructive; the brief: never hammer purchase):
- **Each customer virtual user (VU)** signs in once as its own seeded account (500 organizations, each with an Easy CRM trial and a seat; `seed_load.py`), then loops with 1 s think time over:
  - session;
  - My Products;
  - the public catalog;
  - its subscription list;
  - a product launch (entitlement and seat verified, JSON mode).
- **2 partner VUs** read their provisioned customer list (60 customers).
- **1 staff VU** signs in, verifies a TOTP code (D-037) and reads the cross-tenant customer directory.

**Levels:** 50, 100, 250 and 500 concurrent customer VUs, 90 s each (`run_load.sh`). During each level the driver samples every 5 s: container CPU and memory (`docker stats`), PostgreSQL connections, and queue depth (`/healthz/metrics`); it also counts committed transactions.

**One deliberate deviation.** Every VU shares k6's single source address. Two per-address limits that a real population never trips from one IP were raised for the test only, then restored:
- `login_ip`: 30/min;
- `public_catalog_ip`: 120/min.

Per-account and per-user limits were unchanged.

**An invalid first run, disclosed.** The first run reported 59 % failures, but they were the harness: k6 empties each VU's cookie jar every iteration unless `noCookiesReset` is set, so every VU signed itself out after its first pass. Fixed in `load.js` and validated with a 5-VU probe (0 % failures) before the runs below. The numbers from the invalid run are not used anywhere.

## 2. Results

Final configuration (rc5 with the edge keep-alive change). A first valid run on rc5 without that change gave the same picture: 100–108 requests/s, p95 645 ms / 1.2 s / 3.6 s / 9.4 s.

| Customer VUs | Requests | Requests/s | Failed | p50 | p95 | p99 | max | DB commits/s |
|---|---|---|---|---|---|---|---|---|
| 50 | 10,967 | **119** | 0.02 % (2) | 186 ms | 535 ms | 754 ms | 2.0 s | 884 |
| 100 | 10,418 | **112** | 0.00 % | 716 ms | 1.26 s | 1.93 s | 3.2 s | 822 |
| 250 | 10,270 | **104** | 0.00 % | 2.18 s | 3.96 s | 6.29 s | 8.5 s | 795 |
| 500 | 11,334 | **107** | 0.02 % (2) | 4.86 s | 8.17 s | 13.1 s | 15.8 s | 765 |

**Per endpoint** (p50 / p95 ms):

| Endpoint | 50 | 100 | 250 | 500 |
|---|---|---|---|---|
| sign-in | 884 / 1,779 | 2,202 / 2,987 | 3,952 / 7,896 | 7,783 / 14,600 |
| session | 148 / 516 | 671 / 1,337 | 2,052 / 4,140 | 4,686 / 9,745 |
| My Products | 244 / 598 | 796 / 1,350 | 2,344 / 4,021 | 5,076 / 7,766 |
| public catalog | 156 / 482 | 700 / 1,185 | 2,139 / 3,785 | 4,904 / 6,596 |
| subscription list | 181 / 500 | 705 / 1,169 | 2,120 / 3,768 | 4,654 / 6,318 |
| launch (entitlement + seat) | 185 / 506 | 702 / 1,161 | 2,126 / 3,805 | 4,308 / 6,041 |
| partner customer list | 186 / 504 | 759 / 1,333 | 2,279 / 3,773 | 4,911 / 8,286 |
| staff customer directory | 173 / 560 | 664 / 1,141 | 2,199 / 3,973 | 2,786 / 7,247 |

Every check passed apart from the 4 launches in the "Failed" column: 912 sign-ins, 8,129 My Products, 4 staff step-ups, the partner and staff lists.

**Resources** (CPU median / peak across the level; the first `docker stats` sample of each level is dropped, since it is an artefact):

| Container | 50 VUs | 500 VUs | Memory (peak) |
|---|---|---|---|
| **api** | **248 % / 276 %** | **254 % / 270 %** | 271–302 MiB |
| PgBouncer | 60 % / 70 % | 68 % / 74 % | 7 MiB |
| PostgreSQL | 37 % / 45 % | 42 % / 50 % | 108–127 MiB |
| edge (Caddy) | 13 % / 16 % | 15 % / 19 % | 33–128 MiB (grows with open connections) |
| Redis | 2 % | 2 % | 10–11 MiB |
| RabbitMQ | ~1 % (spikes) | ~1 % (spikes) | 172–240 MiB |
| workers, Beat, web | ~0 % | ~0 % | 53–283 MiB each, flat |

- **PostgreSQL connections:** at most 15 open and 6 active at once.
- **Queue depth:** 0 on every queue, at every level. These flows are synchronous reads; asynchronous work was load-bearing in the outage tests (phase-12-status items 18–20).
- **Memory:** flat through every level, with no growth from level to level (G-34, the unbounded metric list, would have shown here).

## 3. Bottlenecks, in the order they bind

1. **API compute: the ceiling today.** Throughput is flat at **~105–120 requests/s** from 50 to 500 VUs, and latency grows in proportion to concurrency. That is a fixed capacity being queued for, not a component degrading. The api container runs at 250–285 % CPU, which is its **3 gunicorn processes**, each bound to one core by the GIL, while the host had 24 vCPUs mostly idle.
   - **Remedy:** more processes per instance (`GUNICORN_WORKERS`, about 2 per core) and more instances. More instances are also what zero-downtime deploys need (OPS-1).
2. **PgBouncer: the next ceiling.** It is single-threaded and uses 60–75 % of one core at ~800 transactions/s. The platform issues about **7 database transactions per HTTP request**: autocommit statements, each its own transaction through the pooler. So PgBouncer would saturate at roughly 1,100–1,200 transactions/s, only about 40–50 % above today's API ceiling.
   - **Remedies:** run PgBouncer as several processes (`so_reuseport`) or use a managed pooler (RDS Proxy on the AWS target); and fewer transactions per request (group a request's read statements).
3. **Sign-in cost.** Argon2 verification is deliberately CPU-expensive, and on a saturated api it queues behind everything else: sign-in p95 was 1.8 s at 50 VUs and 14.6 s when 500 VUs signed in at once. A real population does not sign in simultaneously; the per-address and per-account limits bound an attacker.
4. **PostgreSQL: not a bottleneck at this size.** It ran at ~40 % CPU, with at most 6 active connections.
   - It scans small tables sequentially, which is correct at 4–600 rows.
   - **At scale (P3):** the staff customer directory aggregates the whole customer summary table per request. That is fine at 600 organizations; cache it or keep counters before about 100,000.
5. **Rare 502 on launch (P3).** 6 of about 43,000 requests over the two runs (0.014 %), all `POST …/launch`, each at the moment a gunicorn worker was recycled after `max_requests`. The exiting worker closes a pooled connection that the edge then uses. Caddy's transport retries this transparently for idempotent requests, but not for a plain POST.
   - Setting the edge's upstream keep-alive below the upstreams' 5 s idle timeout closed a related race but not this one.
   - **Remedies:** send an `Idempotency-Key` header on launch, which lets the transport retry safely, or recycle less often (memory stayed flat).

## 4. What this means for a controlled go-live

A single api instance with 3 workers serves about 110 requests/s of this mix, with p95 under 0.6 s at 50 VUs that each issue about 2–3 requests per second. A person browsing issues a request every few seconds, so this is on the order of hundreds of concurrently active users, which is proportionate for a controlled launch with a small number of customers. For general availability:
- raise the worker count and run two or more instances;
- lift the PgBouncer ceiling;
- re-run this test on production-sized hardware and data, with k6 on its own machine.

**Reproduce:**

```
W360_STG_COMPOSE=<compose.sh> W360_ENV_FILE=<staging.env> OUT=<dir> LOAD_PASSWORD=... \
  STAFF_PASSWORD=... STAFF_TOTP=... bash scripts/phase12_staging/run_load.sh
```
