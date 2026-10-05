# Phase 6 — Adversarial (Opus) Audit

**Date:** 2026-09-24. **Scope:** the Phase 6 integration, event-routing,
webhook and automation substrate as implemented (Phase 7 not started).
**Verdict: PASS** — three P1 defects found, reproduced, fixed and pinned by
regression tests; no P0; no open P1. The open P2/P3 items are listed in §4.

Evidence was produced against real PostgreSQL 16, PgBouncer (transaction
mode), Redis 7 and RabbitMQ 3.13 in Docker, real loopback HTTP/HTTPS/TCP
peers, a real Celery worker process, and the Next.js production build.

## 1. P0

None.

## 2. P1 — found, reproduced, fixed

### P1-1 The egress "overall deadline" was not enforced

`egress.client` set a socket timeout per read and checked the 15 s budget
only *between* `response.read()` calls. A socket timeout bounds one `recv`;
`http.client` and OpenSSL loop over as many as the peer makes them. A
customer-chosen endpoint that trickled one byte inside each read timeout
held the request indefinitely.

- **Reproduced** with a 3 s budget: a trickled status line/headers held the
  call **50.8 s**, a trickled body and an endless `100 Continue` stream
  **101.6 s** (until the peer stopped). In production the only bound was the
  delivery task's 60 s soft time limit — and only under the prefork pool.
- **Fix:** a per-request watchdog (`_Deadline`) shuts the socket down at the
  deadline, covering the TLS handshake (now performed after the TLS socket
  is registered, `do_handshake_on_connect=False`), status line, headers,
  interim responses and body. It is finished before the socket is closed,
  so it can never act on a reused descriptor. Once the status line has
  arrived it is the receiver's answer: a slow or endless body is truncated,
  not turned into a retry of an accepted delivery (which would be an
  avoidable duplicate).
- **After:** every trickle mode ends at 3.0–3.1 s.
- **Tests:** `test_a_trickling_endpoint_is_cut_off_at_the_overall_deadline`
  (headers, interim responses, status line),
  `test_a_trickled_tls_handshake_is_cut_off_at_the_overall_deadline`,
  `test_a_received_status_survives_a_body_that_never_finishes`
  (trickled and endless body).

### P1-2 No per-organization delivery bulkhead

The only concurrency cap was 4 in-flight attempts per connection. An
organization may have 50 connections, and a test ping works even on a draft
connection, so one organization's administrator could hold **200** delivery
workers busy with slow endpoints — every worker on `heavy`, which all
organizations share and on which entitlement drift repair
(`commercial.provisioning.reconcile_all`) also runs. The egress docstring
claimed a per-organization bound that did not exist.

- **Fix:** `WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION` (default 8), checked at
  claim time under a transaction-scoped advisory lock keyed on the
  organization, so concurrent claims for different connections cannot both
  read a count below the cap.
- **Test:** `test_one_organization_cannot_hold_more_than_its_share_of_delivery_workers`
  — 8 real threads, 8 connections, cap 2: peak concurrency at the receiver
  is ≤ 2 (it was 8), and all 8 are still delivered.

### P1-3 Throttled deliveries re-polled the shared timer every 2–6 s

A delivery refused a slot was re-timed 2–6 s ahead, indefinitely. The
due-work sweeper that carries **every** organization's retries, schedule
firings, execution retries and broker-outage recovery claims 200 timers per
5 s tick (~40/s). One slow endpoint with a 1,000-delivery backlog therefore
re-entered that sweeper ~250 times a second: the sweeper saturated and
every other organization's timers lagged by the backlog's cycle time
(~25 s per saturated connection, additive across the fleet).

- **Fix:** a throttled delivery is **parked** (`WEBHOOK_THROTTLE_HOLD_S` to
  twice that, default 300–600 s), and every finishing attempt **pulls
  forward** the organization's least recently touched due delivery on a
  connection with room (timer due now, enqueued after commit; `SKIP LOCKED`
  plus an `updated_at` touch so concurrent pulls pick different rows). A
  backlog drains at full speed with no polling; the parked timer is only
  the fallback, so churn per backlogged connection drops from ~250/s to
  ~2/s. Round-robin by last touch keeps one busy endpoint from keeping an
  organization's slots.
