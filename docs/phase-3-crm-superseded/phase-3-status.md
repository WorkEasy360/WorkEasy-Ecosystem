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

# Phase 3 — Exit Gate Assessment

**Date:** 2026-09-22
**Assessed by:** Implementation
**Repository state at start:** Phase 2 complete, committed (`34cf7a3`). 647
tests passing. No CRM, no product code beyond structural placeholders.

---

# PHASE 3 STATUS: **PASS**

---

## 1. Exit criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | CRM domain boundary exists | ✅ | `products/crm/{models,domain,services,selectors,api,tasks}/`; `lint-imports` 11/11 contracts kept |
| 2 | CRM entitlement gate works | ✅ | `authz.require_crm_access`, via `contracts.ports.entitlements.get_gate()`; 7 tests incl. active/suspended/expired/missing/memoization |
| 3 | CRM RBAC works | ✅ | 30 permissions, deny-by-default, no `is_admin` checks; `test_crm_security.py` |
| 4 | Companies work | ✅ | CRUD, archive/restore, owner validation, delete-blocked-by-reference |
| 5 | Contacts work | ✅ | CRUD, non-unique email, explicit Company relationship with single-primary invariant |
| 6 | Leads/Prospects work | ✅ | Lifecycle, conversion atomic/idempotent/auditable |
| 7 | Lead conversion is concurrency-safe | ✅ | 8-thread race → exactly 1 contact |
| 8 | Tags work | ✅ | 4 explicit join tables, idempotent attach, cross-tenant isolation verified |
| 9 | Custom fields work | ✅ | Normalised definitions, JSONB values, full type/bound validation, cross-tenant/cross-entity isolation |
| 10 | Pipelines work | ✅ | Default-pipeline partial-unique index, race-tested |
| 11 | Stages work | ✅ | Category-not-name semantics, probability bounded |
| 12 | Stage reorder is safe | ✅ | Deferred unique constraint + full-set lock; 8-thread shuffle race → valid permutation every time |
| 13 | Deals work | ✅ | No separate status field; `stage.category` is the only source of truth |
| 14 | Deal stage invariants hold | ✅ | Composite FK `(stage_id, pipeline_id)` makes cross-pipeline stages unrepresentable |
| 15 | Deal history is append-only | ✅ | DB grant + trigger; `IntegrityError` on direct mutation attempt, tested |
| 16 | Deal-contact relationships work | ✅ | `DealContact` with role, tested directly and via conversion |
| 17 | Activities work | ✅ | Task/call/meeting, one model, idempotent completion (race-tested) |
| 18 | Notes work | ✅ | Soft-reference pattern, justified and enforced at write time |
| 19 | Archive/restore behaviour is explicit | ✅ | Company/Contact/Lead/Deal/Pipeline/Stage; Note excluded by design (Task 24) |
| 20 | Bulk operations are bounded and safe | ✅ | 200-id cap, all-or-nothing, per-record + aggregate audit entries |
| 21 | Search/filter/sort are allowlisted | ✅ | No caller-supplied ORM passthrough anywhere in `products/crm/` |
| 22 | Pagination is bounded | ✅ | Keyset cursors, `MAX_PAGE_SIZE=100`, no offset pagination |
| 23 | Tenant RLS is verified | ✅ | 17/17 models covered automatically by the registry-derived suite (102 assertions) |
| 24 | Tenant scope restores correctly | ✅ | No CRM code opens a second nested scope; platform mechanism re-verified unchanged |
| 25 | Background jobs establish both tenant layers | ✅ | `services/system.py`, worker-shaped tests prove no inherited context |
| 26 | Worker-shaped tests pass | ✅ | `test_system_tasks.py`, 5/5 |
| 27 | Silent-empty-result risks are tested | ✅ | 9 categories checked, [tenant-isolation-review.md](tenant-isolation-review.md) §5 |
| 28 | CRM events/outbox are transaction-safe | ✅ | 8 schemas registered; every `events.publish` call inside the same transaction as its business write |
| 29 | Concurrency tests pass | ✅ | 5/5, real threads, real commits; 1 P0 found and fixed |
| 30 | Security tests pass | ✅ | 11/11, IDOR/mass-assignment/owner-manipulation/injection/bulk-abuse/bypass all covered |
| 31 | Query/performance budgets pass | ✅ | 5/5 pinned; 1 real N+1 found and fixed |
| 32 | Codebase cleanup is complete | ✅ | 1 oversized file split, 1 stray artifact removed, verified-clean sweep for dead code/duplication |
| 33 | No shared platform functionality was duplicated | ✅ | Grepped and confirmed; CRM consumes every platform service it needs |
| 34 | Architecture contracts remain intact | ✅ | `lint-imports` 11/11 kept; zero `products→commercial` imports |
| 35 | All prior Phase 0-2 regression tests still pass | ✅ | Re-run in full against the same database |
| 36 | No unresolved P0 | ✅ | 1 found (default-pipeline race), fixed, regression-tested |
| 37 | No Phase-3-blocking P1 remains | ✅ | See §18 |

