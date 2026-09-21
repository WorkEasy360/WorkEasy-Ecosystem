# ADR-014 — `Membership` as a Fourth Cross-Module FK Target

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture
**Amends:** [ADR-009](ADR-009-tenant-data-isolation.md), [02-dependency-rules.md](../02-dependency-rules.md) F-6

---

## Context

F-6 limits cross-module foreign keys to a frozen allowlist:

| Allowed FK target | Why |
|---|---|
| `platform.organizations.Organization` | the tenant discriminator |
| `platform.identity.User` | actor attribution |
| `platform.files.File` | attachment references |

> Everything else is a soft reference. These three are frozen by ADR-009 and
> adding a fourth requires an ADR.

Implementation surfaced an internal inconsistency: **Phase 0's own model
definitions require a fourth.**

- [04-identity-authz.md](../04-identity-authz.md) §4.4:
  `RoleAssignment.membership = FK(Membership, on_delete=CASCADE)`
- [06-entitlements.md](../06-entitlements.md) §6:
  `SeatAssignment.membership = FK(Membership, on_delete=CASCADE)`
- [04-identity-authz.md](../04-identity-authz.md) §3.2:
  `Session.active_membership = FK("organizations.Membership", ...)`

`authz` and `entitlements` are P2; `organizations` is P1. Each of those
foreign keys crosses a module boundary, and none of the three allowlisted
targets is `Membership`.

So Phase 0 mandates both the rule and three violations of it. One of them has
to give.

## Decision

**`platform.organizations.Membership` is added to the allowlist, restricted to
`platform/` and `commercial/`.**

Products may **not** hold an FK to `Membership`. They use
`platform.identity.User` for attribution, exactly as the original allowlist
intends, or a soft reference.

Enforced by system check `W360.E003`, which checks both membership of the
allowlist and the package restriction.

## Alternatives considered

### A. Make `Membership` references soft (a plain UUID column)

- **Pro:** keeps the allowlist at three.
- **Con:** loses referential integrity on the single most access-critical
  relationship in the system. A `RoleAssignment` pointing at a deleted
  membership is a dangling grant; a `SeatAssignment` pointing at one is a
  paid seat consumed by nobody. Both currently CASCADE, which is the correct
  behaviour and is free.
- **Decisive objection:** the reason F-6 exists is that a cross-module FK
  welds the database to another module's schema and blocks extraction. But
  ADR-001 and [13-deployment-evolution.md](../13-deployment-evolution.md) §4.5
  state that `identity`, `organizations`, `authz`, `entitlements` and
  `tenancy` are **never extracted** — distributing the authorization path is
  categorically forbidden. The cost F-6 is protecting against cannot be
  incurred here.

### B. Amend the three Phase 0 documents to use soft references instead

- **Con:** same loss of integrity as A, plus it rewrites three accepted
  documents to accommodate a rule rather than the other way round. The rule
  is the newer, more general statement; the models are the specific,
  considered design.

### C. Move `Membership` into `platform/tenancy` so the FKs stop crossing

- **Con:** [01-domain-boundaries.md](../01-domain-boundaries.md) §5 states
  explicitly that `tenancy` does **not** own the Organization model, and by
  extension not Membership. It would also make tenancy (P0) depend on
  identity (P1) for the user FK — an upward edge, which is worse.

### D. Add `Membership` to the allowlist, narrowly (selected)

## Why selected

1. **Phase 0 already requires it**, in two places, in accepted documents.
   Adding it resolves a genuine contradiction rather than relaxing a rule.
2. **`Membership` is the same *kind* of thing as the other three.** It is the
   universal junction through which all authorization resolves
   ([03-multi-tenancy.md](../03-multi-tenancy.md) §3.4:
   "Authorization always resolves through a membership, never through a
   user"). `User` is on the allowlist for actor attribution; `Membership` is
   the org-scoped form of exactly that.
3. **The extraction cost is zero** because the owning module is never
   extracted.
4. **The restriction keeps it narrow.** Products are unchanged, so the
   allowlist has not been widened where it would erode product independence —
   which is what F-6 is really protecting.

## Consequences

**Positive**

- `RoleAssignment` and `SeatAssignment` keep referential integrity and
  CASCADE cleanup on membership removal.
- Composite FKs (Layer 4) can target `Membership`, making a cross-tenant role
  assignment or seat grant structurally impossible rather than merely
  forbidden.
- The contradiction between the rule and the models is resolved in writing.

**Negative, accepted**

- The allowlist has four entries rather than three, with one carrying a
  package restriction — marginally more complex to state and to check.
- A future extraction of `organizations` would now be harder. Accepted,
  because ADR-001 forbids that extraction outright.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| The allowlist becomes a list people add to | Medium | Every addition needs an ADR and a debt-register entry; `W360.E003` names the rule in its failure message |
| A product adds a Membership FK | Medium | `W360.E003` fails the build with a message naming the alternative |

## Verification

- `W360.E003` enforces both the allowlist and the package restriction; it runs
  under `manage.py check` in CI and at container start.
- `tests/architecture/test_architecture.py::test_no_cross_module_orm_access`
  covers the related ORM-access rule.

## Related

- [02-dependency-rules.md](../02-dependency-rules.md) §3, F-6
- [ADR-009](ADR-009-tenant-data-isolation.md)
- [debt-register.md](../debt-register.md) entry D-002
