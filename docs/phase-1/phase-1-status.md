# Phase 1 — Exit Gate Assessment

**Date:** 2026-09-21
**Assessed by:** Implementation
**Repository state at start:** Phase 0 documentation only. No code.

---

# PHASE 1 STATUS: **PASS**

---

## 1. Exit criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Custom identity foundation works | ✅ | `platform/identity` — 34 tests. Email-normalised, Argon2id, MFA-ready, no membership fields |
| 2 | Organization / membership model works | ✅ | `platform/organizations` — Organization, Membership, Invitation with lifecycle and constraints |
| 3 | Tenant scoping is fail-closed | ✅ | `TenantScopedModel` **raises**; 128 isolation tests |
| 4 | PostgreSQL RLS isolation is verified | ✅ | 16/16 tables enabled + FORCED + `USING` + `WITH CHECK`; verified as the non-owner role |
| 5 | R-01 resolved with evidence | ✅ | **960 interleaved transactions, 2 shared server connections, 0 leaks** — [rls-verification.md](rls-verification.md) |
| 6 | R-09 broker decision verified | ✅ | AmazonMQ (RabbitMQ), 16 checks passed — [broker-verification.md](broker-verification.md) |
| 7 | R-14 entitlement benchmark recorded | ✅ | Measured; **budget revised in writing** — [entitlement-benchmark.md](entitlement-benchmark.md) |
| 8 | Suspended organizations cannot bypass write blocking | ✅ | Two chokepoints + four bulk paths; 18 tests |
| 9 | RBAC denies by default | ✅ | All 25 permissions denied with no roles; unknown permission raises |
| 10 | Entitlement read contract works | ✅ | `check`/`feature`/`quota`/`has_seat`; 25 tests; single-writer enforced three ways |
| 11 | Audit log is append-only | ✅ | Grants **and** trigger; refused at ORM and database level |
| 12 | Notification foundation fails safely | ✅ | Never inline; provider and broker outages do not fail the caller |
| 13 | File foundation is private and tenant-scoped | ✅ | Not servable until scanned; magic-byte verification; signed short-lived URLs |
| 14 | Celery queues and retries are bounded | ✅ | Three groups, per-task routing, 5 attempts, full jitter, no nesting |
| 15 | Outbox foundation is transaction-safe | ✅ | Atomic with the business change; rollback discards; crash-recoverable |
| 16 | Health / liveness / readiness are correct | ✅ | Liveness survives a database outage with zero queries |
| 17 | Structured observability exists | ✅ | JSON logs, correlation spine, redaction, cardinality guard |
| 18 | Architecture rules enforced in CI | ✅ | 9 import contracts + 9 system checks + 21 fitness tests; the linter is itself tested |
| 19 | Full test suite passes | ✅ | **379 passed, 0 failed** |
| 20 | No unresolved P0 | ✅ | 5 found, 5 fixed with regression coverage |

## 2. Verification spikes

| Spike | Verdict | Headline |
|---|---|---|
| **R-01** RLS + PgBouncer | **PASS** | 960 transactions, 24 workers, 2 server connections, **0 leaks**. Fallback not needed. Second finding: `prepare_threshold=None` is correctness-critical — 63/960 transactions complete without it. |
| **R-09** Broker | **PASS** | AmazonMQ chosen on operability (priority + Celery observability). Correctness depends on neither broker. |
| **R-14** Entitlement latency | **Measured, budget revised** | Per-check target unachievable (0.93 ms I/O floor). Per request: **1.56 ms p50 / 1.79 ms p95, 1 query**. Repeat check: **0.0043 ms**. No distributed-cache complexity added. |

## 3. Results

```
pytest            379 passed, 0 failed, 0 skipped        26.5 s
ruff check        All checks passed
ruff format       167 files already formatted
mypy              Success: no issues in 16 source files
lint-imports      9 contracts kept, 0 broken (163 files, 459 dependencies)
manage.py check   no issues (0 silenced)
```

## 4. Findings

### P0 — 5 found, 5 fixed

| # | Finding | Fix |
|---|---|---|
| 1 | `unscoped_access()` did not satisfy RLS — no cross-tenant write could succeed | `organization_db_scope(org_id)` |
| 2 | Tenant resolver could never resolve — every tenant request 404'd | Resolve the organization first, then enter its scope |
| 3 | Audit blocked for suspended organizations, masking every denial | `tenancy_write_block_exempt` on `AuditEvent` only |
| 4 | Audit cascaded on organization delete | `DO_NOTHING`, `db_constraint=False` — audit is a retained tombstone |
| 5 | Content sniffing rejected only known-bad bytes | Verify the declaration against the bytes |

Findings 1, 3 and 4 are defence-in-depth working: the inner layer refused what
the outer layer had permitted.

### P1 — 6 found, 4 fixed, 2 carried

| # | Finding | Status |
|---|---|---|
| 1 | `IntegrityError` caught without a savepoint, poisoning the caller's transaction | **Fixed** |
| 2 | Domain errors surfaced as unhandled 500s | **Fixed** |
| 3 | Pinned dependencies carried 15 known CVEs (Django 5.1.6, DRF 3.15.2, python-dotenv 1.0.1) | **Fixed** — upgraded to Django 5.2.17 / DRF 3.18.1 / dotenv 1.2.3; `pip-audit --strict` clean |
| 4 | Three tenant-to-tenant FKs had no composite constraint, so a cross-tenant `granted_by` was representable | **Fixed** — `tenancy/0002`, plus a structural fitness test that found the third one |
| 5 | Breach-corpus password check not implemented | **Carried.** Interface exists, deliberately unregistered so the gap is visible. **Platform · before first customer** |
| 6 | Malware scanning is a null implementation | **Carried.** Quarantine and not-servable-by-default are real. **Platform · before uploads are enabled** |

