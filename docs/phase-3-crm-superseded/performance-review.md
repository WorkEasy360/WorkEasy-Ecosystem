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

# Performance Review

**Status:** Accepted (Phase 3)

---

## 1. Method and scope

Query-count budgets (`tests/test_query_budgets.py`,
`django_assert_num_queries`) against a small, realistic populated dataset
(15-30 rows) - an N+1 regression guard, **not** a load test. Task 49's full
load/soak testing at production scale remains Phase 10 scope; nothing in
Phase 3 surfaced a blocker that would pull it forward.

## 2. What was measured

| Endpoint/selector | Query count | What it proves |
|---|---|---|
| Company list (20 rows) | 3 (entitlement + permission + list) | Constant regardless of row count - `select_related("owner")` prevents one query per row for the owner's email/name |
| Deal list (20 rows) | 3 | Same, plus `select_related("stage")` for the stage name/category every list DTO includes |
| Pipeline board (1 stage, 30 deals) | 4 | One query for the stage's deals regardless of how many are in it - not one per deal (see the N+1 finding below) |
| Deal detail (with 3 tags) | 6 (entitlement + permission + deal join + tags prefetch + contacts prefetch + 1 more) | `prefetch_related("deal_tags__tag", "deal_contacts__contact")` avoids one query per tag/contact |
| Dashboard KPIs (15 open deals) | 9 (entitlement + permission + 6 aggregate queries: open sum, weighted sum, won count, lost count, activities overdue, activities due) | Aggregate queries (`Sum`, `Count`) do the weighting/summing in the database, not by loading every deal into Python |

Every count includes 2 queries neither list literally names: the first
entitlement check and the first permission check in the request/test scope
(both memoised after that - see
[tenant-isolation-review.md](tenant-isolation-review.md) and
`tests/test_entitlement.py`/`tests/integration/test_crm_entitlement_memoization.py`).
Exact counts are pinned in
`workeasy360/products/crm/tests/test_query_budgets.py` via
`django_assert_num_queries`, which is the source of truth going forward - a
future change that regresses a count fails CI, not this document.

## 2a. N+1 found and fixed: the pipeline board

**Before a fix, `pipeline_board` cost 34 queries for one stage holding 30
deals - not the 2 the test originally (wrongly) assumed.** Root cause:
`_to_dto()` reads `deal.stage.name`/`deal.stage.category` for every deal in
every column, and the board's own deal query only carried
`select_related("owner")` - not `"stage"` - even though the surrounding
loop already iterates per stage. Each `deal.stage` access outside the
`select_related` set triggered its own query, one per deal.

**Fix:** `select_related("owner", "stage")` on the per-stage deal query
(`selectors/deal_selectors.py::pipeline_board`). Re-measured: 4 queries,
constant regardless of deal count (verified again at 30 deals in one
column). This is recorded as a finding, not silently corrected, because it
is exactly the category of bug a wrong first guess at a query budget can
hide: the original pinned count (2) was never actually measured against
real data before being written down, and running the test against a
populated dataset - not a single row - is what caught it.

## 3. Design choices made for this reason

- Every list selector takes `select_related` for whatever it always shows
  (owner, stage) and never for what it does not.
- `pipeline_board` bounds deals **per stage**
  (`limit_per_stage`, default 25, clamped to `MAX_PAGE_SIZE`) rather than
  loading an entire pipeline's deals into memory - Task 47/29's "no
  unbounded list/search" requirement, applied to the one view shape (a
  kanban board) that could otherwise return an unbounded response by
  construction.
- Dashboard KPIs and the forecast breakdown are computed with database
  aggregates (`Sum`, `Count`, `TruncMonth`) rather than iterating deals in
  Python - the alternative would scale linearly with deal count for a
  request that should stay near-constant.
- Cursor (keyset) pagination throughout (`selectors/pagination.py`), never
  offset - `OFFSET 50000` forces PostgreSQL to scan and discard 50,000
  rows; a keyset cursor does not, regardless of how deep into a list a
  caller pages (Task 29).

## 4. What was not measured, and why

- **Production-scale datasets** (thousands to millions of rows per
  organization): no such dataset exists in this environment, and
  fabricating a specific latency number for one would be exactly the
  invented-benchmark Phase 3 explicitly forbids. The query-count budgets
  above are the honest substitute: they prove the *shape* of each query
  plan does not degrade with row count, which is the property that
  actually predicts production behaviour, without claiming a number this
  environment cannot produce honestly.
- **Index effectiveness under EXPLAIN**: every CRM table's indexes lead
  with `organization` (Phase 3 Task 48, and asserted platform-wide by the
  existing `test_has_an_organization_leading_index` registry-derived
  check, which now covers all 17 CRM tables too) - beyond that, no index
  was added speculatively. `docs/architecture/10-performance-concurrency.md`
  is the standing rationale for organization-leading indexes; nothing here
  adds a new one without that shape.
- **Redis/caching**: none added. No CRM read path was found to need one -
  the entitlement and permission caches CRM relies on (`platform.entitlements`,
  `platform.authz`) already exist and were exercised, not modified.

## 5. Trigger review (Task 54/55, performance-adjacent items)

See [failure-mode-review.md](failure-mode-review.md) §4 for the full trigger
table (rate limiting, circuit breakers, backpressure, entitlement-version
query, malware scanning, etc.) - none reached a Phase-3 trigger.
