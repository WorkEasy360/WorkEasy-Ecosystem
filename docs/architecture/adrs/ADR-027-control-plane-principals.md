# ADR-027: Staff and partner principals act through one authorization check, in an explicit scope

**Status:** Accepted
**Date:** 2026-09-24
**Deciders:** Architecture
**Depends on:** [ADR-020](ADR-020-signing-keys-and-the-control-plane-code.md)

---

## Context

Until Phase 7 every request had one shape: a user acting as a member of one
organization. Phase 7 adds two actors that are not that: **Workeasy360 staff**
(who act across customers) and **partner users** (who act for a partner
account and, through it, on customers they are related to). Three questions
decide whether adding them is safe:

1. Do they get a second authorization system?
2. How does a request that is not "inside one organization" get past row-level
   security without RLS being relaxed for everyone?
3. What stops a partner reaching a customer it merely *knows the id of*?

## Decision

**One `authz.check`, more principals.** `TenantContext` gains `principal`
(`member`, `staff`, `partner`) and `partner_id`. `authz.check` is still the only
place a permission is decided; `effective_permissions` branches on the principal
through resolvers registered at import time (`register_principal_resolver`), so
`platform/` never imports `commercial/`.

- **Staff** hold `StaffGrant` rows (`platform.staff_*` permissions), each with a
  reason, an optional expiry and an actor. Grants are read with **no cache**, so
  revocation is immediate. `end_grant` takes locks over all security-admin
  holders in id order, so the last security administrator cannot be removed by
  two concurrent revocations.
- **Partners** hold a *capability set* and a member role; see
  [ADR-028](ADR-028-partner-capability-model.md).
- Every permission that can change money, access or authority is `dangerous`
  and needs AAL 2 (`session_aal`, `record_step_up`) - for staff and partners
  alike. The browser hides controls; the backend is authoritative.

**An explicit scope, never an ambient one.** Control-plane code enters
`organization_scope(org_id)` (one customer, through `set_config(..., true)` so it
ends with the transaction) or `acting_scope` for control-plane tables. The
`@in_scope` decorator makes the scope part of a function's contract. RLS is
never relaxed; a staff request that touches one customer enters that
customer's scope exactly as a member request does.

**Partners reach customers only through a live link.** A
`PartnerOrganizationLink` (locked by `lock_active_link` inside the transaction)
is the only path from a partner to an organization. A route taking an
organization id resolves it *through the link* and answers 404 - not 403 - when
there is none, so ids are not an oracle.

**Trusted contexts for jobs.** Scheduled work builds its own context
(`platform_context`, the platform organization and system user); it never
borrows a request's.

**No second audit system, catalog or entitlement writer.** Staff actions write
`AuditEvent` through the existing service (actor kind `staff`, membership
`NULL`); entitlement overrides go through provisioning; pricing through the
existing commercial services.

## Consequences

- Control-plane tables (`partners.PartnerOrganizationLink`,
  `partners.PartnerOwnerInvite`, `provisioning.CustomerSummary`,
  `commissions.Referral`, `commissions.CommissionAccrual`) are exempt from
  W360.E001 (debt **D-019**), each for a stated reason. An architecture test
  pins the list.
- Adding a principal kind is a resolver plus a permission set - not a new check.

## Alternatives rejected

- **A separate admin service with a database role that bypasses RLS.** Simplest
  to write and the worst failure mode: one bug reads every tenant.
- **Impersonation ("act as the customer").** Loses the distinction between who
  acted and whose data it was, and cannot be audited honestly.
- **A partner `type` column.** See ADR-028.

## Amendment (Phase 7 final audit, 2026-09-24)

**Staff authority is a live `StaffGrant`, on every route.** The audit found the
older organization-scoped catalog, price-book and integration-registry routes
still gated by a role permission plus Django's `is_staff` - and the owner role's
`*` carries those permissions, so any staff identity that owned an organization
held them with no grant, beyond revocation. `authz.require_platform_staff` now
takes the exercised permission and requires it among the actor's live, uncached
grants; integration administration got its own keys
(`platform.staff_integration.view/manage`). Consequences:

- `is_staff` is a precondition set by an operator (`w360_bootstrap_staff`,
  `w360_staff_identity`), never an authority; a fitness test allowlists every
  production reader of it.
- A customer role never carries a control-plane key (filtered when the set is
  compiled, not only when roles are seeded), and a staff grant can only be a
  `platform.staff_*` key.
- Grants may carry an expiry (frozen with the grant's terms); the last
  *permanent* security administrator cannot be removed.
