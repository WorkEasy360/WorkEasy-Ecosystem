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

# Custom Fields and Tags

**Status:** Accepted (Phase 3)

---

## 1. Custom fields: normalised definitions, validated-not-EAV values

**`CustomFieldDefinition`** is normalised: one row per (organization,
entity_type, key), `field_type` from a closed set of nine
(text/long_text/integer/decimal/boolean/date/datetime/select/multi_select -
Task 11 explicitly asks for only the types genuinely needed, not a general
type system), `required`, `options` (choice types only, enforced by
`create_definition`), `is_active`, `sort_order`. Deactivated, never deleted
- deleting one would strand every value already stored under its key with
nothing left to validate it against, turning "invalid data" into
"undetectable data" without a trace.

**Values are not a second, EAV-shaped table.** Each of `Company`, `Contact`,
`Lead` and `Deal` carries its own `custom_fields` JSON column. Task 12
explicitly sanctions this: "a JSONB value container may be acceptable if
definitions remain normalized [and] writes validate against definitions" -
the alternative, one row per field per entity, is the "uncontrolled EAV
architecture" the same task explicitly forbids. Every write is validated
against the tenant's *active* definitions for that entity type
(`domain.custom_fields.validate_custom_fields`, called from
`services.custom_field_services.apply_custom_fields`, called from every
entity's `create_*`/`update_*`):

- Unknown keys rejected.
- Required-and-missing rejected.
- Type mismatches rejected (`bool` is explicitly rejected for `INTEGER` -
  the same "bool is a subclass of int" guard `Money.__post_init__` uses).
- `SELECT`/`MULTI_SELECT` values validated against the definition's own
  `options`; `MULTI_SELECT` deduplicates and is bounded at 50 items.
- Text fields bounded (500 / 10,000 characters), `MULTI_SELECT` bounded, and
  an entity may not define more than 100 custom fields - Task 53's input
  limits, enforced in `domain/custom_fields.py` so they hold regardless of
  which service calls it.

**Cross-tenant and cross-entity-type isolation is structural, not a
runtime check.** `specs_for()` builds the validation map from
`CustomFieldDefinition.objects.filter(entity_type=..., is_active=True)` -
the ordinary tenant-scoped manager, so a definition belonging to another
organization is not merely "checked and rejected", it is **not in the map
to begin with**. A definition for `Company` cannot be used to validate a
`Contact`'s values for the identical reason: the map is built per entity
type. Tested:
`test_custom_field_definition_scoped_to_entity_type`,
`test_custom_field_unknown_key_rejected`,
`test_deactivated_definition_rejects_new_writes`.

**Documented limitation (Task 12 asks for this to be stated, not solved):**
querying by custom field value is limited to whatever PostgreSQL's JSONB
containment operators give for free - no field-specific index exists, and
none was added speculatively. If a specific custom field becomes a common
filter or sort target, that is the measured trigger for a targeted
expression index, not a reason to add one now.

## 2. Tags: four explicit join tables, not a GenericForeignKey

`Tag` (tenant-owned, unique `(organization, name)`) plus `CompanyTag`,
`ContactTag`, `LeadTag`, `DealTag` - one small table per taggable entity
type, each with its own composite tenant foreign keys. A
`GenericForeignKey`'s `object_id` cannot itself be a database foreign key,
so a tag attached that way would carry no referential integrity and none of
the composite-tenant-FK protection every other CRM relationship has - a
cross-tenant or dangling attachment would be representable, and RLS would
only protect it by accident, at read time, on the `Tag` row itself rather
than on the attachment (Task 10).

**Get-or-create on first use.** `tag_services.add_tags` creates the tag row
if it does not already exist for this organization, then creates the join
row, tolerating `IntegrityError` on either as a no-op - attaching an
already-attached tag is idempotent, not an error. Tested:
`test_tag_attach_is_idempotent_and_creates_on_first_use`.

**Gated by the entity's own `update` permission**, not a separate tag
permission - tagging a company is an edit to that company, and Phase 3's
permission list names no distinct tag privilege. `tag_services._require_tag_access`
maps `entity_type` to `crm.<entity>.update` and calls
`require_crm_access` before either the create-tag or the attach/detach
step runs. This was missing in an early draft (the service performed the
write with no authorization check at all) and was found and fixed before
the corresponding endpoint tests were written - see
[security-review.md](security-review.md).

**Cross-tenant attachment is structurally impossible**, not merely
refused: `add_tags`'s own tag lookup/creation goes through the tenant-scoped
manager, so two organizations independently tagging a record `"vip"` each
get their **own** `Tag` row with that name - there is no shared row to
attach across a tenant boundary in the first place. Tested:
`test_tag_cross_tenant_attachment_impossible`.

## 3. Bulk tagging

`bulk_services.bulk_add_tags`/`bulk_remove_tags` (Task 25) apply to a
bounded, deduplicated id list in one transaction, rejecting the whole batch
if any id does not resolve in this tenant before attaching anything - see
[cleanup-review.md](cleanup-review.md) is not relevant here;
see the bulk-operations section of
[security-review.md](security-review.md) for the "reject mixed-tenant IDs"
finding this same all-or-nothing shape produces for every bulk operation,
not only tagging.