- **Test:** `test_a_throttled_delivery_is_parked_then_pulled_forward_when_a_slot_frees`,
  and the P1-2 test above (all parked deliveries delivered by pulls).

## 3. P2/P3 fixed in this audit

| Sev | Finding | Fix | Test |
|---|---|---|---|
| P2 | Condition evaluation raised `OverflowError` on a JSON integer too large for a float (a 400-digit int parses as `int` and fits 64 KiB): every matching execution retried, then died | `_is_number`: ints are finite; only floats are checked | `test_an_integer_too_large_for_a_float_is_compared_not_crashed_on` |
| P2 | A double-clicked **Rotate secret** issued two rotations; the second ends the first one's overlap, so the secret the receiver still used stopped signing at once (deliveries then 401 → dead) | button disabled while in flight + synchronous ref guard | `sends one rotation for a double click` (fails on the old code: 2 POSTs) |
| P2 | "Actions never perform network I/O" was not structural: the layering contract lets `automation` import `egress`, and the socket-library test would not see a call to `egress.client.send` | architecture test: only `webhooks/pipeline.py` may import the client module or `send` | `test_only_the_delivery_pipeline_can_send` |
| P2 | Queued-delivery semantics after a URL change, rotation or unsubscribe were undocumented | decided and documented ([03-webhooks.md](03-webhooks.md) "What a queued delivery uses") | — |
| P3 | D-017: a timer's `organization_id` was overwritten on re-schedule, and `complete()` ignored the organization in scope (not exploitable today — every target id is a server-generated id of a row in the same organization) | upsert refuses a conflicting organization; `complete()` deletes only the in-scope organization's timer | `test_a_timer_keeps_its_organization_and_only_its_organization_completes_it` |
| P3 | Namespace, condition-path and schedule-time regexes used `$`, which matches before a trailing newline | `\Z` | `test_a_product_owns_exactly_its_own_namespace`, `test_a_filter_with_a_trailing_newline_is_not_a_filter`, `test_a_trailing_newline_does_not_pass_as_a_path_segment` |
| P3 | A non-string `op` in a condition raised `TypeError` (500) instead of a 400 | type check | `test_an_operator_that_is_not_a_string_is_a_validation_error` |
| P3 (test) | `test_the_entitlement_endpoint_is_limited` (Phase 3) failed once in the full run: the limiter's window is `int(time.time()) // 60`, and requests straddling a minute boundary reset the count. The repository's `frozen_throttle_window` fixture exists for exactly this; the module did not use it | module-wide `usefixtures("frozen_throttle_window")` (the cache is cleared around every test, so no count carries over) | the module's 11 tests |

## 4. Open items (not Phase-7-blocking)

| Sev | Item |
|---|---|
| P2 | **D-018** kept at P2: one application master key seals every organization's signing secrets. Mitigations verified: dedicated key (production refuses to start without it, ≥ 32 chars, distinct from other keys), organization/connection/version binding checked on unseal (`test_phase6_security.py`, ciphertext swap refused), immutable versions by trigger, `MultiFernet` master-key rotation, never logged or exposed (health, diagnostics, settings dumps checked). Key loss is explicit: `SecretUnavailable` → the delivery dies `unsendable`, never sent unsigned; recovery is rotating each connection's secret — lost secrets are not recoverable and nothing promises otherwise. Blast radius is "every signing secret" but not "any customer data at rest"; acceptable until a KMS exists. |
| P2 | `heavy` is shared by webhook delivery, entitlement drift repair and pruning. P1-2 bounds one organization; a fleet-wide surge of slow endpoints can still delay drift repair. Recommend routing `integration.webhooks.deliver` to a dedicated queue at deployment (a route override, no code change). |
| P2 | The egress DNS pool is 4 threads per process and `getaddrinfo` cannot be cancelled: a hostile authoritative server that never answers can occupy them for the system resolver's timeout, and other deliveries in that process then fail `dns_timeout` (retryable) until it passes. Bounded by the resolver timeout and the per-organization cap. |
| P2 | Due-work sweeper capacity is 200 timers per 5 s tick (~40/s). Watch `integration_due_work_lag_seconds`; raise `W360_SCHEDULING_CLAIM_BATCH` or the beat interval before it saturates. |
| P2 | Production throughput not load-tested (no environment); tenant purge workflow missing (pre-existing; Phase 6 rows cascade from the organization; orphan timers are dropped by the sweeper when the organization is gone). |
| P3 | Stored response excerpt (≤ 200 printable chars of a non-2xx reply) is readable with `platform.connection.view`; a receiver that echoes the request could expose event data to view-only members. |
| P3 | Default dataclass `repr` of `OutboundRequest`/`RuntimeConnection` would print the full URL and signature if ever logged (nothing logs them); log redaction is key-based only. |
| P3 | Connection count, backlog limit and per-rule hourly cap are count-then-insert: concurrent creators can overshoot by the number of concurrent routers. Soft bounds, documented as such. |
| P3 | Changing a connection's URL does not close an open circuit (suspend → activate does). `Retry-After` with non-ASCII digits becomes an `internal_error` retry. Missed-run count is capped at 1,000 without a flag. |
| P3 | Manual screen-reader pass **NOT EXECUTED** (none available). Pre-existing `MfaSettings` focus-test flake under load. |

