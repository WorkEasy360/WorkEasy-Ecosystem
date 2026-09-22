# 05 — Organization Selection

## The chooser (Task 5)

`GET /api/v1/organizations` — every organization the signed-in user actively
belongs to. The frontend (`/organizations/select`) auto-continues past it
when there is exactly one (`app/page.tsx`); shows the chooser for more than
one; shows a safe, explicit empty state for zero ("ask an administrator to
invite you" - never a redirect loop, never a bare error).

**No organization id is ever trusted from the browser as an assertion of
membership.** Every endpoint that acts within an organization
(`/orgs/<slug>/...`) re-resolves membership from the session's user id via
`TenancyMiddleware`, independent of what the frontend last rendered.

## §1 The RLS constraint this query runs into

`Membership` carries row-level security keyed on exactly one organization at
a time (`organization_id = current_setting('app.organization_id')`, `FORCE
ROW LEVEL SECURITY`), and the application database role is deliberately
`NOBYPASSRLS` (ADR-009). There is therefore no single SQL query that answers
"which organizations is this user in" - not an oversight, the isolation
model that protects every tenant's data makes a cross-tenant *filter*
structurally impossible from the app role, by design.

## §2 The only path RLS leaves open

`platform.organizations.selectors.organizations_for_user()` takes the same
path `all_organization_ids()` already established for background
reconciliation jobs: enumerate the (RLS-free, control-plane) `Organization`
table, and check membership inside each candidate's own database scope.
`ix_membership_user` (an index already present, leading with `user_id`) is
what keeps each per-organization check an index seek rather than a scan -
it does not, and cannot, make the query cross-tenant in one call.

Bounded by `max_organizations_scanned` (default 2000) so a very large
platform degrades - and emits a metric,
`organizations_for_user_scan_truncated_total` - rather than hangs.

## §3 Known limitation, honestly stated

This is O(organizations on the platform) per login, run synchronously in the
request. Acceptable at this project's current scale (a development/early-
stage environment - see [09-performance.md](09-performance.md) for the
stated test environment) and not acceptable indefinitely.

**Proposed for Phase 5, not built now:** a denormalised, RLS-exempt
projection table (`(user_id, organization_id, status)`, control-plane like
`Organization` itself), written alongside every membership change. Not built
in Phase 4 because doing it correctly means auditing every membership write
path to keep it synchronised - real scope the brief did not ask for, and a
projection that can silently drift from `Membership` is worse than an
honestly-bounded scan that is at least never wrong.

## §4 A gap this surfaced, pre-existing, out of Phase 4's scope

Manual verification (see [13-test-report.md](13-test-report.md) §3) found
that a freshly created organization's owner membership carries **no
permissions at all** until a role is explicitly assigned - `is_owner=True`
alone grants nothing in this codebase's RBAC model, and there is no
`platform/organizations` *service* (only selectors) to create an
organization and bootstrap its owner's role in one step. Every existing
test that needs an authorized member uses the `grant_permissions` test
fixture explicitly, which is the tell: production has no first-class
"create an organization" flow yet at all, only direct model construction in
test fixtures and (presumably) an as-yet-unbuilt onboarding path. This
predates Phase 4 - organization creation was never in this phase's task
list - and is recorded here because Phase 4's own manual verification is
where it became visible. Proposed as a Phase 5 prerequisite for the
organization-settings work to be genuinely useful to a real, freshly signed
up customer.