Plus, from the reviews:

| Finding | Status |
|---|---|
| `ignore_exceptions` is not a valid Django `RedisCache` option | **Fixed** — tolerance moved to call sites and tested |
| W360.E007 passed vacuously (lazy Celery autodiscovery) | **Fixed** — tasks registered at app-ready |
| `base_manager_name` broke unscoped inserts | **Fixed** |
| No circuit breakers | **Carried · Platform · with the first external provider** |
| No backpressure / load shedding | **Carried · Platform · before first customer** |
| First entitlement check costs one query | **Carried deliberately.** Trigger: p95 > 4 ms, or cache hit ratio < 0.95 |

### P2 — carried, documented

Rate limiting; session/MFA policy wiring; CSP (no HTML surface yet); audit
hash-chain forks under concurrency; no SSRF-safe fetch helper; metrics are
in-process; no load/soak/spike/chaos testing; no read replica; Celery Beat not
configured; no partition strategy for `prune_published`.

Each has a stated reason and a trigger in the review documents.

## 5. Architecture deviations

Three, each with an ADR, each narrower or stricter than what it amends:

| ADR | Change | Why |
|---|---|---|
| [013](../architecture/adrs/ADR-013-package-namespace.md) | Modules nested under `workeasy360/` | A top-level `platform/` shadows the stdlib `platform` module, breaking `platform.system()`. Verified empirically. Directory names and layer rules unchanged. |
| [014](../architecture/adrs/ADR-014-membership-fk-allowlist.md) | `Membership` added to the FK allowlist | Phase 0's own models require it in two places. Restricted to `platform/` and `commercial/`; products unchanged. |
| [015](../architecture/adrs/ADR-015-platform-p0-ordering.md) | P0 split into P0a (observability) < P0b (tenancy) | `tenancy` must log and `observability` must read tenant context; F-5 forbade both directions. Adds an ordering constraint rather than removing one. |

No frozen Phase 0 decision was weakened.

## 6. Decisions frozen for Phase 2

Changing any of these requires a new ADR.

### Structure
1. Modules live under `workeasy360/`; directory names and layer rules as documented.
2. `observability` (P0a) is strictly below `tenancy` (P0b); higher layers push via registries.
3. Cross-module FK allowlist: `Organization`, `User`, `File`, `Membership` (platform/commercial only).

### Tenancy
4. `TenantScopedModel.objects` **raises** on missing context; it never returns empty.
5. `unscoped` requires `unscoped_access(reason=…)` and an allowlisted module.
6. `unscoped_access()` relaxes Layer 2 only — cross-tenant writers enter `organization_db_scope()` **per organization**.
7. RLS policies use `NULLIF(current_setting(…), '')::uuid`.
8. `prepare_threshold=None` and `CONN_MAX_AGE=0` are correctness requirements behind PgBouncer.
9. Transaction pooling is the web-tier mode. The session-pooling fallback is not in use.

### Suspension
10. Two chokepoints: middleware and `TenantScopedModel.save()/delete()`, plus all four bulk paths.
11. `AuditEvent` is the **only** write-block exemption. A second requires an ADR.

### Authorization and entitlement
12. Deny by default; an unknown permission **raises**.
13. Permissions are code-declared; a permission not in the registry cannot be granted.
14. Entitlement budget is **per request**: 2 ms p50 / 5 ms p95 / 10 ms p99, ≤ 2 queries.
15. Request-local caching first. No distributed-cache layer without a measured trigger.
16. `platform.entitlements.services.write` is importable only by `commercial/provisioning`.

### Integration
17. Broker: AmazonMQ (RabbitMQ). Consumers stay order-tolerant regardless.
18. Three queue groups with per-task routing, so splitting is a deployment change.
19. `publish()` requires an open transaction and validates against the registry.
20. Event names are past-tense; command-shaped names are rejected at registration.

### Audit and observability
21. Audit is append-only by **grants and a trigger**, and survives organization deletion.
22. `organization_id` is never a metric label.
23. Liveness checks nothing. Readiness checks Tier 0 only.

## 7. What Phase 1 deliberately did not do

- **No product features.** The nine product packages contain `__init__.py` and nothing else, asserted by a test.
- **No commercial flows.** No catalog, pricing, subscriptions or billing. `provisioning` exists because the single-writer rule needs a writer.
- **No partner model.** PartnerAccount and capability grants are Phase 2.
- **No frontend.**
- **No speculative infrastructure.** No search cluster, no read replica, no distributed cache beyond the entitlement snapshot, no circuit breakers before there is an external provider to break.

## 8. Recommended Phase 2 sequence

Not part of Phase 1 scope; the ordering follows from the dependency graph.

1. Close the two carried P1 items (breach corpus, malware scanning) if customer data is imminent.
2. `commercial/catalog` + `pricing` + `subscriptions`, then wire `provisioning` to compute from subscriptions rather than grants alone.
3. Session/MFA policy, rate limiting and CSP — the security items deferred for want of a surface.
4. **The first product, end to end**, as the proof that the boundaries hold.
5. The second product — the real test, because it is the first time the event path carries weight.

Phase 0 §7 is right that the second product is where the architecture is
actually validated. If adding it requires changing the first, something was
wrong earlier.

---

**Phase 2 has not begun.**
