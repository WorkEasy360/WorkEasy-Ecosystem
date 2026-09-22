> **SUPERSEDED — CRM MOVED OUTSIDE PLATFORM SCOPE.**
>
> This document describes the CRM product as it was built *inside* this
> repository during Phase 3. That code was removed by the Phase 3R scope
> correction: products are now separate applications in their own
> repositories ([ADR-017](../architecture/adrs/ADR-017-products-are-separate-applications.md),
> [16 Ecosystem / Product Boundary](../architecture/16-ecosystem-product-boundary.md)).
>
> Retained as historical audit evidence only. **Nothing here describes the
> current repository.** For what was kept from this work and why, see
> [Phase 3R status](../phase-3R/phase-3R-status.md).

---

# Phase 3 — Test Report

**Status:** Accepted (Phase 3)
**Database:** Real PostgreSQL throughout (no SQLite anywhere), RLS genuinely
enforced (`w360_app`, a non-owner `NOBYPASSRLS` role) - never the migrator.

---

## 1. Suite composition

| Layer | Location | Tests |
|---|---|---|
| Domain (pure, no DB) | `products/crm/tests/test_domain.py` | 18 |
| Company/Contact services | `products/crm/tests/test_company_contact_services.py` | 10 |
| Lead conversion | `products/crm/tests/test_lead_conversion.py` | 7 |
| Pipeline/Stage/Deal services | `products/crm/tests/test_pipeline_deal_services.py` | 11 |
| Activities/Notes/Tags/Custom fields | `products/crm/tests/test_activities_notes_tags_fields.py` | 11 |
| Bulk operations | `products/crm/tests/test_bulk_services.py` | 7 |
| Entitlement gate | `products/crm/tests/test_entitlement.py` | 7 |
| Dashboard/forecast correctness | `products/crm/tests/test_dashboard_selectors.py` | 6 |
| Selector smoke + DealContact | `products/crm/tests/test_selectors_and_deal_contacts.py` | 8 |
| Background sweep (worker-shaped) | `products/crm/tests/test_system_tasks.py` | 5 |
| Query-count budgets | `products/crm/tests/test_query_budgets.py` | 5 |
| HTTP API, end to end | `products/crm/tests/test_api.py` | 8 |
| Concurrency (real threads) | `tests/integration/test_crm_concurrency.py` | 5 |
| Entitlement memoization | `tests/integration/test_crm_entitlement_memoization.py` | 1 |
| Security regressions | `tests/security/test_crm_security.py` | 11 |
| **CRM total** | | **120** |
| Isolation, registry-derived, covering all 17 CRM models automatically | `tests/isolation/test_every_tenant_model.py` (parametrised) | 102 of the suite's total |
| Full Phase 0-2 baseline, re-run unmodified except where noted in §3 | everything else | 647 originally reported |

The 120 CRM-specific tests are in addition to the 102 registry-derived
isolation assertions the 17 new tenant models triggered automatically (those
102 are counted once, in the whole-suite total below, not double-counted
here) and in addition to every Phase 0-2 test, all of which were re-run
against the same database, not skipped or mocked around.

## 2. Full-suite result

```
pytest (real PostgreSQL, RLS-enforced role)   869/869 passed, 0 failed, 0 skipped   318.69s (0:05:18)
```

One pre-existing, benign warning (unrelated to test correctness - a database
teardown race between pytest-django's parallel-safe teardown and a lingering
connection from `test_count_overdue_without_scope_helper_raises_not_silently_empty`'s
own worker-thread setup): `OperationalError('database "test_workeasy360" is
being accessed by other users')`. Does not affect the pass count, does not
recur on every run, and does not indicate a test correctness problem.

Run on a database that already held Phase 0-2's applied migrations plus
Phase 3's two new ones (`crm.0001_initial`,
`tenancy.0005_crm_isolation`) - not a freshly dropped-and-recreated one, a
deliberate choice to also prove the new migrations apply cleanly on top of
an existing, populated Phase 2 database rather than only on an empty one.
`makemigrations --check` confirms no model/migration drift either way.

## 3. Pre-existing tests updated (not CRM's own, but touched because CRM
made their Phase 1/2 assumptions concrete)

Every one of these is a *consequence* of CRM becoming the first real
product, predicted by the Phase 1/2 code's own comments - not a defect CRM
introduced. Each is listed with what changed and why.

