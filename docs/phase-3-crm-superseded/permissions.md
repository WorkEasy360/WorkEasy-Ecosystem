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

# CRM Permissions

**Status:** Accepted (Phase 3)

---

## 1. The catalog

Declared in `products/crm/permissions.py`, registered at `AppConfig.ready()`
exactly as every other module's permissions are
(`platform.authz.registry.permission_registry`). 30 permissions:

| Resource | Permissions |
|---|---|
| Company | `view`, `create`, `update`, `delete` |
| Contact | `view`, `create`, `update`, `delete` |
| Lead | `view`, `create`, `update` (also gates conversion), `delete` |
| Deal | `view`, `create`, `update` (also gates stage changes), `delete` |
| Pipeline | `view`, `manage` (create/reorder/archive pipelines and stages) |
| Activity | `view`, `create`, `update`, `delete` |
| Note | `view`, `create`, `update`, `delete` |
| Report | `view` (dashboards, KPIs, forecast) |
| Custom field | `view`, `manage` - **not** in the Phase 3 brief's minimum list, added because definitions are schema-shaping and span every entity type, closer in kind to pipeline administration than to any one resource's CRUD |
| Bulk | `manage`, implying the four resources' own `update` permissions |

Every `*.update`/`*.create`/`*.delete` implies its own `*.view` (you cannot
be granted the ability to change what you cannot see).

## 2. Deny by default, no `if user.is_admin`

Every service function's first call is `products.crm.authz.require_crm_access`,
which is `entitlements.require(...)` then `platform.authz.services.require(ctx, permission)`
- the same two-decision order the platform-wide `check()` docstring
specifies (entitlement before permission, because the remedies differ).
There is no scattered `if user.is_admin`/`if user.is_owner` check anywhere
in `products/crm/` - confirmed by inspection of every `services/*.py` file,
each of which reads `require_crm_access(ctx, PERMISSION, write=...)` as its
opening line.

## 3. None of these are `dangerous`

Unlike `commercial.catalog.manage` (changes what every tenant can buy) or
`platform.billing.manage` (an account-takeover target), every CRM
permission here only ever affects one organization's own sales data, and is
already gated by tenancy (RLS), entitlement, permission and (for the
record-changing ones) an audit entry. Requiring AAL 2 to archive a contact
would be step-up fatigue with no matching risk reduction - the same
reasoning `commercial.subscription.manage`'s own docstring gives for why
buying a subscription is not `dangerous` either, despite committing the
customer to money.

## 4. Not baked into any system role

`platform.authz.services.SYSTEM_ROLES["owner"]["permissions"] = "*"`
expands to every registered key, so an organization's owner automatically
gains every CRM permission the moment `permissions.py` is imported - no
CRM-specific change was needed for that. No other system role
(`admin`, `billing_manager`, `member`, `readonly`, `collaborator`) lists any
CRM permission, matching the precedent `commercial.subscription.manage`
already sets: an org's day-to-day administrator does not automatically
gain a new product's privilege surface just because the product was
installed. A CRM-using organization assigns a custom role (or extends
`admin`) explicitly, through the existing role-management surface -
Phase 3 adds no new mechanism for this, only new permission keys to grant
through the existing one.

## 5. Object-level rules

None registered. `platform.authz.services.register_object_rule` exists for
cases where "is this permission held" is insufficient and an object's own
state or ownership matters - CRM's tenant scoping (RLS + the scoped
manager) already makes "which records exist for you to act on" a tenancy
question, not an authorization one, and no CRM record has an ownership
model finer than "owner may differ from acting user, and any permitted user
may act on any record their tenant scope makes visible". If a future
requirement needs "only the owner or an admin may delete this deal", that
is the trigger for an object rule; none exists today because no requirement
asked for it.

## 6. Verified

`tests/test_entitlement.py` and `tests/security/test_crm_security.py` cover:
active/missing/non-writable entitlement, missing permission (denied, not
silently allowed), and that a permission check for an unregistered key
would raise `UnknownPermission` rather than deny quietly - not exercised
directly against a CRM key (every key here *is* registered), but the
platform mechanism itself is Phase 1/2-tested and CRM adds nothing that
bypasses it.
