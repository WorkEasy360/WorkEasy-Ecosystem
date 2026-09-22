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

# Pipelines, Stages and Deals

**Status:** Accepted (Phase 3)

---

## 1. Pipeline: the default invariant

`Pipeline.is_default` has exactly one `True` row per organization, enforced
by a **partial unique index** (`UniqueConstraint(fields=["organization"],
condition=Q(is_default=True))`) - the database refuses a second one to
exist, full stop, regardless of what application code does.

The locking around it exists to make a *race* resolve predictably (last
write wins, never zero defaults, never two), not to enforce the invariant -
the constraint does that on its own. An earlier version of
`set_default_pipeline` locked only the pipeline being *made* default, which
does not serialise two callers naming two *different* targets: a concurrency
test with 8 threads, each promoting a different pipeline, produced a live
`IntegrityError` (two threads both believing they alone were changing the
default). The fix (`services/pipeline_services.py::_lock_all_pipelines`)
locks every pipeline row for the organization, in a fixed `id` order,
before any read of `is_default` - including the "no default exists yet"
case, where there would otherwise be no row at all to contend on. See
[concurrency-review.md](concurrency-review.md) for the finding in full.

## 2. Stage: category, not name, and no duplicate positions

`PipelineStage.category` (`open`/`won`/`lost`) is what a stage *means*.
`name` is display only. A pipeline owner renaming "Closed Won" to "Signed"
must not silently turn every won deal into an open one - Task 14's explicit
concern - and nothing in `services/` or `selectors/` ever branches on
`name`.

`sort_order` is unique per pipeline via a **deferred** unique constraint
(`Deferrable.DEFERRED`) - the database only checks it at commit, which is
what lets `reorder_stages` write a whole new permutation inside one
transaction without a temporary offset dance (write position 0, 1, 2... in
one pass, even though two rows briefly share a value mid-transaction).
`reorder_stages` locks the pipeline and every one of its stages,
`ORDER BY id`, before writing anything - a concurrency test with 8 threads
each submitting a random shuffle of the same 5 stages leaves a valid,
gap-free, duplicate-free permutation of `0..4` every time.

## 3. Deal: one field for open/won/lost

`Deal` has **no `status` field.** Its open/won/lost state is always
`self.stage.category` - read through a `select_related("stage")` that every
list and detail selector already does for the stage's name, so it costs no
extra query. Task 17's "do not permit a WON deal with an OPEN stage" is not
a state a denormalised field could drift into; it is a state that has
nowhere to be recorded, structurally.

**The stage must belong to the deal's own pipeline.** Enforced twice:

1. `change_stage` looks the target stage up filtered by
   `pipeline_id=deal.pipeline_id` and raises a clear `CrmValidationError` if
   it does not belong.
2. A **composite foreign key** on `(crm_deal.stage_id, crm_deal.pipeline_id)`
   referencing `crm_pipeline_stage (id, pipeline_id)`
   (`platform/tenancy/migrations/0005_crm_isolation.py`,
   `STAGE_BELONGS_TO_PIPELINE`) makes the combination unrepresentable at the
   database level even if every application check were somehow bypassed.
   This reuses `migrations_helpers.add_tenant_fk` with
   `tenant_column="pipeline_id"` instead of the usual `"organization_id"` -
   the same "child's second column must match the parent's" shape, applied
   to a business invariant instead of a tenant one. Because the *tenant*
   composite FK on `stage_id` already exists too, the helper gained an
   optional `constraint_name` parameter so the two constraints on the same
   column do not collide on the default name.

## 4. Money

`amount_minor` (`BigIntegerField`) + `currency` (`CurrencyCode` choices),
never a float, matching `contracts.values.money.Money`'s own representation
(R-08). A `CheckConstraint` requires both or neither - an amount with no
currency, or a currency with no amount, is refused at the database level.

**Not an accounting value.** A deal's amount is CRM's own commercial
forecast; Books remains the eventual authority for anything that touches a
ledger, and CRM creates no accounting entries anywhere (Task 20).

## 5. Deal stage history - append-only

`DealStageHistory` is written exactly once per transition, including the
first row a deal is created with (`from_stage=None`). Append-only is
enforced at the database grant level (`migrations_helpers.make_append_only`)
- `REVOKE UPDATE, DELETE` from the application role plus a rejecting
trigger - the same mechanism `audit_event` uses, so an ORM bug, a data-fix
script or the admin cannot rewrite history, only application discipline
could be bypassed by any of those. Tested directly:
`test_deal_history_is_append_only` attempts `.save()` on an existing row and
asserts `IntegrityError`.

A consequence, not a bug: hard-deleting a `Deal` is refused the moment it
has *any* history row - which is every deal, from creation - because the
cascade would still be a `DELETE` on an append-only table. `delete_deal`
catches this and returns a clear message pointing at archive instead.
Archive (`archived_at`) is the real deletion path for a deal.

## 6. Won/lost transitions

`change_stage` classifies every transition through
`domain.deal_stages.transition()` (pure, tested with 6 cases in
`tests/test_domain.py`): landing on any non-open category stamps
`closed_at`; landing back on open clears it (a deal can be reopened - Phase
3 documents no restriction against it). Landing directly on a WON or LOST
stage **at creation time** (a backfilled historical deal, a same-visit
close) stamps `closed_at` from the first row too - found missing by a test
(`test_reopening_a_won_deal_clears_closed_at` failed until `create_deal`
was corrected to classify its own initial stage) and fixed before this
document was written, not after.

Won and lost transitions get their own audit action
(`crm.deal.won`/`crm.deal.lost`) and their own event, in addition to the
generic `crm.deal.stage_changed` - Task 40's explicit ask, because "which
deals did we win this month" should not require scanning every stage
change looking for the ones that happened to land on a won-category stage.

## 7. Optimistic concurrency

`Deal.version` (Task 46), the same mechanism `Subscription.version` already
uses. `update_deal` and `change_stage` both accept an optional
`expected_version` and raise `ConcurrentModification` on mismatch. Chosen
deliberately for `Deal` and not for `Company`/`Contact`/`Lead`: a deal is
the CRM record most plausibly edited by two reps racing each other (two
people moving the same deal to different stages after a call); the others
do not have an equivalently sharp lost-update story, and adding the token
everywhere without one would be complexity with no demonstrated risk.