## 2. Results

```
pytest (real PostgreSQL, RLS-enforced non-owner role)
    869 passed, 0 failed, 0 skipped in 318.69s (0:05:18)

ruff check          All checks passed
ruff format --check 293 files already formatted
mypy                Success: no issues found in 18 source files (contracts/, strict)
bandit -ll           0 high, 0 medium, 0 #nosec (27,976 lines scanned)
pip-audit --strict   No known vulnerabilities found
lint-imports         11 contracts kept, 0 broken
manage.py check      System check identified no issues (0 silenced)
check --deploy       6 issues - byte-identical to the Phase 1/2 baseline, all dev-settings-only
makemigrations       No changes detected
```

`check --deploy`'s 6 warnings (`security.W004/W008/W009/W012/W016/W018`) are
the same six Phase 2 already carried, all artefacts of running under
`config.settings.development` for this check rather than production - none
introduced by Phase 3.

## 3. New models and tables

17 tenant-scoped tables, 0 control-plane tables (CRM has no global reference
data of its own):

`crm_company`, `crm_contact`, `crm_company_contact`, `crm_lead`,
`crm_pipeline`, `crm_pipeline_stage`, `crm_deal`, `crm_deal_stage_history`,
`crm_deal_contact`, `crm_tag`, `crm_company_tag`, `crm_contact_tag`,
`crm_lead_tag`, `crm_deal_tag`, `crm_custom_field_definition`,
`crm_activity`, `crm_note`.

## 4. Migrations

Two, both forward-only and reversible:

| Migration | Contents |
|---|---|
| `crm.0001_initial` | 17 tables, all constraints/indexes declared in `Meta` |
| `tenancy.0005_crm_isolation` | RLS ×17, composite FKs ×23 (22 tenant + 1 business-invariant), append-only ×1 |

`makemigrations --check` is clean.

## 5. API endpoints

53 routes under `/api/v1/orgs/<slug>/crm/`, covering companies, contacts
(+ company links), leads (+ convert), pipelines (+ stages, reorder,
default, board), deals (+ change-stage, history, contacts), activities
(+ complete/cancel), notes, tags (generic attach/detach), custom fields,
bulk operations (archive/restore/reassign-owner/add-tags/remove-tags),
search, and dashboard (KPIs, forecast). Full list:
`products/crm/api/urls.py`.

## 6. Permission catalog additions

30 permissions across 10 resources (company, contact, lead, deal, pipeline,
activity, note, report, custom field, bulk) - see
[permissions.md](permissions.md) for the full list and the deny-by-default
verification.

## 7. Tenant/RLS findings

All 17 models covered automatically by the registry-derived isolation
suite; zero hand-written isolation tests needed. No gap found. See
[tenant-isolation-review.md](tenant-isolation-review.md).

## 8. Nested-scope findings

None new. CRM introduces no code path that opens a second, independent
tenant scope inside an already-open one - the Phase 2 Opus-audited fix in
`platform.tenancy.db._db_scope()` is what every CRM service relies on,
unchanged.

## 9. Background-task findings

