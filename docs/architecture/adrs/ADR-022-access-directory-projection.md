# ADR-022 — The organization access-directory projection

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Architecture
**Depends on:** [ADR-009](ADR-009-row-level-security-for-tenant-isolation.md), [ADR-020](ADR-020-signing-keys-and-the-control-plane-code.md)

---

## Context

D-013 (debt register) measured the Phase 4 organization chooser against
real PostgreSQL: 10 organizations on the platform cost 56 queries / 43 ms;
100 cost 506 / 355 ms; 500 cost 2,506 / 1,743 ms; past a 2,000-organization
scan cap the answer becomes silently *wrong* rather than merely slow - a
user's own organization can disappear from their own chooser. The cause is
structural, not an oversight: `Membership` carries row-level security keyed
on one organization at a time, and the application role is `NOBYPASSRLS`
(ADR-009), so there is no single SQL query that can answer "which
organizations does this user belong to" from that table. The only path RLS
leaves open - enumerate every organization and check membership inside each
one's own scope - is what `organizations_for_user` did, and it is O(platform
size), not O(user's own memberships), which is the wrong variable to be
paying for at all.

Phase 4's own audit specified the fix rather than building it
(docs/phase-4/05-organization-selection.md §3): a denormalised, RLS-exempt
projection table, written alongside every membership change, deferred
because "doing it correctly means auditing every membership write path to
keep it synchronised" - real scope outside that phase's brief. That audit
is now done (§3 below): in this codebase, before Phase 5, the only
production code path that ever created a `Membership` row did not exist -
organization creation was itself a Phase 5 deliverable, invented alongside
the projection it feeds. Every membership-mutating write path is therefore
either brand new (owned by `platform.onboarding`) or a test fixture
(`conftest.organization_factory`), both updated in the same change that
adds the projection.

## Decision

### 1. `UserOrganizationAccess`: a projection, not a cache

`platform/organizations/models.py` gains `UserOrganizationAccess`
(`organizations_user_access`): `(user, organization)` unique, denormalised
`organization_slug`/`display_name`/`status`, a soft-reference
`membership_id`, `is_owner`, `updated_at`. Deliberately narrow (Task 17): no
permission set, no subscription, no entitlement - a wider table is more
that can drift, and none of that data answers "what should the chooser
show".

**It answers discovery, never authorization** (Task 19, the property this
ADR exists to make load-bearing rather than a slogan): every tenant-scoped
request continues to resolve membership from scratch through
`platform.tenancy.resolution`, independent of anything this table says. A
stale or - in the limit - maliciously written row here can make an
organization wrongly *appear*; it cannot make it wrongly *actable upon*,
because no authorization decision anywhere in the codebase reads this
table. That single property is what makes every other decision below safe.

### 2. Not RLS, not `TenantScopedModel` - protected like `Organization` itself

The one query this table exists to answer - every organization for one
user - is cross-tenant by definition. RLS keyed on `organization_id` would
force it back into exactly the one-organization-at-a-time posture D-013
exists to remove: the same reason `organizations_for_user`'s old scan and
`platform.organizations.resolver`'s tenant-bootstrap query both read
`Organization` unscoped rather than through RLS. `UserOrganizationAccess`
joins `Organization`, `identity.User`, `ecosystem.AuthorizationCode`/
`PendingAuthorization` and `organizations.OrganizationCreationRequest`
(Phase 5, added alongside it) on the `TENANT_SCOPE_EXEMPT` list in
`platform/tenancy/checks.py` (W360.E001) - each protected by an explicit,
narrow condition instead of the tenant isolation layer meant for a tenant's
own business data.

The condition here: **every read goes through
`platform.organizations.selectors.organizations_for_user(user_id, ...)`,
and `user_id` is always the caller's own, server-verified session identity
- never accepted from a client parameter, anywhere.** There is no endpoint
in this codebase that takes a `user_id` from the request and passes it to
this selector. An IDOR would require the *application code itself* to pass
another user's id, which is a code-review-visible defect, not a request an
attacker can construct - the same shape `platform.tenancy.resolution`
already relies on for the impossibility of an organization-enumeration
oracle. `backend/tests/security/test_onboarding_security.py` asserts this
directly: two users, two organizations, and User A's chooser response
never names User B's.

### 3. Synchronous, in the same transaction - not eventually consistent

Every write path that changes membership (currently: `create_organization`
and `remove_member`, both in `platform.onboarding.services`) calls
`platform.organizations.projection.sync_access_for_membership` /
`remove_access_for_membership` **inside the same database transaction** as
the `Membership` row it mirrors. There is no queue, no async worker, no
propagation delay to document, because there is no step between "the
membership changed" and "the projection reflects it" - they commit
together or roll back together. If a future write path needs to be
asynchronous (a bulk import, say), it must document its own consistency
window explicitly rather than silently inheriting this one - see
docs/phase-5/00-overview.md.

### 4. Reconciliation: drift is reported; repair is an explicit operator step

`platform.organizations.projection` also provides
`rebuild_access_projection` (idempotent, bounded-batch, one transaction per
organization; a converged row is not rewritten) and
`check_access_projection_drift` (read-only: missing / stale /
wrong-metadata rows, never repaired by the checker itself).

*Amended in the Phase 5 completion pass (2026-09-23):*

- Rebuild reads each organization and its active memberships
  `FOR NO KEY UPDATE`, so it serialises with a concurrent removal or
  suspension instead of re-inserting a row for a membership removed a
  moment earlier (a real race, proven with threads in
  `tests/integration/test_phase5_concurrency.py`).
- Rebuild deletes only when explicitly asked (`--prune-stale`), and then
  only rows whose authoritative membership is not active - the one
  divergence decidable from source-of-truth state alone. Without it, stale
  rows are reported by the drift check, as before. Neither mode affects
  authorization, which never reads this table.
- The initial backfill is a data migration
  (`organizations.0003_backfill_access_projection`), not an operator step:
  the upgrade rehearsal showed that without it every pre-existing user sees
  an empty chooser and is routed to onboarding.

## Consequences

- **Positive.** Chooser query count is now bounded by page size, not
  platform size - one indexed query per page, keyset-paginated exactly like
  `membership_summaries`. A user with 500 memberships is never silently
  truncated (the old scan's cap); they get pages.
- **Positive.** The authorization boundary is unchanged and independently
  testable: nothing about this table weakens `platform.tenancy.resolution`,
  and the test suite proves selection still revalidates membership even
  when the projection is (deliberately, in a test) stale.
- **Accepted cost.** A second place membership state is written - exactly
  the risk Phase 4 declined to take on without auditing every write path
  first. That audit is §3 above; the ongoing discipline is that any new
  membership-mutating code must call the sync functions in the same
  transaction, and `check_access_projection_drift` exists specifically to
  catch a future path that does not.
- **Naming.** `products/`, the reserved empty package, remains untouched -
  this is platform-only, tenant-adjacent read-model work, not a product
  concern.

See also: [docs/phase-5/00-overview.md](../../phase-5/00-overview.md),
[docs/phase-5/phase-5-status.md](../../phase-5/phase-5-status.md).
