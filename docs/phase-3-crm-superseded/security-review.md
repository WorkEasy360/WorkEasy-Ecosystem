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

# Security Review

**Status:** Accepted (Phase 3)

---

## 1. Method

`tests/security/test_crm_security.py` (11 tests) plus the security-relevant
cases embedded in the service-level suites (ownership validation,
permission denial, entitlement denial). Every test asserts behaviour
against real Postgres/RLS, not a mock.

## 2. IDOR / cross-tenant object reference

An id from organization A, presented by an authenticated, permitted actor
in organization B, is **never** distinguishable from a nonexistent id: the
tenant-scoped manager filters it out before any service-level "not found"
branch is reached, so every code path returns the identical
`CrmRecordNotFound` (API: identical 404 body) regardless of whether the id
is real. Tested for company, deal, and the company/contact linking path
(`test_idor_company_id_across_tenants_returns_not_found`,
`test_idor_deal_id_across_tenants`,
`test_contact_cannot_link_to_company_from_another_tenant`,
`test_deal_cannot_reference_company_from_another_tenant`).

## 3. Mass assignment

No CRM API handler uses a generic serializer or `fields = "__all__"`. Every
view in `products/crm/api/*.py` names its accepted fields explicitly via
`api/_parsing.py` helpers - a client cannot set `organization_id`,
`status` directly (archival goes through `archive_*`/`restore_*` actions,
never a plain field write), or any server-computed field by adding it to a
request body.

## 4. Owner manipulation

`owner_id` on every entity is validated against active membership of the
*acting* organization before it is accepted
(`services/ownership.py::assert_valid_owner`) - a real user id belonging to
another organization is refused with `CrmValidationError`, not silently
accepted because the id happens to exist globally
(`identity.User` is a global table). Tested:
`test_idor_owner_cannot_be_set_to_another_orgs_user`.

## 5. Pipeline/stage manipulation

- A deal cannot be moved to a stage outside its own pipeline - checked in
  the service and enforced structurally by a composite foreign key (see
  [pipelines-deals.md](pipelines-deals.md) §3).
- A stage reorder cannot smuggle a stage id belonging to a different
  pipeline into the requested ordering -
  `test_stage_reorder_rejects_ids_from_another_pipeline` confirms
  `reorder_stages` validates the submitted id set against exactly the
  target pipeline's own stages.

## 6. Custom-field injection

Unknown keys, wrong types, out-of-range choice values and oversized
text/lists are all rejected before a write reaches the database - see
[custom-fields-tags.md](custom-fields-tags.md) §1. No custom field value is
ever interpolated into a raw query anywhere in `products/crm/` (confirmed
by `test_no_raw_sql_built_with_f_strings`, the existing platform-wide
architecture fitness test, which continues to pass with CRM's code
included).

## 7. Search / sort / filter injection

- **Search**: bounded length (200 chars), `icontains` only, tenant-scoped
  by construction. Tested: `test_search_query_length_bounded`.
- **Sort**: every list selector validates the requested field against an
  explicit `frozenset` allowlist before it becomes part of an `order_by()`
  call (`selectors/pagination.py::validate_sort`) - a caller-supplied
  string never reaches the ORM as a column name unchecked.
- **Filter**: `status`, `owner_id`, `tag`, `pipeline_id`, `stage_id` are the
  only filterable dimensions per entity, each parsed to its correct type
  (UUID, enum) before use - no generic `**request.GET` passthrough exists
  anywhere in `products/crm/api/`.

## 8. Bulk-endpoint abuse

- Bounded: `MAX_BULK_IDS = 200`, enforced before any query runs.
  Tested: `test_bulk_ids_bounded`, `test_bulk_ids_reject_unbounded_batch`.
- All-or-nothing: one bad or cross-tenant id refuses the entire batch,
  never a partial application. Tested:
  `test_bulk_operation_rejects_unknown_id_and_applies_nothing`,
  `test_bulk_rejects_cross_tenant_id`.
- Every individual record touched by a bulk call still goes through its own
  `require_crm_access` call (defence in depth, not only the bulk entry
  point's own `crm.bulk.manage` check).

## 9. Permission / entitlement bypass

- No entitlement, entitled-but-no-permission, and entitlement-lapsed
  (suspended/expired) are all refused with the correct error type
  (`EntitlementDenied`/`PermissionDenied`), distinguishable from a tenancy
  failure (`MissingTenantContext`), never conflated. See
  [permissions.md](permissions.md) and `tests/test_entitlement.py`.
- Tag attach/detach was found, during development, to have **no**
  authorization check at all in an early draft - `tag_services.add_tags`
  performed the write unconditionally. Found by writing the security test
  suite (not by a user report), fixed by routing every tag mutation through
  `_require_tag_access` before any query runs. No regression test existed
  to catch this specific gap before the fix because the gap predated the
  test; the fix is covered by the same tag tests that would have caught it
  had they existed first, which is the argument for writing the
  security-focused suite as part of the phase rather than after it.

## 10. Archived-record modification

Archival is a status change, not a freeze - `update_deal` on an archived
deal succeeds and changes the record (`test_archived_deal_can_still_be_updated_not_frozen_silently`).
This is a deliberate choice, not an oversight: Phase 3 defines archive as
"hidden from default views, not locked", and a silent no-op on an archived
record's update would be exactly the kind of quiet, undetectable failure
Task 35 asks to be tested against - the alternative (refusing writes on an
archived record) was considered and rejected because nothing in the brief
asks for it and it would need its own error path, its own tests, and a
clear story for "how do I fix a typo in something I archived", none of
which exists.

## 11. Race-based authorization bypass

Every authorization decision is made fresh, inside the transaction that
performs the write, immediately before the write - there is no
check-then-act window where a permission or entitlement could be revoked
between the check and the effect, because both happen in the same database
transaction the row lock (where one is taken) also covers.

## 12. Not covered by this review

- **Rate limiting on CRM endpoints**: see
  [failure-mode-review.md](failure-mode-review.md) Task 54 for the trigger
  assessment (not reached).
- **CSRF/session-fixation/transport security**: unchanged by Phase 3,
  platform-level, already covered by Phase 1/2 review.
