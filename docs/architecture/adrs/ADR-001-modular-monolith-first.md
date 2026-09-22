# ADR-001 — Modular Monolith First

> **CORRECTION (2026-09-22, [ADR-017](ADR-017-products-are-separate-applications.md)).**
> This document was written when product domains were to live *inside* this
> repository, under `products/`. They no longer do: each business product is
> a separate application in its own repository. Everything here about
> layering, ownership, isolation and enforcement still holds — but wherever
> it places a product's code in this repository, read
> [16 — Ecosystem / Product Boundary](../16-ecosystem-product-boundary.md)
> instead.


**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture
**Supersedes:** —

---

## Context

Workeasy360 hosts many independently commercializable products (CRM, Books, Inventory, Projects, …) on shared platform services. Products must be independently sellable and logically isolated, but they share identity, tenancy, authorization, entitlements, notifications, files and audit.

The initial team is small. There is no production traffic, no measured scaling profile, and no evidence about which modules will grow fastest. The commercial requirement is to ship several products over the next 18–24 months and let customers activate them incrementally.

The obvious temptation — "many products means many services" — conflates *logical* independence with *physical* independence. These are different properties with very different costs.

## Decision

**Build a modular monolith: one Django application, one deployment artifact, one database, with strictly enforced internal module boundaries and predefined extraction points.**

Specifically:

- One repository, one image, multiple entrypoints (API, seven worker queue groups, scheduler).
- Modules are separated by enforced dependency rules ([02-dependency-rules.md](../02-dependency-rules.md)), not by network boundaries.
- Cross-product communication is via domain events and `contracts/` ports — the same mechanisms that would be used across services.
- Each module owns its tables; no cross-module foreign keys outside a frozen three-entry allowlist.
- Extraction criteria and procedure are defined in advance ([13-deployment-evolution.md](../13-deployment-evolution.md)).

## Alternatives considered

### A. Microservices from day one

Each product and each platform capability as an independently deployed service.

- **Pro:** independent deploys, independent scaling, technology freedom, hard boundaries enforced by the network.
- **Con:** distributed transactions for operations spanning products; network failure as a routine case; per-service CI/CD, observability, secrets and on-call; cross-service debugging; versioned wire protocols between teams that share a room; 10–20× the operational surface for a team that cannot staff it.
- **Decisive objection:** the boundaries would be guesses. We do not yet know where the seams belong, and a wrongly-placed service boundary is enormously more expensive to correct than a wrongly-placed module boundary. Microservices make boundaries expensive to change at exactly the moment we are least certain about them.

### B. Service per product, shared platform monolith

Products as services; platform as a shared library or service.

- **Pro:** product isolation with fewer services than full decomposition.
- **Con:** the shared platform becomes a distributed dependency on the request path — every authorization check is a network call. Either platform is a library (and then it is a monolith with extra deployment steps) or a service (and then every product request depends on its availability and latency).
- **Decisive objection:** it distributes the authorization path, which is the one thing that must never be distributed ([13-deployment-evolution.md](../13-deployment-evolution.md), §4.5).

### C. Unstructured monolith

One Django project, apps as convenience groupings, free imports between them.

- **Pro:** fastest initially; no ceremony.
- **Con:** boundaries erode silently. Within two years, CRM imports Books, Books queries CRM tables, and independent commercialization becomes impossible without a rewrite.
- **Decisive objection:** it directly contradicts the product vision. Products that cannot be separated cannot be sold separately.

### D. Modular monolith (selected)

## Why selected

1. **It preserves optionality.** Module boundaries can be moved in an afternoon; service boundaries cannot. We keep the ability to be wrong cheaply during the period we are most likely to be wrong.
2. **The isolation requirement is logical, not physical.** Independent commercialization needs independent *code and data ownership*, which module rules provide. It does not need independent *processes*.
3. **Operational cost matches team size.** One pipeline, one set of dashboards, one on-call rotation, one migration path.
4. **Transactional integrity is free.** Operations spanning modules (create org → membership → role → entitlement) are single database transactions rather than sagas with compensating actions.
5. **The extraction path is real**, because a module obeying the rules already has no peer imports, no shared tables, its own events and its own API namespace. Extraction becomes a deployment change rather than a rewrite.
6. **Performance.** In-process calls are nanoseconds. A distributed system spends much of its latency budget on serialisation and network hops that buy nothing at our scale.

## Consequences

**Positive**

- One deployment, one migration set, one dependency graph.
- Cross-module refactoring is a normal PR.
- Local development runs the whole platform.
- Strong consistency where it matters.
- Debugging is a single stack trace.

**Negative, accepted**

- A deployment deploys everything. Mitigated by per-product kill switches, feature flags and expand/contract migrations.
- All modules scale together. Mitigated by queue and pool bulkheads; genuinely divergent workloads become extraction candidates.
- Boundaries need active enforcement, because nothing physical prevents a bad import. Mitigated by import-linter, system checks and architecture tests in CI.
- One process crash affects everything. Mitigated by many instances, health checks, bulkheads.
- A single database is a shared resource. Mitigated by per-tier pools, statement timeouts, replica for reports.

**Neutral**

- Team ownership is by directory rather than by repository. Enforced via `CODEOWNERS`.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Boundary erosion under deadline pressure | **High** | Automated enforcement in CI; exceptions need an ADR and a dated debt-register entry |
| Database becomes the bottleneck | Medium | Bulkheads, replica, tuning path in [10](../10-performance-concurrency.md); partitioning and sharding defined |
| Deployment blast radius | Medium | Kill switches, flags, canary deploys, automatic rollback |
| "Just this once" exceptions accumulate | **High** | Suppressions expire after 90 days and fail CI |
| Team growth causes deployment contention | Low now | Measured; a documented extraction trigger |

The highest risk is social, not technical: enforcement must be automated, because discipline alone does not survive a deadline.

## Future extraction path

Defined in full in [13-deployment-evolution.md](../13-deployment-evolution.md), §4. Summary: verify the boundary → externalise events to a real topic → replace in-process port adapters with HTTP adapters behind the same `contracts/` interfaces → split the schema → run both paths with shadow traffic → cut over behind a kill switch → remove the in-process path.

Extraction requires at least two of six documented conditions (divergent scaling, divergent availability, measured team friction, genuine technology need, regulatory isolation, proven cadence conflict) plus an ADR. "The codebase is large" is explicitly not a valid trigger.

**Never extractable:** `platform/identity`, `organizations`, `authz`, `entitlements`, `tenancy` — distributing the authorization path introduces latency into every request and the possibility of authorizing against stale state.

## Related

- [01-domain-boundaries.md](../01-domain-boundaries.md), [02-dependency-rules.md](../02-dependency-rules.md), [13-deployment-evolution.md](../13-deployment-evolution.md)
- [ADR-007](ADR-007-cross-product-communication.md), [ADR-012](ADR-012-failure-isolation-strategy.md)
