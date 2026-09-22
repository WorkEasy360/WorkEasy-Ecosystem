# ADR-016 — Foreign Keys Between Commercial Modules

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture
**Amends:** [02-dependency-rules.md](../02-dependency-rules.md) F-6,
[ARCHITECTURE.md](../../../ARCHITECTURE.md) §3

---

## Context

F-6 limits cross-module foreign keys to a frozen allowlist — `Organization`,
`User`, `File`, and (by [ADR-014](ADR-014-membership-fk-allowlist.md))
`Membership`. Everything else must be a soft reference.

Phase 2 implements the commercial domain, and the Phase 0 model definitions
for it specify foreign keys **between commercial modules**:

```python
# 05-commercial-model.md §4.1
class PlanPrice(models.Model):
    plan_version   = FK(PlanVersion, null=True, on_delete=PROTECT)
    bundle_version = FK(BundleVersion, null=True, on_delete=PROTECT)

# 06-entitlements.md §3
class SubscriptionItem(TenantScopedModel):
    plan_version   = FK(PlanVersion, null=True, on_delete=PROTECT)
```

`pricing`, `subscriptions` and `catalog` are separate Django apps, so
`W360.E003` — which compares app labels — flags every one of these.

Phase 0 therefore specifies models that its own enforcement rejects. One of
the two has to move.

## Decision

**A module inside `commercial/` may hold a foreign key to another module
inside `commercial/`.**

Narrow and explicit:

- `commercial/*` → `commercial/*` only.
- `platform/*` siblings stay forbidden — F-5 already bans even *importing*
  sideways there, so an FK would be worse.
- `products/*` stay forbidden from everything.
- No foreign key may **leave** `commercial/` except to the four allowlisted
  targets.

Implemented as `INTRA_PACKAGE_FK_PACKAGES = {"commercial"}` in
`platform/tenancy/checks.py`, a named set rather than a special case, so
extending it to another package is a visible edit with a diff.

## Why the F-6 argument does not apply here

F-6 exists for one reason, stated in ARCHITECTURE.md §3:

> A queryset over another module's model welds you to its schema; a foreign
> key welds the **database** to it and makes the module unextractable.

Extractability is the concern. For these edges it does not arise:

1. **Catalog and pricing are extracted together or not at all.** ADR-006's
   own extraction path says so: *"`commercial/catalog` and
   `commercial/pricing` are strong extraction candidates… If extracted,
   `resolve_price()` becomes an HTTP call behind the same signature."* They
   move as one unit, so a foreign key between them survives the move.

2. **The foreign keys are load-bearing for a correctness property.**
   `on_delete=PROTECT` is what makes it impossible to delete a plan version a
   subscription pinned or a price row an invoice referenced. That is the
   database-level half of "historical pricing cannot mutate" — the property
   ADR-006 exists to guarantee. A soft reference would leave it to
   application discipline, which is exactly the trade F5 refuses everywhere
   else.

3. **The alternative was tried and is worse.** A `plan_version_id` UUID field
   with no constraint means a subscription can reference a plan version that
   no longer exists, and nothing detects it until provisioning finds the row
   missing and cannot decide how much access to grant. Provisioning already
   logs and skips that case defensively; the FK is what makes it unreachable.

## Alternatives considered

### A. Soft references (`plan_version_id = UUIDField()`)

- **Pro:** satisfies F-6 as written; no check change.
- **Con:** loses `PROTECT`, which is the mechanism behind an accepted ADR-006
  guarantee. Referential integrity for **financial** records becomes a matter
  of nobody running the wrong delete.
- **Rejected:** trades a structural guarantee for a rule whose motivation
  does not apply.

### B. One `commercial` Django app

- **Pro:** no cross-module FK at all; the check passes untouched.
- **Con:** destroys the module boundaries the architecture is built on.
  `catalog`, `pricing`, `subscriptions` and `provisioning` would share one
  migration history, one app label and one namespace, and the internal
  layering contract would have nothing to constrain.
- **Rejected:** solves a checker's model of the problem, not the problem.

### C. Amend Phase 0's model definitions to use soft references

- **Pro:** keeps F-6 literally intact.
- **Con:** the same loss as A, plus it changes an accepted ADR to accommodate
  an enforcement rule rather than the other way round.
- **Rejected:** the models were reasoned about; the check's app-label
  granularity was not.

### D. Permit `commercial/*` → `commercial/*` (selected)

## Consequences

**Positive**

- Phase 0's own model definitions become implementable as written.
- `PROTECT` guards every reference from a financial record to the catalog
  row it pinned.
- The rule is one named set, checked in CI, and readable in one line.

**Negative, accepted**

- `commercial/` is now internally welded at the database level. Extracting
  `pricing` *alone* would require breaking these keys first — which ADR-006
  already says is not the plan.
- The check has one more concept in it. Mitigated by the constant being named
  after what it means rather than after the modules it happens to contain.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| The exception widens to other packages | Medium | It is a set literal; adding a package is a visible diff requiring an ADR and a debt-register row |
| An FK leaves `commercial/` unnoticed | Medium | Unchanged — the allowlist check still runs for every edge that is not intra-package |
| Someone reads this as "FKs are fine now" | Low | The rule names one package, and `platform/` siblings remain forbidden by a stricter rule than F-6 |

## Related

- [ADR-006](ADR-006-product-catalog-and-bundles.md) — the model definitions
  this makes implementable, and the extraction path this relies on
- [ADR-014](ADR-014-membership-fk-allowlist.md) — the same shape of decision,
  one phase earlier
- [02-dependency-rules.md](../02-dependency-rules.md) F-6
- Debt register **D-005**
