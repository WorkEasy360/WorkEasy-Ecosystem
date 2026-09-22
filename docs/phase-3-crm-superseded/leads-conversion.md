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

# Leads and Conversion

**Status:** Accepted (Phase 3)

---

## 1. Why Lead is a separate model

`Lead` is not `Contact` with an extra status value. Its fields
(`company_name` as free text, no relationship tables, no custom-field
history worth preserving before qualification) and lifecycle (new →
contacted → qualified/unqualified → converted, terminal) are genuinely
different from an established contact's, and overloading `Contact.status`
to imitate a lead pipeline would force every contact query, everywhere, to
filter out not-yet-qualified rows forever (Phase 3 Task 8).

`LeadStatus.CONVERTED` is terminal and one-way: `update_lead` refuses to set
it directly (`services/lead_services.py::update_lead` raises
`CrmValidationError` if asked to), and refuses to edit a lead that already
is converted - the conversion service is the only path to that state.

## 2. Conversion: atomic, idempotent, auditable (Task 9)

One transaction (`services/lead_services.py::convert_lead`) does all of:

1. Optionally create (or reuse an existing) `Contact`.
2. Optionally create (or reuse an existing) `Company`, and link the contact
   to it as primary.
3. Optionally create a `Deal` in the organization's default pipeline (or a
   named one), with the new contact attached via `DealContact`.
4. Stamp the lead `status=CONVERTED`, `converted_at`, and the three
   `converted_*` foreign keys.
5. One audit entry (`crm.lead.converted`) and one domain event
   (`crm.lead.converted.v1`, carrying whichever of `contact_id`/`company_id`/
   `deal_id` were actually produced).

All in the transaction started by `convert_lead` itself - a lead is never
left half-converted, because there is no commit between any of these steps.

### Two independent idempotency guards

1. **The idempotency ledger** (`integration.idempotency`), for a retried
   call with the *same* key - a client timeout followed by a retry.
2. **A row lock plus a status check**, for two *different* callers
   converting the same lead with two different keys. Whichever transaction
   wins `Lead.objects.select_for_update()` converts; the other, once
   unblocked, finds `status == CONVERTED` already and returns that result
   instead of converting a second time.

Neither guard alone covers both cases - a ledger keyed only on a
caller-supplied string cannot dedupe two different callers who never agreed
on a key, and a row lock alone does not survive a genuine retry racing
itself faster than the first attempt's transaction commits. Both are
present because each closes a gap the other leaves.

**Tested** (`tests/test_lead_conversion.py`):
`test_repeated_conversion_with_same_key_is_idempotent`,
`test_repeated_conversion_with_different_key_does_not_duplicate`, and, under
real concurrency with 8 threads racing the same lead
(`tests/integration/test_crm_concurrency.py::test_concurrent_lead_conversion_creates_exactly_one_contact`):
exactly one `Contact` row survives regardless of how many threads convert
"simultaneously".

## 3. Choices at conversion time

- `existing_contact_id` **or** `create_contact` (both is refused, neither is
  the "just stamp the lead, nothing else" case).
- `existing_company_id` **or** `create_company`, needing `company_name` from
  either the call or the lead's own free-text field.
- `create_deal`, resolving to the org's default pipeline and its first
  stage unless told otherwise (`deal_services.default_pipeline_and_stage`),
  reused by both `convert_lead` and the plain `create_deal` API so "open a
  deal" needs no arguments in the common case.

Every branch reuses the ordinary `company_services.create_company`/
`contact_services.create_contact`/`deal_services.create_deal` functions
rather than constructing rows directly - so a company created via
conversion gets the identical validation, audit entry and
`crm.company.created` event a company created directly would, and a caller
without `crm.company.create` (say) is refused exactly as they would be
outside conversion.

## 4. What was tested and found

- **Wrong tenant**: converting an id from another organization is a 404
  (`CrmRecordNotFound`), not a 403 - the row is invisible before permission
  is even relevant (`test_convert_wrong_tenant_lead_not_found`).
- **Missing permission**: a membership with `crm.lead.view` but not
  `crm.lead.update` is refused (`test_convert_missing_permission_denied`).
- **Rollback midway**: not separately tested with an injected failure, but
  structurally guaranteed - every side effect happens inside the one
  transaction `convert_lead` opens, so any exception (a validation failure
  partway through, a database constraint violation) rolls back everything,
  including the lead's own state.
