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

# Concurrency Review

**Status:** Accepted (Phase 3)

---

## 1. Method

Real PostgreSQL, real threads (`threading`/`ThreadPoolExecutor`), real
commits - `pytest.mark.django_db(transaction=True)` throughout
(`tests/integration/test_crm_concurrency.py`), because a test wrapped in
pytest-django's default rollback-only transaction has every thread looking
at a different, invisible view of the data and cannot exercise a
uniqueness constraint or a row lock under genuine contention. Each worker
thread establishes its **own** tenant scope
(`tenant_context(ctx), tenant_db_scope(ctx)`) - contextvars and database
connections are per-thread, so a worker that inherited the main thread's
scope would not be testing anything real. This mirrors
`tests/integration/test_commercial_concurrency.py` exactly, the only
existing precedent for this shape of test in the repository.

## 2. Contention points tested

| # | Race | Mechanism under test | Result |
|---|---|---|---|
| 1 | Lead conversion | Row lock + idempotency ledger, two independent guards | 8 threads converting the same lead -> exactly 1 `Contact` |
| 2 | Default pipeline | Partial unique index + full-row-set locking | 8 threads each promoting a different pipeline -> exactly 1 default, always |
| 3 | Stage reorder | Deferred unique constraint + locked row set, `ORDER BY id` | 8 threads each submitting a random shuffle of the same 5 stages -> a valid, gap-free permutation of 0..4 |
| 4 | Deal stage change | Optimistic concurrency (`Deal.version`) | 2 threads both reading version N, racing to move the deal two different ways -> exactly 1 succeeds, 1 raises `ConcurrentModification` |
| 5 | Activity completion | Idempotent state check before any write | 8 threads completing the same activity -> exactly 1 `crm.activity.completed` event |

## 3. Finding: the default-pipeline race (P0, found and fixed)

**The first version of `set_default_pipeline` locked only the pipeline
being made default.** Two callers naming two *different* target pipelines
each lock a distinct row and never contend with each other; both could pass
"clear whichever row is currently default" believing they alone were
changing it. Running the 8-thread concurrency test against that version
produced a live `IntegrityError` on `uq_one_default_pipeline_per_org` -
proof the partial unique index was doing exactly its job as the last line
of defence, and equally clear proof the application-level race existed
above it.

**Fix:** lock every pipeline row for the organization, in a fixed `id`
order, before reading or changing `is_default` at all
(`services/pipeline_services.py::_lock_all_pipelines`, applied inside both
`create_pipeline(is_default=True)` and `set_default_pipeline`). This also
closes the "no default pipeline exists yet" case, which locking only a
current-default row cannot cover at all (there is nothing to lock).
Re-run: 8/8 threads pass, every time across repeated local runs.

This is recorded here rather than only in the test file because it is
Phase 3's version of the Phase 2 Opus audit's core lesson: a green
single-threaded suite is not evidence a concurrency invariant holds, and a
database constraint that exists specifically to catch this class of bug is
only proof of anything if a real race is thrown at it.

## 4. Deliberately narrower locking, not table-wide

`reorder_stages` locks only the target pipeline's own stages, not every
pipeline in the organization - reordering one pipeline's stages does not
need to block a concurrent reorder of a *different* pipeline, and a
narrower lock is the "narrowest safe locking strategy" Task 45 asks for.
`_lock_all_pipelines` is deliberately coarser (whole organization) because
"which pipeline is the default" is an organization-wide fact with no
narrower unit to lock against - contrast this with `reorder_stages`, where
the natural unit (one pipeline's stages) is available and used.

## 5. Not separately covered by a dedicated test, and why

- **Owner reassignment race**: `bulk_reassign_owner` runs each entity's
  `update_*` inside the bulk transaction; no two owners can be assigned to
  the same record in the same call, and no destructive interleaving is
  possible beyond what "last statement in the transaction wins" already
  means for any other single-row update. Not a novel contention point
  beyond what optimistic concurrency (§4) and ordinary row-level MVCC
  already cover.
- **Duplicate bulk commands**: bulk operations take no idempotency key by
  design (Task 25 does not ask for one, and a bulk archive is naturally
  idempotent - archiving an already-archived record is a no-op, verified
  in `test_archive_and_restore_company_is_idempotent`). A duplicate bulk
  request produces the same end state, not a duplicated side effect.
- **Archive/update conflict**: covered functionally
  (`test_archived_deal_can_still_be_updated_not_frozen_silently`), not
  under simultaneous threads - archiving and updating are both ordinary
  row-locked writes with no special interaction to race.

## 6. What would extend this review

A production-scale soak test (Task 49) is explicitly out of Phase 3's
scope ("Full enterprise load/soak testing remains Phase 10 unless a Phase 3
blocker appears" - none did). The 8-worker figure throughout matches the
existing commercial concurrency suite's own choice, not a measured
production concurrency level.