`services/system.py::sweep_overdue_activities` is CRM's one cross-tenant
job. Worker-shaped tests confirm it establishes both isolation layers per
organization and that a version of the underlying model access which
forgot to would raise, not silently return empty. See
[tenant-isolation-review.md](tenant-isolation-review.md) §4 and
[failure-mode-review.md](failure-mode-review.md) §5.

## 10. Concurrency results

5/5 tests pass, real threads, real commits. One P0 found (default-pipeline
race) and fixed before this assessment. Full detail:
[concurrency-review.md](concurrency-review.md).

## 11. Security findings

11/11 tests pass. One gap found and fixed during development: tag
attach/detach had no authorization check at all in an early draft. Full
detail: [security-review.md](security-review.md).

## 12. CRM domain invariants

All stated in the Phase 3 brief are held - see §1 above for the
per-invariant evidence, and the individual domain docs
([companies-contacts.md](companies-contacts.md),
[leads-conversion.md](leads-conversion.md),
[pipelines-deals.md](pipelines-deals.md),
[activities.md](activities.md),
[custom-fields-tags.md](custom-fields-tags.md)) for the reasoning behind
each.

## 13. Custom-field design/results

Normalised definitions (`CustomFieldDefinition`), denormalised-but-validated
values (one JSON column per entity, not an EAV table). Full validation:
unknown keys, required fields, type mismatches, choice-option membership,
text/list length bounds, cross-tenant and cross-entity-type isolation - all
tested. See [custom-fields-tags.md](custom-fields-tags.md) §1.

## 14. Search/filter/pagination results

Search: bounded (200 chars), tenant-scoped, permission-aware per entity
type. Filter: explicit allowlist per entity, no passthrough. Sort:
allowlisted field names only, validated before reaching `order_by()`.
Pagination: keyset cursors throughout, `MAX_PAGE_SIZE=100`, no offset
pagination anywhere.

## 15. Performance/query results

5 query-count budgets pinned and passing. One real N+1 found (`pipeline_board`,
34 queries for 30 deals in one stage) and fixed (now 4, constant). Full
detail, including why production-scale load testing was not attempted
dishonestly: [performance-review.md](performance-review.md).

## 16. Failure-mode findings

No CRM write path holds a transaction open across a network call. Every
optional dependency (Redis, RabbitMQ, Celery workers, notification
provider) degrades gracefully - none can freeze a core CRM read or write.
Full detail: [failure-mode-review.md](failure-mode-review.md).

## 17. Event/outbox findings

8 schemas registered (`crm.company.created`, `crm.contact.created`,
`crm.lead.converted`, `crm.deal.created`, `crm.deal.stage_changed`,
`crm.deal.won`, `crm.deal.lost`, `crm.activity.completed`), each validated
at publish time, each published inside the same transaction as the state
change it describes. No consumer exists yet in Books/Inventory/Projects -
none was built, per the brief.

## 18. Unresolved P1/P2

**No Phase-3-blocking P1.** One P2, carried and documented, not a Phase 3
regression:

| # | Finding | Status |
|---|---|---|
| 1 | A suspended organization cannot be notified at all (`notify()` write-blocked by R-10) | Pre-existing Phase 2 finding (Opus audit P2-6). CRM's activity-assignment notification tolerates it explicitly (`OrganizationNotWritable` caught, assignment still succeeds). Not CRM's to fix; no new trigger reached |

## 19. Trigger review for carried debt

| Item | Trigger reached in Phase 3? |
|---|---|
| Rate limiting | **NO** - no public/unauthenticated CRM endpoint exists |
| Circuit breakers | **NO** - CRM calls no external HTTP dependency |
| Backpressure/load shedding | **NO** - unchanged from Phase 2 |
| Malware scanning | **NO** - CRM implements no file attachments |
| CSP | **NO** - CRM ships no HTML surface |
| SSRF helper | **NO** - CRM makes no outbound HTTP call |
| Entitlement-version query budget | **NO** - re-verified memoised, unchanged |
| AAL2 step-up | **NO** - no CRM permission is `dangerous` |

Full detail with evidence: [failure-mode-review.md](failure-mode-review.md) §2-4.

