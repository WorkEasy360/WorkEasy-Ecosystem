# Phase 6 — Status

**PHASE 6 STATUS: PASS** (implementation pass, 2026-09-23; adversarial
Opus audit **PASS**, 2026-09-24 - three P1 defects found and fixed, see
[09-opus-audit.md](09-opus-audit.md), which supersedes the counts and the
"Unresolved" table below).

Every gate below was run against real PostgreSQL 16, PgBouncer, Redis 7 and
RabbitMQ 3.13 (Docker), the Next.js production build, and a loopback HTTP(S)
receiver for real signed deliveries. Design is in
[00-overview.md](00-overview.md); this file is the evidence.

## 1. Final backend test run

One run from a freshly created test database, with no other test process
active:

| | |
|---|---|
| Command | `.venv/Scripts/python.exe -m pytest -q -rs -p no:cacheprovider backend` |
| **Passed** | **1,483** |
| Failed | 0 |
| Skipped | 0 |
| Duration | 1515.72 s (25 min 15 s) |

The Phase 5 baseline was 1,182. Phase 6 adds:

| Suite | Tests |
|---|---|
| Egress policy | 55 |
| Egress client | 13 |
| Conditions + schedules | 52 |
| Webhook connector | 23 |
| Acceptance (20 steps, real HTTP) | 1 |
| Security | 41 |
| Concurrency | 12 |
| Resilience | 20 |
| Performance | 13 |
| Architecture | 10 |
| Manifest, authz and FK inventory | the remainder |

## 2. Frontend

Gates were run in a clean copy of `frontend/`: the user's `next dev` server
held the SWC binary in the working tree, and `next build` would have
overwritten its `.next`.

| Gate | Result |
|---|---|
| `npm ci` | 440 packages, clean |
| `next typegen && tsc --noEmit` | clean |
| `eslint` | clean |
| `vitest run` | **47 passed** (41 before, +6), 6 files |
| `next build` | success; all 14 routes `ƒ` (dynamic) |
| `scripts/assert-dynamic-rendering.mjs` | "No route is statically prerendered; every page can carry a CSP nonce." |
| axe (component tests) | no violations, including the one-time secret panel |
| `npm audit` (all, and `--omit=dev`) | 0 vulnerabilities |
| Manual screen reader | **NOT EXECUTED** (none available) |
| Lighthouse | not run |

The working tree's `node_modules` was restored after an interrupted
`npm ci`. The lockfile is byte-identical to before.

## 3. Fresh database from zero

A new database, owned by the migrator, with every migration applied from an
empty schema in 5 s. Then, from `pg_catalog`:

- **All 9 new tenant tables:** RLS enabled **and forced**, one policy each,
  owned by `w360_migrator`.
- **`scheduling_due_work`:** no RLS by design (D-017); `w360_app` has
  SELECT, INSERT, UPDATE and DELETE only.
- **Tables with `organization_id` lacking RLS:** exactly the documented
  exemptions (`TENANT_SCOPE_EXEMPT`: outbox, dead letter, authorization
  code, pending authorization, creation request, user access,
  `scheduling_due_work`).
- **Foreign keys:** 94 in total, 23 composite. **0 non-deferrable.**
- **Freeze triggers** present on `connectors_secret`, `events_routed`,
  `automation_rule_version` and `webhooks_attempt`.
- `integration.*` permissions: 5 on `owner`, 5 on `admin`.
- `manage.py check`: no issues. `makemigrations --check`: no changes.

## 4. Phase 5 → Phase 6 upgrade

**Subject.** A `pg_dump` copy of the Phase 5 development database; the
original was not touched. It was:

1. completed to the final Phase 5 state (`identity.0002`);
2. restored to the exact Phase 5 role catalog;
3. seeded **as `w360_app` through the Phase 5 services** with 3 more
   organizations (owners assigned by onboarding), an admin member holding
   the system admin role, and one suspended organization;
4. given 4 **Phase-5-format cached permission sets** (without the new keys),
   planted in Redis to mimic a live system at deploy time.

**Result:**

| Check | Result |
|---|---|
| Migrate | 8 migrations, 4 s; re-run is a no-op |
| Phase 5 data | every table hash-compared before/after. Changed: `authz_permission` 38→43, `authz_role_permission` 76→86, `auth_permission`/`django_content_type` (new models), `django_migrations` (+8). **No Phase 5 row changed** |
| Catalog checks | identical to the fresh database (§3) |
| Smoke as `w360_app` | **11/11** (below) |

The smoke checks:
- all 3 pre-existing owners, and the pre-existing admin, can create a
  connection, rotate its secret, and create and activate a scheduled rule;
- the suspended organization is refused;
- org B cannot list or read org A's connection (404);
- with no tenant scope, the app role sees 0 rows in the new tables;
- one schedule timer is armed per activated rule.

