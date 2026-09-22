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

# Tenant Isolation Review

**Status:** Accepted (Phase 3)

---

## 1. Method

Every CRM model subclasses `platform.tenancy.models.TenantScopedModel`.
That single choice is what brings the entire Phase 0/1 isolation machinery
along automatically:

- **Layer 2** (`TenantScopedManager`): every query requires an active
  `TenantContext` or raises `MissingTenantContext` - never an empty
  queryset.
- **Layer 3** (PostgreSQL RLS): every CRM table has `ENABLE`/`FORCE ROW
  LEVEL SECURITY` and a `USING`/`WITH CHECK` policy matching
  `app.organization_id`, applied by
  `platform/tenancy/migrations/0005_crm_isolation.py::TENANT_TABLES`
  (all 17 tables).
- **Layer 4** (composite foreign keys): every tenant-to-tenant reference
  within CRM (22 of them - `Deal.pipeline`/`stage`/`company`,
  `CompanyContact.company`/`contact`, `DealContact.deal`/`contact`,
  `PipelineStage.pipeline`, `DealStageHistory.deal`/`from_stage`/`to_stage`,
  `Lead.converted_contact`/`converted_company`/`converted_deal`, and the
  eight tag join columns) has a composite `(child_id, organization_id)` FK
  referencing the parent's `(id, organization_id)`, making a cross-tenant
  pointer structurally impossible to store even if the id were somehow
  obtained (R-05).
- **A fifth, CRM-specific structural invariant**: `Deal.stage` also carries
  a *second* composite FK, `(stage_id, pipeline_id)` referencing
  `crm_pipeline_stage (id, pipeline_id)` - not a tenant boundary, the same
  mechanism applied to "a deal's stage belongs to its own pipeline"
  (Task 17). See [pipelines-deals.md](pipelines-deals.md) §3.

## 2. What this bought for free

The registry-derived suite in `tests/isolation/test_every_tenant_model.py`
discovers every `TenantScopedModel` subclass by introspecting the live
Django app registry (`apps.get_models()`), not a maintained list - so it
found and tested all 17 CRM models automatically, with **zero
CRM-specific isolation test code written**. Filtered to `crm.*`:

```
102 passed (scoped-manager-raises, unscoped-requires-grant,
             RLS-policy-complete, RLS-hides-cross-tenant-rows,
             no-rows-without-context, organization-leading-index,
             composite-FK-coverage - 7 assertions x ~17 models,
             two models exempt from the leading-index check where N/A)
```

`test_architecture.py::test_every_tenant_model_has_an_isolation_test`
cross-checks that this parametrised set matches the live model registry
exactly - nothing can be silently excluded from coverage by being added to
a list that forgot to keep up.

## 3. Nested tenant scope (Phase 2 Opus finding, re-verified for CRM)

Phase 2's most serious finding was that `SET LOCAL`/`set_config(...)`
persists across a **released savepoint** (only a savepoint *rollback*
undoes it), so a nested `transaction.atomic()` scope that did not restore
what it found on entry could leak its organization into the caller after
exiting. The fix lives in `platform.tenancy.db._db_scope()`, used by every
CRM service through the ordinary `with in_tenant(ctx):` /
`tenant_context(ctx), tenant_db_scope(ctx)` pattern - CRM introduces no new
nested-scope code path of its own (no CRM service opens a second,
independent tenant scope inside an already-open one), so no new instance of
the defect class was possible to introduce. The platform-level regression
tests for it (`tests/isolation/`) were re-run unchanged and pass; see
[test-report.md](test-report.md) for the full count.

## 4. Background job scope (Task 33/34)

`services/system.py::sweep_overdue_activities` is the one CRM code path
with no ambient tenant context (a `CrossTenantTask`). It establishes both
Layer 2 (`unscoped_access(reason=...)`) and Layer 3
(`organization_db_scope(organization_id)`) explicitly, per organization, in
a `with` block whose exit restores the prior (absent) scope automatically -
mirroring `commercial.subscriptions.system.system_scope` exactly, the
precedent this module was built to match. Worker-shaped tests
(`tests/test_system_tasks.py`) assert there is **no** context before and
after calling it, and that a version of the model access which forgot to
establish scope (a bare `Activity.objects.all()` with nothing entered)
raises `MissingTenantContext` rather than returning nothing.

## 5. Silent-failure categories checked (Task 35)

| Category | CRM-specific check | Result |
|---|---|---|
| RLS blocks query -> zero results read as "no data" | `test_wrong_organization_context_cannot_read_other_orgs_data`; every selector's `None`/empty return for a wrong-tenant id is asserted to mean "not found", never silently treated as authoritative by a caller | No instance found |
| Queryset evaluated after tenant scope exits | No CRM selector returns an unevaluated queryset across a scope boundary - every selector materialises a list/dict before returning | Not applicable by construction |
| Listener exception swallowed | No CRM event handler exists yet (Task 31/60: no consumer in Books/Inventory/Projects) - nothing to swallow an exception in | Not applicable yet |
| Worker eager vs. real-worker difference | `tests/test_system_tasks.py` calls the sweep with genuinely no context, not via `CELERY_TASK_ALWAYS_EAGER` | Verified equivalent |
| Bulk operation partially sees records | `test_bulk_operation_rejects_unknown_id_and_applies_nothing`, `test_bulk_rejects_cross_tenant_id` - the whole batch is refused, nothing partially applies | No instance found |
| Wrong-tenant context returns foreign rows | Every IDOR test in `tests/security/test_crm_security.py` | No instance found |
| Permission cache stale | Not CRM-specific; platform mechanism (`bump_permission_version`) untouched by this phase | Not applicable |
| Entitlement memo stale | `test_entitlement_check_is_memoised_per_request` confirms the memo is per-request-scope, not cross-request | Verified correct, not stale across requests |
| Empty search result caused by scope failure | `test_missing_tenant_scope_raises_not_denies` - a search/read attempted with no scope at all raises, it does not return an empty result set | No instance found |

## 6. Residual risk this review does not close

See [failure-mode-review.md](failure-mode-review.md) and the final
"What bugs could still leave the suite green" section of
[phase-3-status.md](phase-3-status.md) for categories that structural
testing cannot fully rule out (a future service added without going
through `require_crm_access`, for instance) - this document covers what was
built and verified, not a claim that no such category exists.