## 5. Verification by area

**Outbound boundary (1, 5, 9).** The only socket/TLS/HTTP-client imports in
runtime code are `integration/egress` and the operator-configured SMTP relay
in notifications. Both architecture guards were proven non-vacuous by
planting a forbidden import (connector-sandbox contract BROKEN;
confinement test FAILED) and removing it. `http.client` reads no proxy
environment variables; one fresh connection per request, no pool.

**SSRF / rebinding (2, 3).** Static policy: https only in production, ports
443/8443, no userinfo, no internal names; every *resolved* address is
checked (loopback, RFC 1918, ULA, link-local incl. 169.254.169.254, CGNAT,
multicast, reserved, unspecified, documentation; IPv4 embedded in mapped,
6to4 and NAT64 addresses; Teredo refused). Non-canonical IPv4 spellings
(`127.1`, `0177.0.0.1`) pass the static check as "hostnames" but resolve to
loopback and are refused at send time — send time is authoritative. One
lookup per attempt, every address checked (one private record refuses the
host), connection to the validated literal, re-resolved per retry.

**TLS / SNI / Host (4).** New tests with a *trusted* private CA and the host
pinned to 127.0.0.1: the handshake sends SNI `hooks.example.com` and the
request `Host: hooks.example.com:<port>`; a valid certificate for another
host, a certificate valid only for the pinned IP, and an expired
certificate are all refused `tls_error`. Verification is never disabled.

**Redirects, limits (6, 7).** 3xx returned, never followed (dead
`redirect_refused`). No `Accept-Encoding` is sent and nothing is
decompressed; at most 64 KiB is read; headers bounded by `http.client`.

**Signature, rotation, secrets (10–12, 48–51).** `t.` + raw body,
HMAC-SHA256, lowercase hex; the reference receiver verifies before parsing,
in constant time, accepts any of several `v1`, rejects a missing or
duplicate `t` and a skew over 300 s. See §3 for the rotation defect and the
documented one-generation overlap.

**Delivery concurrency (13–18).** Claim/fence/abandon semantics as
documented; concurrency suite (8 workers → 1 send, late-ack fenced, replay
only of dead deliveries and never mutating the original). Retry
classification: 2xx success; 408/425/429/5xx retry with `Retry-After`
clamped to 1 h (past date → 0; negative/invalid ignored); 3xx and all other
4xx (400/401/403/404/409) dead.

**Circuit breaker (19, 20).** Serialised on the connection row; half-open =
one in flight; consecutive attempt results, reset by any success or by
reactivation; auto-suspend at 50 is recoverable by an administrator.

**Inbound (22–28).** Namespace ownership is an exact comparison of the
regex-extracted product code with the authenticated one (`crm2`, `crmevil`,
uppercase, `crm-1`, platform roots: all refused). The organization is bound
by the live entitlement decision *and* the live organization status.
Idempotency is the database's `(organization, source, event_id)` unique
key; the fingerprint covers type, version, `occurred_at` and payload
(canonical JSON), not correlation/causation. Events ≤ 64 KiB, depth 8,
2,000 nodes, strings 8 KiB, arrays 500, finite numbers.