## 20. Architecture deviations / ADRs

**Zero new ADRs.** One structural addition, not a deviation: a runtime
registry (`register_gate`/`get_gate`) added to
`contracts/ports/entitlements.py`, so CRM can reach the entitlement gate
without importing `platform.entitlements` directly
(`commercial/provisioning/tests/test_provisioning.py::
test_products_import_nothing_commercial` asserts this by AST, including
over CRM's own test files). This is not a new pattern - it is the
*identical* mechanism `contracts/ports/observability.py` already documents
and uses for the same structural reason (a port needs an implementation
registered from below without an import edge in either direction). No
frozen decision changed; the Protocol in `contracts/ports/entitlements.py`
already specified what a product would call, and nothing previously wired
it up because no product existed to need it. See
[crm-domain-model.md](crm-domain-model.md) §2 and the module docstrings in
`contracts/ports/entitlements.py` and `platform/entitlements/apps.py`.

Two small, backward-compatible extensions to existing infrastructure,
neither a deviation:
- `migrations_helpers.add_tenant_fk` gained an optional `constraint_name`
  parameter (existing callers unaffected), needed because `Deal.stage_id`
  carries two composite foreign keys with different purposes.
- Two allowlists in `tests/architecture/test_architecture.py` extended
  (`IMPLEMENTED_PRODUCTS`, the `.unscoped` allowlist) - the same kind of
  entry every prior phase added when it needed one.

## 21. Lightweight/code-cleanup findings

One file split (`deal_services.py`, 515→446 lines, `deal_contact_services.py`
extracted at 78 lines), one stray pre-existing artifact removed
(`requirements-dev.txt`'s trailing garbage line, unrelated to CRM). No dead
code, no duplicate services/selectors/validators, no unnecessary
abstraction layers found - `manifest.py` and `admin.py` from the
architecture doc's illustrative tree were deliberately not created (no
consumer for either). Full detail: [cleanup-review.md](cleanup-review.md).

## 22. Dependencies added/removed

**None.** `pip-audit --strict` found no new package and no new
vulnerability.

## 23. Frontend results

**Not built.** No frontend foundation exists anywhere in this repository
(confirmed before any Phase 3 code was written: no `package.json`, no
`next.config.*`, no `/frontend` directory). Building one now would be the
"unrelated frontend architecture" Task 56 explicitly warns against. Full
reasoning: [frontend-review.md](frontend-review.md).

## 24. Files changed

- **~80** new Python files under `products/crm/` (models, domain, services,
  selectors, api, tasks, tests).
- **15** new documentation files under `docs/phase-3/` (this one is the 17th
  file with the 16 others it cross-references, all committed together).
- **5** other new files: 2 cross-cutting integration tests, 1 cross-cutting
  security test file, 1 new contracts event-schema module
  (`contracts/events/product_events.py`), 1 new tenancy migration.
- **13** modified files: `config/settings/base.py` (PRODUCT_APPS,
  CELERY_BEAT_SCHEDULE), `conftest.py` (crm_org/crm_two_orgs fixtures),
  `interfaces/api/v1/urls.py` (CRM route inclusion),
  `contracts/ports/entitlements.py` (runtime registry),
  `platform/authz/permissions.py` (EXAMPLE_PRODUCT_PERMISSIONS pruned),
  `platform/entitlements/apps.py` (gate registration),
  `platform/tenancy/migrations_helpers.py` (`constraint_name` parameter),
  `products/crm/__init__.py` (docstring), and five pre-existing test files
  updated as described in [test-report.md](test-report.md) §3.

Also committed as part of this phase, unrelated to CRM's own code: the
completed, previously-uncommitted Phase 2 work (commit `34cf7a3`), per an
explicit decision made before Phase 3 work began.

## 25. Commits

Not created as part of this assessment - commits were not explicitly
requested for the Phase 3 work itself (only the Phase 2 checkpoint was, and
that is committed as `34cf7a3`). The working tree is ready for review and
commit on request.

## 26. Decisions proposed to freeze for Phase 4

1. **`Deal` carries no denormalised status field.** Open/won/lost is always
   `stage.category`, read through the same `select_related` every list/detail
   view already performs.
2. **Custom field values are a JSON column per entity, validated against
   normalised definitions - never an EAV table.**
3. **Tags are explicit per-entity join tables, never a
   `GenericForeignKey`.**
4. **Soft references (`related_entity_type`/`related_entity_id`) are the
   sanctioned pattern for a record that may attach to one of several entity
   types**, enforced by an existence check at write time through the
   ordinary tenant-scoped manager - not a `GenericForeignKey`.
5. **A composite foreign key can enforce a non-tenant structural invariant**
   (`Deal.stage` belonging to `Deal.pipeline`), reusing
   `migrations_helpers.add_tenant_fk` with a non-`organization_id` second
   column and an explicit `constraint_name` when a column needs more than
   one composite FK.
6. **Products reach entitlements exclusively through
   `contracts.ports.entitlements.get_gate()`**, never by importing
   `platform.entitlements` - now a real, working, tested pattern rather
   than an aspirational rule with nothing to enforce it.
7. **Bulk operations are all-or-nothing in one transaction**, bounded at
   200 ids, and get one aggregate audit entry in addition to whatever their
   constituent per-record calls write.
8. **A default-pipeline-style "exactly one flagged row" invariant needs a
   full-set lock, not a lock on the row being changed** - the concurrency
   finding in [concurrency-review.md](concurrency-review.md) §3, worth
   remembering for any future "exactly one X per organization" feature.

---

## What bugs could still leave the suite green while producing silently
## incorrect CRM production state?

Not answered "none" - four residual categories, each with what limits it:

1. **A future CRM service added without calling `require_crm_access`
   first.** Nothing *structurally* forces every new service function to
   open with the entitlement/permission check - it is a convention, followed
   consistently today (verified by reading every `services/*.py` file), but
   a reviewer approving a future PR that skips it would ship a hole the test
   suite would only catch if someone wrote a test specifically checking that
   *new* function's authorization, which nothing guarantees. Mitigation:
   the pattern is uniform and grep-able (`require_crm_access(ctx,` appears
   as the first substantive line in 100% of current service functions),
   making a missing one visually obvious in review - but that is a review
   discipline, not a test.
2. **A future selector that forgets `select_related`/`prefetch_related`
   for a new field it starts exposing.** The query-count budgets pin
   *today's* fields; a selector change that adds a new related-object read
   without updating its budget will simply fail the pinned count (good) -
   but a selector *not* covered by `test_query_budgets.py` (most of them
   are not) can regress silently. The five pinned selectors are the
   highest-traffic ones, not all of them.
3. **A custom field type added later without updating both
   `domain/custom_fields.py`'s validator and `models/custom_field.py`'s
   choices consistently.** The two are separate enums
   (`CustomFieldType` is shared, so this specific case is actually
   guarded - a genuinely open risk would be a *new kind of validation rule*
   for an existing type, added to only one of the write path or the read
   path).
4. **Two concurrent bulk operations on overlapping id sets from the same
   organization**, e.g. one bulk-archiving companies 1-10 while another
   bulk-reassigns owner on companies 5-15. Neither operation takes a
   `select_for_update()` lock on the records it touches (each per-record
   service call does its own locking internally, but the bulk wrapper
   itself does not serialise against another *bulk* call touching an
   overlapping set) - the result is well-defined per row (whichever
   transaction's write to a given row commits last wins, ordinary MVCC), but
   not tested under concurrency the way pipeline defaults and stage reorder
   were. Genuinely untested, not merely undocumented.

None of these is speculative alarmism disconnected from what this phase
actually found: #1's discipline is exactly the kind of gap that produced
finding #4 in [test-report.md](test-report.md) §4 (tags had no check at
all) before it was caught by writing the security suite; #2 is exactly the
shape of the N+1 finding #5; #4 is the untested sibling of the concurrency
finding #1. Phase 4 (or a dedicated hardening pass) should treat these four
as its starting list, not rediscover them from zero.

---

**Phase 4 has not begun. No AI features, no Books/Inventory/Projects
integration, no reseller/white-label/affiliate work.**
