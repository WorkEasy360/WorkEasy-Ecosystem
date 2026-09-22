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

# Activities and Notes

**Status:** Accepted (Phase 3)

---

## 1. One `Activity` model, three types

`activity_type` (task/call/meeting) is a discriminator on one model, not
three tables. All three share every field that matters here - owner,
related entity, subject, timing, status, priority, completion - and Task
21 asks for a shared model *or* explicit types "depending on the approved
domain model", not both. Splitting would triple the list, bulk-operation
and search code for zero behavioural difference between the three: exactly
the over-generalisation warning read backwards. `due_at` (tasks),
`start_at`/`end_at` (calls/meetings) all exist on every row; which are
populated is a function of `activity_type`, and every query already reads
the same row shape regardless.

## 2. Idempotent completion (Task 23)

`complete_activity` checks `status == COMPLETED` first and returns the
existing row unchanged if so - no second `completed_at` stamp, no second
audit entry, no second `crm.activity.completed` event. Verified two ways:
a direct call twice in a row
(`tests/test_activities_notes_tags_fields.py::test_complete_activity_is_idempotent`,
asserting exactly one `OutboxEvent` row after both calls) and under real
concurrency, 8 threads calling `complete_activity` on the same activity
simultaneously
(`tests/integration/test_crm_concurrency.py::test_concurrent_activity_completion_publishes_exactly_one_event`).

## 3. Assignment notifications (Task 41)

`create_activity`/`update_activity` notify the new owner
(`services/activity_services.py::_notify_assignment`) through
`platform.notifications`, using a template registered in
`products/crm/notifications.py` (`crm.activity.assigned`) - never a raw
string, matching the platform's server-side-template-injection defence.

**Deliberately not wired into the overdue-activity sweep.** `notify()`
writes through the ordinary tenant-scoped manager, which needs a real
*application* tenant context (Layer 2); the cross-tenant sweep in
`services/system.py` only ever establishes Layer 3 (`organization_db_scope`)
plus `unscoped_access`, because there is no real membership behind a clock
tick to attribute a notification to. Extending `notify()` to work from a
system context was out of scope for Phase 3 and would touch platform
internals `commercial/subscriptions/system.py` itself does not touch
either - it uses `audit.record_system()` and `events.publish()`, neither of
which is `notifications.notify()`.

**Known, accepted limitation, not CRM's to fix:** a suspended
organization cannot be notified at all (Phase 2 Opus audit finding P2-6) -
`_notify_assignment` catches `OrganizationNotWritable` and treats it as
best-effort, so an assignment inside a suspended organization still
succeeds; the assignee just is not emailed about it.

## 4. Overdue-activity sweep (Task 33)

`tasks/activity_tasks.py::sweep_overdue_activities` - a `CrossTenantTask`
(`requires_tenant=False`), dispatched on the `default` queue, calling
`services/system.py::sweep_overdue_activities`, which iterates every
organization from the control-plane `all_organization_ids()` selector and
enters `unscoped_access(reason=...) + organization_db_scope(org)` per
organization before counting overdue tasks - never a single unscoped scan
across tenants, and one organization's failure is logged and skipped
without aborting the sweep for the rest.

**Worker-shaped, not eager-shaped, tests**
(`tests/test_system_tasks.py`): the suite asserts
`current_tenant_or_none() is None` both before and after calling the sweep
function directly, and separately proves that calling
`Activity.objects.all()` with genuinely no scope at all raises
`MissingTenantContext` rather than returning an empty (and therefore
misleading) queryset - the exact silent-failure shape Task 35 asks to be
tested against.

## 5. Notes: not append-only, not an AuditEvent

`Note` supports ordinary update and hard delete - Task 24's archive-instead
of-delete list names Company, Contact, Lead, Deal, Pipeline and Stage
explicitly; Note is not on it, and nothing else holds a reference *to* a
note that deletion could corrupt.

A note is a person's authored commentary, edited deliberately; an
`AuditEvent` records what happened, including denied attempts, written by a
service at the point of change. The two stay conceptually separate even
though both end up "on" the same CRM record (Task 22).

## 6. The soft-reference pattern (Activity and Note)

Both `Activity.related_entity_type`/`related_entity_id` and
`Note.related_entity_type`/`related_entity_id` are a plain
`(CharField, UUIDField)` pair, not a `GenericForeignKey`. Justification: a
*single* foreign key cannot target four different tables with or without
`contenttypes` - a GFK's `object_id` gets no more composite-tenant-FK
protection than a soft reference does, so adopting one would add a
dependency on `django.contrib.contenttypes` for no structural gain.
Referential integrity is instead enforced at the point of write
(`services/related_entity.py::assert_entity_exists`), which looks the
target up through the ordinary tenant-scoped manager - already refusing a
wrong-tenant id - before a note or activity is allowed to attach to it.
Because Company/Contact/Lead/Deal are archived rather than deleted, the
target does not simply vanish under an existing reference; RLS continues
to protect cross-tenant reads regardless of the reference's shape. Tested:
`test_activity_related_entity_must_exist`.