The first rehearsal **found a defect**. System-role grants written at
deploy time never invalidated cached permission sets. Fixed with a
catalog fingerprint in the cache key; the stale planted sets above prove
it.

**Rollback:**

```
migrate tenancy 0004 → automation zero → webhooks zero → connectors zero
        → scheduling zero → events 0001 → authz 0003
```

Every step succeeded. No Phase 6 table was left behind, and Phase 5 data is
unchanged. Audit rows written by the smoke are kept, as audit is
append-only. Migrating forward again re-applied all 8 migrations with RLS
intact.

A code-only rollback is safe: Phase 5 code carries unknown permission keys
through harmlessly.

## 5. Acceptance (Task 98)

`test_phase6_acceptance.py` runs the brief's 20-step flow with
`transaction=True` and real commits. A registered product publishes a
organization's event over HTTP, authenticating with its Phase 3 credential. It is
routed to a subscribed webhook **and** an event-triggered rule, delivered
over real HTTP with a verified signature. A failing destination (scripted
500s) is retried with bounded backoff, dead-lettered, replayed and audited. Along the way:
- a second product publishing in the first's namespace is 403;
- an undeclared event is 422;
- organization B, subscribed to the same event type, receives nothing of
  organization A's.

## 6. Concurrency (Task 97): 12 tests, all pass

- **Delivery:** 8 workers race one delivery → 1 send.
- **Idempotency:** 1 event submitted 8× concurrently → 1 accepted, 7
  duplicates, 1 row.
- **Replay:** concurrent replays of 1 dead delivery → 1 replay.
- **Rotation:** concurrent rotations → exactly 1 active secret.
- **Activation:** racing activations → 1 winner.
- **Schedules:** 1 occurrence fired under contention → 1 execution.
- **Subscriptions:** concurrent identical subscriptions → 1 row.
- **Fencing:** a late acknowledgement cannot overwrite a newer attempt.
- **Suspension:** a suspension racing deliveries → nothing sent after it
  commits.
- **Retry ownership:** replay is refused while the automatic retry owns
  the delivery.

## 7. Failure injection (Tasks 67–72): 20 tests, all pass

| Injected | Result |
|---|---|
| PostgreSQL failure while recording an attempt | recovered as `abandoned` on the next claim; not lost |
| Worker killed after claiming / after sending, before recording | lease expiry → attempt recorded `abandoned`, retried; the receiver deduplicates on `event.id` (at-least-once) |
| RabbitMQ unreachable | work and timers committed. A **real** unreachable broker costs 1.0 s per enqueue (measured three times). Found: enqueue loops would have spent that per item (200 s per dispatch batch). **Fixed:** the first broker error ends the loop and the timers carry the rest; everything is delivered once the broker returns |
| Redis unreachable | rate limits degrade (per-process), ingestion and authorization unaffected; authorization never fails open |
| Receiver: timeout, refused, DNS failure, untrusted TLS, 429, 500, 3xx | retry (`read_timeout`) / retry (`connect_failed`) / retry (`dns_failure`) / retry (`tls_error`, bounded; never sent unverified) / retry with clamped `Retry-After` / retry / dead `redirect_refused` |
| Receiver down for good | growing equal-jitter backoff, then dead `retries_exhausted`; circuit opens and holds without spending attempts; auto-suspend after 50 |
| Backlog | bounded at 1,000 open per connection |
| Readiness | never gated by integration health (`/health/deep` only) |

The RabbitMQ container was not stopped, because the user's development
stack was using it. The outage was exercised at the enqueue boundary, and
real unreachable-broker latency was measured against a closed port.

## 8. Quality gates

| Gate | Result |
|---|---|
| ruff check / format | clean (366 files) |
| mypy | no issues |
| import-linter | **14 contracts kept**, 0 broken. Two new: `integration-internal-layers`, `connector-providers-are-sandboxed` |
| bandit (`-ll`) | no medium or high findings |
| pip-audit `--strict` | no known vulnerabilities |
| `manage.py check` | no issues |
| `check --deploy` | via `test_security_baseline.py` (passes, with the new required key) |
| `makemigrations --check` | no changes |

## 9. Performance

See [08-performance.md](08-performance.md): local measurements only, with
query counts asserted.

- Fan-out: 10 queries at 10, 100 or 500 subscriptions.
- Rule matching: 14 queries at 10, 100 or 500 rules.
- Ingestion: 14 queries, p50 20.5 ms.
- The three Settings lists: 17 / 19 / 17 queries at any page size.
- Hot plans are index-served on a padded table.

## 10. Defects found and fixed during the gates

