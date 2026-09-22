# Phase 2 Test Report

**Date:** 2026-09-21
**Database:** PostgreSQL 16 through PgBouncer (transaction pooling), as the
non-owner `w360_app` role with `NOBYPASSRLS`.

---

## 1. Headline

```
pytest            627 passed, 0 failed, 0 skipped        139 s
ruff check        All checks passed
ruff format       211 files already formatted
mypy              Success: no issues in 17 source files
bandit -ll        0 high, 0 medium, 0 #nosec suppressions
pip-audit         No known vulnerabilities found
lint-imports      11 contracts kept, 0 broken
manage.py check   no issues (0 silenced)
check --deploy    6 issues — identical to the Phase 1 baseline
makemigrations    No changes detected
```

Phase 1 finished at **379 passed**. Phase 2 adds **248 tests** and changes
none of Phase 1's behaviour: the whole Phase 1 suite still passes unmodified,
apart from two Phase-1 tests whose *scope* was widened for stated reasons
(§5).

## 2. By area

| Area | Tests |
|---|---|
| `workeasy360/commercial/` | 147 |
| `workeasy360/platform/` | 157 |
| `workeasy360/integration/` | 23 |
| `tests/` (cross-cutting) | 300 |
| **Total** | **627** |

## 3. By marker

| Marker | Tests | What it covers |
|---|---|---|
| `isolation` | 159 | Every tenant model, parametrised over the live registry |
| `security` | 127 | Authorization, tenancy, tampering, replay |
| `architecture` | 21 | Import contracts, fitness rules, banned patterns |
| `concurrency` | 17 | Real threads, real connections, real commits |

The isolation and security counts both grew automatically: the Phase 1 suites
are parametrised over the model registry and the permission registry, so
Phase 2's five new tenant models and six new permissions were covered the
moment they existed. That is the Phase 1 pattern working as intended.

## 4. What Phase 2 added

| Suite | Tests | Covers |
|---|---|---|
| `commercial/catalog/tests/test_catalog.py` | 40 | Products, features, plans, versioning, bundles, immutability, change classification |
| `commercial/pricing/tests/test_pricing.py` | 24 | Resolution order, historical correctness, price models, currency safety |
| `commercial/subscriptions/tests/test_subscriptions.py` | 51 | Lifecycle, items, bundles, trials, upgrade, downgrade, cancellation, renewal, suspension |
| `commercial/provisioning/tests/test_provisioning.py` | 32 | Determinism, idempotence, drift, sources, the single-writer rule |
| `tests/integration/test_seat_licensing.py` | 13 | Capacity, assignment, over-limit policy |
| `tests/integration/test_commercial_concurrency.py` | 11 | Eleven contention points |
| `tests/integration/test_commercial_failure_modes.py` | 14 | Transaction boundaries, injected failures, redelivery |
| `tests/security/test_commercial_security.py` | 19 | The HTTP attack surface |
| `tests/performance/test_commercial_queries.py` | 13 | Pinned query counts and growth |
| Parametrised isolation (automatic) | 30 | Five new tenant models × six properties each |

## 5. Phase 1 tests whose scope changed

Two, both widened rather than weakened, both with the reason recorded in the
test:

| Test | Change |
|---|---|
| `test_platform_permissions_use_the_platform_prefix` → `test_only_non_product_namespaces_register_permissions` | Now admits `commercial` alongside `platform`. The rule it protects is unchanged: **no product may register a permission before its module exists**, because a grantable permission with no enforcement is worse than none. A new test, `test_no_system_role_grants_catalog_or_pricing_management`, was added alongside it |
| `test_unscoped_manager_use_is_allowlisted` | Three modules added to the allowlist, each with a written reason and each mirroring an existing Phase 1 precedent. See the security review §5 |

One Phase 1 test was also **strengthened**:
`test_no_writable_serializer_uses_fields_all` now reads assignments by AST
rather than searching text, so a docstring explaining the rule is no longer a
violation.

## 6. Tests relocated

`test_seats.py` moved from `platform/entitlements/tests/` to
`tests/integration/test_seat_licensing.py`. It exercises both platform and
commercial, and a test file inside `platform/` that imports `commercial/` is
a genuine upward edge in the import graph — the linter was right to refuse
it. Moving the file was the honest fix rather than widening the contract.

## 7. Notable test designs

**Registry-derived, not hand-maintained.** Three suites compute their
expectations from live state:

- the isolation suite, from the model registry
- `test_commercial_status_produces_the_expected_entitlement_status`, from
  `list(SubscriptionStatus)` — a status added tomorrow is covered at once
- `test_every_tenant_to_tenant_fk_has_a_composite_constraint`, from the
  model registry compared against the live schema

**Structural, not textual.** Checks that could be fooled by a comment read
the AST instead: the single-writer sweep, the products-import check, the G6
mass-assignment check, and the "every mutation is audited" sweep.

**Adversarial rather than illustrative.** The immutability tests attack with
`QuerySet.update()`, going around the service guard, because the trigger is
the control being tested. The security tests send the request an attacker
would craft — their own organization's slug in the path, someone else's id in
the body.

**Real concurrency.** Eleven tests with `transaction=True`, a
`threading.Barrier`, and real connections. That choice found both P0s.

## 8. Running it

```bash
make up            # PostgreSQL, PgBouncer, Redis, RabbitMQ
make migrate       # as the owner role
make test          # the full suite, from a clean database
make ci            # lint, typecheck, lint-arch, check, test
```

The suite drops and recreates its database on every run — `conftest.py`
terminates PgBouncer's pooled sessions first, so an interrupted run
self-heals rather than requiring manual cleanup.

Migrations run as `w360_migrator`; every assertion then runs as `w360_app`,
which owns nothing and cannot bypass RLS. An autouse fixture fails the run
loudly if that is ever not true, because otherwise every isolation test would
be a test of nothing.

## 9. No skips

```
627 passed, 0 failed, 0 skipped
```

No test was skipped, marked `xfail`, or narrowed to make a failure go away.
The two Phase-1 tests whose scope changed are documented above with their
reasons, and both still fail if the rule they protect is broken.
