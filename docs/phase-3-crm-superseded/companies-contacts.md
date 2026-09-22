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

# Companies, Contacts and their relationship

**Status:** Accepted (Phase 3)

---

## 1. The one distinction that matters

**`organizations.Organization`** is the Workeasy360 *customer* - the tenant,
the thing every row in this codebase is scoped to.

**`crm.Company`** is a business *that tenant's own customer sells to*. It is
CRM data, owned by one organization, about a third party the organization
has no authority over.

Every docstring in `models/company.py` and every variable name in
`services/company_services.py` says "organization" for the tenant and
"company" for the CRM record, on purpose - conflating the two in code is
exactly the mistake this document exists to prevent a future contributor
from making by accident.

## 2. Company

Deliberately narrow: `name`, `domain`, `phone`, `description`, an embedded
address (`address_line1/2`, `city`, `state`, `postal_code`, `country`),
`status` (active/archived), `owner`, `custom_fields`. No separate address
model or shared `contracts/values/address.py` type - no other module needs
to agree on a company's address shape, and nothing in `docs/architecture/`
defines one yet. If a second module later needs the same shape, that is the
trigger to extract it; doing so speculatively now would be an unowned
abstraction with one caller.

**Ownership.** `owner` is a foreign key to `identity.User` (on the W360.E003
cross-module FK allowlist), not to `organizations.Membership` - products may
not hold that FK at all (ADR-014, narrowed to `platform`/`commercial`). "Is
this user an active member of this organization" is therefore an
*application*-level check (`services/ownership.py::assert_valid_owner`),
run before every create/update that sets an owner. Tested:
`test_company_owner_must_be_active_member`,
`test_idor_owner_cannot_be_set_to_another_orgs_user`.

**Deletion.** Archive (`status`, `archived_at`) is the everyday path. Hard
delete exists (`crm.company.delete`) but is refused with a clear
`CrmValidationError` the moment any `Deal` references the company -
`Deal.company` is `on_delete=PROTECT`, deliberately (not `SET_NULL`): a
deal's company association is a business fact, and silently nulling it on
deletion is exactly the "archival does not corrupt references" invariant
Phase 3 requires. Tested: `test_delete_company_blocked_when_deal_references_it`,
`test_delete_company_succeeds_with_no_references`.

## 3. Contact

`first_name`, `last_name`, `email`, `phone`, `title`, `status`, `owner`,
`custom_fields`. **`email` carries no uniqueness constraint** - Phase 3 Task
6 is explicit that email is not globally unique and that "prevent legitimate
CRM data" is the wrong trade to make for deduplication, which is a human,
administrative decision. Tested: `test_contact_email_is_not_unique`.

## 4. The Company <-> Contact relationship

**Single source of truth: `CompanyContact`.** There is no
`Contact.primary_company` foreign key sitting alongside it. Two reasons:

1. A foreign key and a join-table flag both claiming to say "the primary
   company" is two places that can disagree, and the failure mode (they
   silently drift) is worse than the join query it would save.
2. Task 7 asks for "a contact belongs to multiple companies" to be modelled
   explicitly - `CompanyContact` already is that model; a redundant FK adds
   nothing a selector can't compute.

`CompanyContact.is_primary` is enforced to be unique per contact by a
**partial unique index** (`UniqueConstraint(fields=["contact"],
condition=Q(is_primary=True))`) - the database, not application code,
refuses a second primary. Linking a contact to a new primary company
atomically clears the old flag first (`services/contact_services.py::link_company`).
Tested: `test_link_company_enforces_single_primary`.

## 5. Isolation

Both models, and `CompanyContact`, are `TenantScopedModel`s, RLS-enabled
(`platform/tenancy/migrations/0005_crm_isolation.py`), and automatically
covered by the parametrised registry-derived suite in
`tests/isolation/test_every_tenant_model.py` - no isolation test was
hand-written for either model; the 7-assertion suite (scoped-manager-raises,
unscoped-requires-grant, RLS-policy-complete, RLS-hides-cross-tenant-rows,
no-rows-without-context, organization-leading-index,
composite-FK-coverage) runs against every one of them by construction.