| Severity | Defect | Fix |
|---|---|---|
| P1 (pre-existing) | Web processes never loaded the Celery app, so any `shared_task` enqueued from a request used Celery's default app and broker settings | `config/__init__.py` imports the app |
| P2 | Deploy-time system-role grants left pre-deploy cached permission sets readable for up to 5 min (owners saw 403) | catalog fingerprint in the permission cache key + regression test |
| P2 | During a broker outage, enqueue loops would hold a worker for 1 s per item (200 s per dispatch batch) | stop at the first broker error; timers carry the rest; 2 tests |
| P2 | A suspended organization's events were refused with a stale-snapshot 402 instead of `organization_inactive` | ingestion checks the live write status |
| P2 | Condition paths accepted `__dunder__` segments | refused at save |
| P2 | Rule activation armed its timer before its conditional update (D-016) | timer written after the update, inside the transaction |
| P3 | Egress read loop could touch a socket `http.client` had already closed | loop on `isclosed()` |
| Test gap | Log-secret test covered only create and rotate | now covers two real signed deliveries, both secrets, all three signatures and the URL token |
| Test gap | No test pinned that a `2026-09-01` manifest still loads | 2 manifest tests |
| Test | Backoff assertion timed from before a 2 s connect; plan assertion ran on a 600-row table | measured from the attempt; realistic padding |

## 11. Cleanup (lean-code review)

- **Retry arithmetic consolidated.** Three pre-Phase-6 copies (outbox
  relay, `TenantTask.backoff_delay`, notifications) now call
  `contracts/values/backoff.full_jitter`. Behaviour is unchanged.
- **Removed:**
  - the unused selector `open_execution_exists`;
  - three empty test packages;
  - the `socket` import in notifications.
- **No new dependencies**, frontend or backend.
- No product-specific condition, action, connector or event exists
  (`test_no_registered_action_or_connector_is_product_specific`).

## 12. Architecture deviations and ADRs

**ADRs:**
- ADR-024: the integration data model and due-work timer;
- ADR-025: the outbound HTTP / SSRF boundary;
- ADR-026: automation execution semantics.

**Debt register:**
- D-017: the cross-tenant timer table and intra-`integration` foreign keys;
- D-018: the application master key instead of KMS envelope encryption.

Phase 5 code changed for Phase 6:
- the permission cache key;
- the `backoff` consolidation;
- `StepUpForm` extracted from `MfaSettings`;
- `config/__init__.py`.

## 13. Unresolved

| Severity | Item |
|---|---|
| P1 | none |
| P2 | D-018: signing secrets under one application key, not KMS per-tenant data keys |
| P2 | Throughput unmeasured; no production-like load environment exists |
| P2 | No tenant purge workflow (pre-existing; Phase 6 rows cascade from the organization) |
| P3 | Manual screen-reader pass **NOT EXECUTED**; Lighthouse not run |
| P3 | Real RabbitMQ container stop not performed (see §7) |
| P3 | Webhook delivery latency excluding the remote was not measured separately (the per-attempt `duration_ms` is recorded) |
| P3 | `MfaSettings` focus test flakes under heavy CPU load (pre-existing; passes in isolation) |
| — | Adversarial Opus audit: done, PASS ([09-opus-audit.md](09-opus-audit.md)) |

## 14. Decisions proposed to freeze for Phase 7

1. Product events are `product.<authenticated code>.*`, declared in a
   `2026-10-01` manifest. The platform routes them opaquely and never
   interprets them.
2. The receiver contract:
   - the `workeasy360.webhook.v1` body;
   - the `X-W360-*` headers;
   - `X-W360-Signature: t=…,v1=…` (HMAC-SHA256 over `t.` + body), with
     several `v1` values during rotation;
   - at-least-once delivery, deduplicated on `event.id`.
3. Every outbound HTTP request goes through `integration/egress`. No other
   module opens a socket.
4. Pending work is a durable row plus a `DueWork` timer in the same
   transaction. The broker is only the fast path.
5. Automation actions create records through existing services and never
   perform side effects. A new action is a registered `ActionSpec`.
6. The 5 integration permission keys: `manage` is dangerous; replay is
   separate.
7. Permission caches are keyed by organization version **and** role-catalog
   fingerprint. A deploy that changes role grants needs no manual cache
   flush.
8. *(Phase 6 audit)* The egress overall deadline is enforced on the socket
   (watchdog), not only between reads; a received status line is final.
9. *(Phase 6 audit)* Delivery bulkheads: 4 in flight per connection, 8 per
   organization. A delivery refused a slot is parked and pulled forward by
   the next finishing attempt - never re-polled through the shared timer.
10. *(Phase 6 audit)* A queued delivery uses its connection's current URL and
    the secrets valid at attempt time ([03-webhooks.md](03-webhooks.md)).
11. *(Phase 6 audit)* A due-work timer's organization is fixed at creation;
    only its own organization can move or complete it.
12. *(Phase 6 audit)* Only `webhooks/pipeline.py` may import the egress client.
