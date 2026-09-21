# Phase 1 Test Report

**Date:** 2026-09-21
**Result: 378 passed, 0 failed, 0 skipped, 0 xfailed**
**Runtime:** 26.5 s (full suite, against real PostgreSQL + PgBouncer)

---

## 1. Summary

```
$ pytest -q
378 passed in 26.50s

$ ruff check backend
All checks passed!

$ ruff format --check backend
167 files already formatted

$ mypy
Success: no issues found in 16 source files

$ lint-imports
Analyzed 163 files, 459 dependencies.
Contracts: 9 kept, 0 broken.

$ manage.py check
System check identified no issues (0 silenced).
```

## 2. By area

| Area | Tests | Covers |
|---|---|---|
| **Isolation** (`tests/isolation/`) | **128** | RLS, fail-closed, R-10 suspension block |
| ├─ `test_rls_isolation.py` | 19 | Correct/wrong/missing tenant, `WITH CHECK`, commit, rollback, savepoint, connection reuse, ORM/RLS agreement |
| ├─ `test_every_tenant_model.py` | 91 | Parametrised over **all 15** tenant models × 6 properties |
| └─ `test_suspension_write_block.py` | 18 | save, delete, bulk paths, task path, all non-active statuses |
| **Identity** | 34 | Normalisation, uniqueness, password handling, auth failures, MFA readiness |
| **Entitlements** | 25 | Deny-by-default, status matrix, most-generous-wins, caching, query counts, single-writer |
| **Files** | 25 | Type validation, magic bytes, path safety, private-by-default, signed URLs, isolation |
| **Outbox / events** | 23 | Atomicity, schema validation, relay, retries, DLQ, consumer idempotency |
| **Request boundary** | 22 | Tenant resolution, uniform denials, R-10 chokepoint 1, error shapes |
| **Authz** | 22 | Deny-by-default, implications, step-up, expiry, cache invalidation, naming |
| **Security baseline** | 22 | Database grants, config validation, secret hygiene |
| **Architecture** | 21 | Import contracts, contracts purity, ORM boundaries, banned patterns, money |
| **Observability** | 21 | Health semantics, correlation, redaction, metric cardinality |
| **Audit** | 16 | Append-only (ORM *and* database), hash chain, redaction, retention |
| **Notifications** | 13 | Never inline, idempotency, bounded retries, failure recording |
| **Concurrency** | 6 | Real threads: relay claims, consumer dedupe, recompute races, context isolation |

Markers overlap by design — a test can be both `isolation` and `security`.
By marker: `isolation` 128, `security` 107, `architecture` 21, `concurrency` 6.

## 3. What makes the suite meaningful

### It runs as the non-owner role

`backend/conftest.py` migrates as `w360_migrator` (the owner), then **rebinds
the live connection to `w360_app`** — non-owner, `NOSUPERUSER`,
`NOBYPASSRLS`. An autouse fixture asserts this on every test.

A suite running as the table owner silently bypasses every RLS policy. Every
isolation test would pass while production was wide open. This is the single
most important property of the harness.

### It runs through PgBouncer

Tests connect via PgBouncer in transaction mode by default
(`W360_TEST_THROUGH_PGBOUNCER`), so the pooling constraints that R-01 is about
are exercised continuously rather than only in the spike.

### Isolation coverage is structural, not enumerated

`test_every_tenant_model.py` is parametrised over the **live model registry**.
Adding a tenant model tomorrow produces six new tests immediately. A
hand-written test per model guarantees the day someone forgets — and the gap
is invisible, because the suite is still green.

The six properties, per model: scoped manager raises without context; the
unscoped manager requires an explicit grant; the table has RLS enabled,
FORCED, with `USING` **and** `WITH CHECK`; no foreign tenant's rows are
visible from either side; zero rows with no context; an
`organization`-leading index exists.

### The enforcement is itself tested

`test_import_linter_actually_detects_a_violation` writes a deliberately false
contract and asserts the linter reports it. A contract that never fails is
indistinguishable from no contract — and this caught a real problem: `python
-m importlinter.cli` exits **0 silently** when it cannot resolve the root
package. Both the test and CI now use the `lint-imports` console script.

### Negative cases throughout

The brief requires more than happy paths. Counting assertions of *refusal*:

| Category | Examples |
|---|---|
| Cross-tenant reads | 91 parametrised + 19 explicit |
| Suspension blocks | 18, across save/delete/bulk/task/status |
| Authorization denials | 22, including "denied for every declared permission with no roles" |
| Entitlement denials | 12, including unknown-status and missing-snapshot |
| Invalid configuration | 11 parametrised production-boot refusals |
| Upload rejections | 8 type/size/content/path cases |
| Append-only violations | 4, at both ORM and database level |

## 4. Bugs the tests found

Every one of these was found by a test, not by review:

| # | Bug | Severity |
|---|---|---|
| 1 | `unscoped_access()` did not satisfy RLS, so no cross-tenant write could succeed | P0 |
| 2 | The tenant resolver could never resolve — every tenant request 404'd | P0 |
| 3 | Audit writes blocked for suspended organizations, masking every denial as `AuditWriteFailed` | P0 |
| 4 | Audit cascaded on organization delete, contradicting the retention requirement | P0 |
| 5 | Content sniffing only rejected known-bad bytes, so HTML-as-PDF passed | P0 |
| 6 | `IntegrityError` caught without a savepoint, poisoning the caller's transaction | P1 |
| 7 | Domain errors surfaced as unhandled 500s | P1 |
| 8 | `ignore_exceptions` is not a Django `RedisCache` option — Redis degradation was unconfigured | P1 |
| 9 | W360.E007 passed vacuously because Celery autodiscovery is lazy | P1 |
| 10 | `base_manager_name="objects"` broke every unscoped insert | P1 |
| 11 | `email_original` was overwritten by normalisation | P2 |
| 12 | `module` is a reserved `LogRecord` attribute — `unscoped_access` raised on every use | P2 |
| 13 | structlog's `add_logger_name` requires a stdlib logger, not `PrintLogger` | P2 |
| 14 | Redundant `event=` kwarg collided with structlog's positional argument | P2 |

Findings 1, 3 and 4 are notable: all three are defence-in-depth working
correctly, where the inner layer refused something the outer layer had
permitted.

## 5. Coverage gaps — stated

| Gap | Why | When needed |
|---|---|---|
| No load / soak / spike testing | No traffic to model, no deployed environment | Before first production customer |
| No chaos testing in staging | No staging environment | Before first production customer |
| No DAST or penetration test | No deployed surface | Before first production customer |
| No generated authorization matrix | Three endpoints, one persona. Generating a matrix over that is ceremony. | With the first product |
| No Celery worker integration test | Tasks are tested through their services; the broker round trip is covered by the R-09 spike | With the first real async workflow |
| No frontend | Out of Phase 1 scope | Phase 2 |

## 6. Known harness limitation

A `PytestWarning` at session end: the test database cannot be dropped because
PgBouncer holds a pooled server connection to it. It is cosmetic — the next
run's `pytest_configure` terminates stale sessions and recreates the database,
so the suite is self-healing. It does not affect any result.

## 7. Reproducing

```bash
make up          # PostgreSQL, PgBouncer, Redis, RabbitMQ
make migrate     # as the owner role
make test        # 378 tests
make ci          # lint + typecheck + import contracts + system checks + tests
make spikes      # re-run the three verification spikes
```