| Test | Before | After | Why |
|---|---|---|---|
| `commercial/provisioning/tests/test_provisioning.py::test_products_import_nothing_commercial` | Passed vacuously (no product existed to violate it) | Passes because CRM reaches entitlements through `contracts.ports.entitlements.get_gate()`, never `platform.entitlements` directly | Real enforcement of an AST rule that had nothing to check before |
| `integration/events/tests/test_outbox.py::test_unregistered_event_type_is_refused` | Used `crm.deal.won` as its "known unregistered" example | Uses a namespace that can never be a real product code | Phase 3 registered `crm.deal.won` for real |
| `platform/authz/tests/test_authz.py::test_only_non_product_namespaces_register_permissions` (renamed `test_only_namespaces_with_enforcement_register_permissions`) | Asserted `crm` was *not* a valid permission namespace | Asserts it *is*, alongside `platform`/`commercial` | CRM permissions are now real and enforced - the same widening Phase 2 already did once for `commercial` |
| `platform/authz/tests/test_authz.py::test_no_product_permissions_are_registered_in_phase_1` (renamed `test_undeclared_product_permissions_remain_unregistered`) | Asserted CRM's example keys were unregistered | Asserts the *remaining* (books/inventory/projects) examples are unregistered; a new sibling test (`test_crm_permissions_are_registered_with_real_enforcement`) asserts CRM's are | The Phase 1 promise ("these become real when products/crm/permissions.py declares them") came due |
| `tests/security/test_security_baseline.py` (3 tests) | Assumed no local `.env` file existed | Explicitly neutralises `Env.load_dotenv` for the duration of each production-settings-import test | A local `.env` (the project's own documented `cp .env.example .env` workflow) was backfilling values the tests needed genuinely absent - a latent hermeticity gap, unrelated to CRM's own code, found because this session's environment setup created that file for the first time |

No frozen Phase 0-2 behaviour changed. Every one of these is a test
assertion catching up to a state the underlying code already allowed, or a
test's own hermeticity being tightened - never a relaxation of what the
platform enforces.

## 4. Bugs found and fixed by this test suite (not pre-existing)

| # | Found by | Bug | Fix |
|---|---|---|---|
| 1 | `tests/integration/test_crm_concurrency.py::test_concurrent_set_default_pipeline_leaves_exactly_one_default` | `set_default_pipeline` locked only the target row; 8 threads each promoting a different pipeline produced a live `IntegrityError` on the partial unique index | Lock every pipeline row for the organization first (see [concurrency-review.md](concurrency-review.md)) |
| 2 | `tests/test_pipeline_deal_services.py::test_reopening_a_won_deal_clears_closed_at` | A deal created directly into a WON/LOST stage did not get `closed_at` stamped | `create_deal` now classifies its own initial stage the same way `change_stage` classifies a transition |
| 3 | `tests/test_activities_notes_tags_fields.py::test_tag_attach_is_idempotent_and_creates_on_first_use` | `tag_services.add_tags` passed a raw UUID to a Django FK field name instead of its `_id` variant, raising `ValueError` on every call | Use `f"{fk_field}_id"` as the kwarg |
| 4 | Writing `tests/security/test_crm_security.py` | `tag_services.add_tags`/`remove_tags` performed the write with **no authorization check at all** | Added `_require_tag_access`, mapping entity type to its own `*.update` permission, called first in both functions |
| 5 | `tests/test_query_budgets.py::test_pipeline_board_query_count_is_bounded_by_stage_count_not_deal_count` | `pipeline_board` cost 34 queries for 30 deals in one stage (one query per deal to fetch `deal.stage`) | `select_related("owner", "stage")` on the per-stage deal query - see [performance-review.md](performance-review.md) §2a |
| 6 | Manual review while writing `test_dashboard_selectors.py` | (Test-only) forecast tests used the 1st of the month as a close date, which is in the past relative to "today" whenever the suite runs after the 1st, and the forecast selector correctly excludes past dates | Tests use today's own date instead |

Bugs 1, 3, 4 and 5 are production code defects a passing-but-shallow test
suite would not have caught; each has a regression test named above that
fails without its fix. Bug 2 was caught before any user-facing behaviour
shipped. Bug 6 was a test-only defect, corrected before it could hide a
real one.

## 5. What Task 62 asked for, and where it is

| Requirement | Where |
|---|---|
| Unit | `test_domain.py` (pure), plus every `test_*_services.py` file |
| Integration | `tests/integration/test_crm_concurrency.py`, `test_crm_entitlement_memoization.py` |
| PostgreSQL/RLS | Every test - `django_db_setup` rebinds the connection to `w360_app` for the whole suite (`backend/conftest.py`), never SQLite |
| Authorization | `test_entitlement.py`, `test_crm_security.py`, plus every service test's implicit `require_crm_access` exercise |
| Entitlements | `test_entitlement.py`, `test_crm_entitlement_memoization.py` |
| API | `test_api.py` |
| Concurrency | `tests/integration/test_crm_concurrency.py` (real threads, `transaction=True`) |
| Events/outbox | Assertions embedded in `test_pipeline_deal_services.py`, `test_activities_notes_tags_fields.py`, `test_lead_conversion.py` (checking `OutboxEvent` rows directly) rather than one separate file |
| Background tasks | `test_system_tasks.py` (worker-shaped, no `CELERY_TASK_ALWAYS_EAGER` reliance) |
| Failure injection | Silent-failure categories in [tenant-isolation-review.md](tenant-isolation-review.md) §5, each with its own test |
| Performance/query counts | `test_query_budgets.py` |
| Architecture constraints | `backend/tests/architecture/test_architecture.py` (two allowlist entries added, both documented in [cleanup-review.md](cleanup-review.md) and above) |
| Security regression | `tests/security/test_crm_security.py` |

## 6. Not exhaustive, by design

This is not 100% line coverage, and Phase 3 does not claim it is. See the
final section of [phase-3-status.md](phase-3-status.md) - "what bugs could
still leave the suite green" - for the categories this report does not
close, stated explicitly rather than answered "none".
