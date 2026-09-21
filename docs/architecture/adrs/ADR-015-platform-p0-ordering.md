# ADR-015 — Splitting Platform P0 into Ordered Layers

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture
**Amends:** [01-domain-boundaries.md](../01-domain-boundaries.md) §5, [02-dependency-rules.md](../02-dependency-rules.md) F-5

---

## Context

Phase 0 places `tenancy` and `observability` together at **P0**, and forbids
same-layer imports inside `platform/` (F-5):

> Within a layer, modules may **not** import each other (`identity` and
> `organizations` are siblings; they communicate via events or via an L4
> orchestrator).

Implementation found this unsatisfiable for these two specific modules:

1. **`tenancy` must log.** Every isolation control emits a structured event:
   `tenant.context.missing` is a correctness alarm that pages, and
   `tenancy.unscoped_access.opened` is an audit record
   ([11-observability.md](../11-observability.md) §6). A module that cannot
   log cannot report the thing it exists to report.

2. **`observability` must read tenant context.** `organization_id` is a
   mandatory field on every log line and span
   ([11-observability.md](../11-observability.md) §4). Without it, an
   engineer woken at 03:00 cannot determine which tenant is affected, which
   is the standard that document holds itself to.

The sanctioned replacements for a forbidden edge do not apply. Events are
asynchronous and a log line is synchronous. An L4 orchestrator cannot inject
a field into a log call made deep inside a P0 module.

## Decision

**P0 is split into two ordered layers. `observability` sits strictly below
`tenancy`.**

| Layer | Modules | May depend on |
|---|---|---|
| **P0a** | `observability` | `contracts` only |
| **P0b** | `tenancy` | `contracts`, P0a |
| **P1** | `identity`, `organizations` | P0a, P0b |
| **P2** | `authz`, `entitlements`, `feature_flags`, `audit` | P0–P1 |
| **P3** | `files`, `notifications`, `workflows` | P0–P2 |

`observability` imports **nothing** from `platform/`. Where it needs
information owned by a higher layer, that layer **pushes** it through a
registry:

- **Log context** — `contracts/ports/observability.py` holds a provider
  registry. `tenancy` registers a provider at app-ready that returns the
  current tenant's identifiers. Observability calls it without knowing who
  supplied it.
- **Health probes** — `observability/health.py` holds a probe registry.
  `tenancy` contributes the RLS-role assertion (R-02) and `integration/events`
  contributes outbox lag. Observability knows how to *run* and *report* a
  probe; each module owns what "healthy" means for itself.

## Alternatives considered

### A. Leave both at P0 and let them import each other

- **Con:** a two-module cycle at the foundation of the platform, and an
  explicit F-5 violation. `identity ↔ notifications` is the example F-5 was
  written to prevent, and this is the same shape.
- **Rejected:** it weakens the rule rather than satisfying it.

### B. Merge the two modules

- **Con:** they own genuinely different things, have different reasons to
  change, and 01-domain-boundaries.md §5 lists them separately with distinct
  responsibilities. Merging to avoid an ordering constraint is the wrong
  trade.

### C. Have `tenancy` use only stdlib `logging`, never structlog

- **Pro:** removes the tenancy → observability edge.
- **Partially adopted.** `tenancy` does use stdlib `logging`, whose records
  flow through the shared processor chain. But `tenancy/tasks.py` needs the
  correlation id to attach to Celery headers, which is observability's, and a
  correlation id that is not propagated is not a correlation id.
- **Insufficient alone.**

### D. Ordered layers plus a push registry (selected)

## Why selected

1. **It adds an ordering constraint rather than removing one.** P0a < P0b is
   strictly more restrictive than "P0 is one layer", so nothing previously
   forbidden becomes permitted.
2. **It removes a real cycle** rather than declaring an exception to it.
   `observability` now depends on nothing internal but `contracts`, which is
   the strongest position any module can be in.
3. **The registry pattern is already in the architecture.**
   07-cross-product-integration.md §4 uses runtime resolution through a
   registry for exactly this purpose: a needed capability with no permitted
   import edge.
4. **Each module owns its own health definition**, which is better design
   independently of the layering problem. Observability should not know what
   "healthy" means for tenancy or for the outbox.

## Consequences

**Positive**

- `observability` is importable from anywhere, including `contracts`-only
  contexts, with no risk of a cycle.
- Adding a health probe or a log field is a registration in the owning
  module, not an edit to observability.
- F-5 holds without exception inside `platform/`.

**Negative, accepted**

- Log context arrives through an indirection rather than a direct call, so
  "where does `organization_id` come from?" takes one extra hop to answer.
  Mitigated by a docstring at the registry naming the provider.
- A provider that raises would silently contribute nothing. Deliberate: a
  logging failure must not break the operation it was describing. Covered by
  `test_log_context_provider_failure_does_not_break_logging`.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| A provider is never registered, so logs silently lack tenant fields | Medium | `test_tenant_context_reaches_log_events` asserts the provider is registered and returns the expected fields |
| A future module imports observability from P0a | Low | The import-linter layer contract fails the build |

## Verification

- `.importlinter` contract `platform-internal-layers` lists
  `workeasy360.platform.observability` below `workeasy360.platform.tenancy`;
  the contract is **KEPT**.
- `grep -rn "platform.tenancy" platform/observability/` returns nothing.
- `test_startup_verifies_the_rls_role` asserts tenancy's probe reaches the
  startup endpoint.

## Related

- [01-domain-boundaries.md](../01-domain-boundaries.md) §5
- [02-dependency-rules.md](../02-dependency-rules.md) F-5
- [11-observability.md](../11-observability.md) §4, §7
