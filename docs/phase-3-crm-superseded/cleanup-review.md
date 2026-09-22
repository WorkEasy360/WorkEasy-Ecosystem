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

# Lean-Code / Cleanup Review

**Status:** Accepted (Phase 3)

---

## 1. Method

Phase 3 is new code, not an accumulation carried across several
iterations, so this review is smaller in scope than Phase 2's own cleanup
pass (which removed things that had accreted over Phase 1). What follows is
what was actually found and changed, not an invented list - Task 61 is
explicit that nothing should be reported that was not verified.

## 2. Found and fixed during development, not left for this pass

These are correctness/design issues the test suite caught while writing
Phase 3, not lean-code findings - recorded in
[security-review.md](security-review.md) and
[concurrency-review.md](concurrency-review.md) rather than duplicated here.

## 3. Found and fixed: `deal_services.py` over the size guideline

`services/deal_services.py` reached 515 lines - past
`14-repository-structure.md`'s "service files over ~500 lines signals a
module doing too much" guideline. The deal-contact relationship functions
(`add_contact`/`remove_contact`, ~65 lines, no dependency on the
create/update/stage-transition logic the rest of the file contains) were
split into a new `services/deal_contact_services.py`. Two call sites
updated (`services/lead_services.py`, `api/deals.py`); one now-unused
import (`DealContactRole`) removed from `deal_services.py`. Verified:
`manage.py check` clean, `ruff check`/`ruff format` clean on all four
touched files, full CRM local test suite re-run green (see
[test-report.md](test-report.md)).

Result: `deal_services.py` now 446 lines; `deal_contact_services.py` 78
lines.

## 4. Found and fixed: a stray artifact in `requirements-dev.txt`

Unrelated to Phase 3's own code, found while setting up the environment to
build it: `requirements-dev.txt` had a trailing line consisting of a stray
`` `   ` `` (backticks and whitespace) with no newline before it, introduced
in the Phase 2 commit and never noticed because nothing parses that file
strictly enough to fail on it until an environment bootstrap script does.
Removed as a one-line fix before Phase 3 work began.

## 5. Checked and found clean

- **Unused imports**: `ruff check` (which includes `F401`) is clean across
  every file in `products/crm/` after the fixes noted in
  [test-report.md](test-report.md) - two were found and fixed during
  development (an incorrect `from django.db import ProtectedError` that
  does not exist there - it is in `django.db.models` - and a tag-service
  kwarg bug that happened to also leave no unused-import residue).
- **Duplicate services/selectors/validators**: none found. Each entity's
  CRUD lives in exactly one service module; the one piece of validation
  logic reused across entities (`custom_field_services.apply_custom_fields`,
  `ownership.assert_valid_owner`, `related_entity.assert_entity_exists`,
  `tag_services.add_tags`/`remove_tags`) is a single shared function each,
  not duplicated per entity.
- **Unnecessary abstraction layers**: `manifest.py` and `admin.py`, both
  present in the architecture document's illustrative tree, were
  deliberately **not** created - see
  [crm-domain-model.md](crm-domain-model.md) §2 for why each would have
  been an abstraction with no consumer.
- **Unnecessary models**: `CustomFieldValue` as a separate EAV-shaped table
  was considered and rejected in favour of a JSON column per entity - see
  [custom-fields-tags.md](custom-fields-tags.md) §1. No other model was
  drafted and then removed.
- **Unnecessary indexes**: every index added leads with `organization` and
  serves a filter/sort/lookup this phase's selectors actually perform
  (confirmed by reading every `selectors/*.py` filter/sort/order_by against
  every model's `Meta.indexes`); none was added speculatively for a query
  that does not exist yet. No index was found to be redundant or removed -
  there was nothing to measure against yet, consistent with "add indexes
  based on actual access patterns... avoid index proliferation" (Task 48)
  meaning *don't add ahead of need*, not *remove after the fact* for a
  freshly-written module.
- **N+1 queries**: see [performance-review.md](performance-review.md) -
  every list/detail/board selector was written with `select_related`/
  `prefetch_related` from the start and verified with pinned query-count
  tests, so none were found needing removal after the fact.
- **Duplicated shared platform functionality** (Task 59): grepped
  `products/crm/` for anything that could be reinventing authentication,
  RBAC, entitlements, file storage, notifications, audit, generic event
  infrastructure, subscription or billing logic. None found - every one of
  those concerns is consumed through `platform.*`/`integration.*` exactly
  as documented in [crm-domain-model.md](crm-domain-model.md) §5.

## 5. Dependencies

**None added or removed.** Phase 3 needed nothing beyond what
`requirements.txt`/`requirements-dev.txt` already provide (Django, Celery,
the existing test/quality tooling) - confirmed by `pip-audit --strict`
finding no new package and no new vulnerability (see
[test-report.md](test-report.md)).

## 6. What this review did not do

No performance-driven removal (nothing was slow enough yet to justify
removing anything for speed - see
[performance-review.md](performance-review.md)), and no speculative
cleanup of Phase 1/2 code: this phase touched exactly two pre-existing
files outside `products/crm/` for reasons unrelated to lean-code cleanup
(`platform/tenancy/migrations_helpers.py`, to add an optional
`constraint_name` parameter `add_tenant_fk` needed for the
stage-belongs-to-pipeline constraint, and
`backend/tests/architecture/test_architecture.py`, to extend two
allowlists the way every prior phase did when it needed to). Neither
touch was a cleanup of existing code; both are additive and backward
compatible with every existing caller.