**Automation (29–37, 41, 42).** Fuzzed: no eval/getattr, dunder and
Unicode path segments refused, worst-case evaluation 5.8 ms at the maximum
rule size against a maximum payload. Registry-driven actions, both creating
records only. Versions frozen by trigger. Chain depth derived from the
platform's own record of the causing event; the two built-in actions emit
no routed events, so an internal loop cannot form; an external loop is
bounded by ingestion rate limits and the 1,000/rule/hour cap. DST verified
with real `zoneinfo` in 11 zones (385,436 generated runs, 0 duplicates);
a spring-forward gap runs just after the gap (as documented — never
skipped), an ambiguous time runs once at its first occurrence; coalescing
fires once after 5 min … 10 years of downtime.

**Real RabbitMQ outage (43, 44, 96).** Container stopped: three events
published over the real HTTP ingestion path were each accepted `202` in
1.05–1.17 s (one failed enqueue), with events and timers committed; the
sweeper stopped at the first broker error in 1.02 s (`failed 1,
deferred 3`); the outbox relay kept draining (it dispatches in-process,
ADR-008). Container restarted and a real Celery worker started: all 3
events routed, 4 executions and 7 deliveries succeeded, the receiver saw 7
requests for 7 distinct deliveries (no duplicate send), and only the next
schedule timer remained.

**Real Redis outage (45).** Container stopped: an event was accepted in
0.08 s; a member without the permission was still refused and one with it
allowed (no fail-open); a test ping was accepted (throttle degrades per
process, as documented) and every delivery succeeded. Rate limits are the
protection that weakens.

**Tenancy (38–40, 52–56, 89).** Fresh database from zero: all 9 Phase 6
tenant tables have RLS enabled **and forced** with one policy, owned by the
non-superuser migrator; the app role has neither superuser nor BYPASSRLS;
the only `organization_id` tables without RLS are the documented
exemptions; 94 foreign keys, 23 composite (11 on Phase 6 tables), 0
non-deferrable; 4 freeze triggers. No interface module imports
`scheduling`.

**Migrations (93–95).** The documented rollback chain removes every Phase 6
table; re-forward restores RLS and all composite keys; `makemigrations
--check` clean. The audit added no migration, so the implementation pass's
Phase 5 → 6 upgrade rehearsal stands unchanged.

## 6. Gates

| Gate | Result |
|---|---|
| Backend `pytest --create-db` (fresh test database, one run, no other test process) | **1,519 passed, 0 failed, 0 skipped** (28 min 38 s). Was 1,483; +36 audit tests |
| ruff check / format | clean |
| mypy (`make typecheck`) | no issues |
| import-linter | 14 kept, 0 broken (and proven to break on a planted import) |
| bandit `-ll` | no medium/high findings |
| pip-audit `--strict` | no known vulnerabilities; no dependency added |
| `manage.py check` / `makemigrations --check` | no issues / no changes (fresh DB too) |
| `check --deploy` | via `test_security_baseline.py` (in the run above) |
| Frontend, clean copy: `npm ci`, `next typegen && tsc`, eslint | clean |
| Frontend `vitest` | **48 passed** (was 47; +1 double-rotation test, shown to fail on the old code) |
| `next build` + `assert-dynamic-rendering.mjs` | success; all 14 routes dynamic |
| Production CSP (`next start`) | nonce + `strict-dynamic`, all 12 scripts carry the nonce, `frame-ancestors 'none'`, `no-store` |
| axe | no violations (component tests) |
| `npm audit` (all / `--omit=dev`) | 0 / 0 |
| Real RabbitMQ stop + recovery, real Redis stop | pass (§5) |
| Fresh database; rollback + re-forward | pass (§5) |
| Manual screen reader | **NOT EXECUTED** |

The real-outage scenario is an operational rehearsal, not a CI test (it
stops containers); it was run from a temporary test module against the
suite's database with a separate Celery worker process, and is not part of
the committed suite.
