# ADR-023 — `platform/onboarding`: a new P3 layer for organization creation

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Architecture
**Depends on:** [ADR-021](ADR-021-launcher-access-decision-and-seat-projection.md)

---

## Context

Phase 4's own audit named organization creation as unbuilt
(docs/phase-4/05-organization-selection.md §4): no service existed to
create an `Organization`, bootstrap its owner's `Membership`, and grant
that owner the "owner" role in one step - only direct model construction in
test fixtures. Building it in Phase 5 means one use case touching four
platform modules that, by design, do not import each other:

- `organizations` (P1) - the `Organization`/`Membership` rows themselves.
- `identity` (P1) - validating the acting `User`. A **sibling** of
  `organizations`, not a lower layer, so F-5 ("same-layer imports inside
  `platform/` are forbidden") applies exactly as it would to an upward one.
- `authz` (P2) - assigning the system "owner" role.
- `audit` (P2) - recording the creation event.

None of this is new architecture in kind: `platform/ecosystem` already
composes `authz` (staff + dangerous permissions), `entitlements`, `audit`,
`organizations` and `identity` for the OIDC flow, and sits at P3 precisely
because it does. `platform/launcher` composes `ecosystem` in addition to
everything `ecosystem` composes, and sits at P4 for the identical reason
(ADR-021). Organization creation is the same shape - "one use case needs
several P0-P2 modules that do not import each other" - encountered for the
fourth time.

## Decision

**`platform/onboarding` joins P3**, alongside `ecosystem`
(`.importlinter`'s `platform-internal-layers` contract, `layers =` row 2).
It holds `create_organization` (Tasks 2-13) and `remove_member` (the
minimal membership-removal path the projection's removal sync and the
large-membership acceptance test need - see
`platform/onboarding/services.py`'s own docstring for why a full invitation
system is explicitly not built here, Task 32).

Two consequences of choosing P3 over the alternatives considered:

1. **Not inside `platform/organizations`.** The layer violation import-
   linter caught during implementation (`organizations.services` importing
   `authz.models`/`identity.models`/`audit.services`) is exactly the signal
   this ADR responds to, not a bug to route around locally. `organizations`
   keeps only what is genuinely its own: `Organization`/`Membership` rows,
   the `OrganizationCreationRequest` idempotency ledger (organizations'
   *own* model, so claiming it needs nothing from another module), and the
   access-directory projection (ADR-022, same module, same reasoning).
2. **Not a new `contracts/` port.** The port pattern
   (`contracts/ports/events.py`, `signing.py`) exists to cross the
   `platform`/`commercial` boundary a product must never see across, or to
   let a strictly lower layer reach a strictly higher one it structurally
   cannot import (`platform/tenancy` resolving through `organizations`,
   `platform/organizations` publishing through `integration/events` -
   both real Phase 5 additions, both ports). `onboarding`, `authz`,
   `identity`, `audit` and `organizations` are all platform code with
   nothing to hide from each other; the missing piece was never a
   translation boundary, only an orchestrator sitting above all of them -
   exactly what a new layer, not a new port, provides.

**Boundary discipline inside the new module.** `onboarding` still does not
reach into `authz`'s or `organizations`' tables directly for anything it
does not itself own (F-6): it calls `authz.services.assign_system_role`
(new - a selector-shaped write, not a raw `RoleAssignment.objects.create`)
and `organizations.services.create_organization_and_owner_membership` /
`mark_membership_removed`, each returning the rows or raising the
validation error `onboarding` needs, never a raw queryset over another
module's model.

## Consequences

- **Positive.** The layering rules did their job during implementation -
  caught the violation before it shipped, not in review. That is the
  intended feedback loop (ARCHITECTURE.md §4: "A PR that violates one does
  not merge"), demonstrated rather than only asserted.
- **Positive.** Every future "compose several platform modules for one use
  case" need has four load-bearing precedents to follow (`ecosystem`,
  `launcher`, `onboarding`, and the pattern itself), rather than each
  needing its own justification from scratch.
- **Accepted cost.** A fifth platform app, for what is - by line count - a
  small module. Judged worth it: the alternative (importing across siblings
  or upward, "just this once") is the exact defect class F-4/F-5 exist to
  make impossible to add by accident, and `platform/onboarding` genuinely
  is where a first-run-account-to-usable-organization use case belongs, not
  an implementation-detail workaround.

See also: [docs/phase-5/00-overview.md](../../phase-5/00-overview.md), `.importlinter`.
