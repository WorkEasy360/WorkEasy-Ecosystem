# ADR-007 — Cross-Product Communication

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

---

## Context

Products must react to each other: a won CRM deal should be able to create a Books invoice, a Projects project and an Inventory reservation. But each product must ship, run and be sold independently — an organization with only Inventory must have a fully working Inventory.

The mechanism chosen here determines whether the product boundaries survive.

## Decision

**Cross-product writes are asynchronous domain events. Cross-product reads are synchronous contract ports. Direct imports between products are forbidden.**

- **Domain events** for anything that causes a write in another product. Publisher does not know its consumers. Events are facts in the past tense (`crm.deal.won`), never commands.
- **Contract ports** for synchronous, read-only lookups, resolved at runtime through a registry, returning DTOs, **nullable**, and **batch-only**.
- **No product imports another product**, for any reason, including type hints (rule F-1, linter-enforced).
- **No cross-product foreign keys.** References are soft: `(product, entity, id)`.
- **Event schemas live in `contracts/`**, so a consumer never imports a publisher.
- Events are versioned; each product's manifest declares `publishes` and `consumes`, and CI builds the graph from manifests.

## Alternatives considered

### A. Direct service calls between products

`books_services.create_invoice_from_deal(deal)` called by CRM.

- **Pro:** simple, synchronous, immediately consistent, easy to debug.
- **Con:** CRM cannot be deployed or sold without Books; a Books failure fails CRM's write; CRM's latency includes Books'; the dependency graph becomes a mesh; extraction becomes impossible.
- **Rejected:** it defeats independent commercialization on day one.

### B. Shared database tables

Products read and write common tables.

- **Pro:** no integration code; immediate consistency.
- **Con:** schema coupling with no code-visible dependency; a migration in one product breaks another; ownership becomes undefinable; extraction requires a data split.
- **Rejected:** explicitly forbidden by the brief, and correctly so.

### C. Django signals

- **Pro:** built in; feels decoupled.
- **Con:** invisible in the call graph; no ordering guarantees; fire inside the sender's transaction so a slow handler slows the write; silently skipped by `bulk_create`, `update()` and fixtures; make tests non-deterministic; no retry, no durability, no observability.
- **Rejected outright**, including within `commercial/`. Permitted only *within* a module for mechanical concerns.

### D. Internal HTTP calls between products in one process

- **Pro:** looks like the eventual service architecture.
- **Con:** serialisation, authentication and network overhead for an in-process call; a circular runtime dependency inside one process; harder to debug than either alternative.
- **Rejected:** all of the cost of distribution, none of the benefit.

### E. Events for writes, ports for reads (selected)

## Why selected

1. **It is the only option that preserves independent sale and deployment.** A consumer that does not exist simply does not consume.
2. **Failure is contained.** A broken Books handler cannot fail CRM's write, because CRM never calls Books.
3. **Publisher latency is constant** regardless of consumer count.
4. **It is already the service-boundary mechanism**, so extraction changes deployment, not design.
5. **Ports cover the genuine synchronous-read case** without opening a general-purpose backdoor — and their deliberate awkwardness (nullable, batch-only, DTO-returning) keeps their number low.
6. **Denormalised event payloads** remove the callback that would otherwise recreate a runtime dependency.

## Consequences

**Positive**

- Products are genuinely independent.
- Adding a consumer requires no change to the publisher.
- The integration graph is generated from manifests and cannot go stale.
- Failure isolation is structural, not conventional.

**Negative, accepted**

- **Eventual consistency between products.** The invoice appears shortly after the deal is won. This must be designed into the UI rather than hidden; pretending otherwise produces confusing products.
- Debugging spans an async boundary. Mitigated by `correlation_id` and `causation_id` propagation and by trace links.
- Denormalised payloads duplicate data. Deliberate: the alternative is a callback and a hard dependency.
- Consumers must be idempotent, which is real engineering discipline. Mitigated by a `ProcessedEvent` helper and a required duplicate-delivery test.
- Dangling soft references must be handled. Made explicit rather than accidental.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| A developer adds a cross-product import under pressure | **High** | import-linter `independence` contract; exception requires an ADR |
| Event schema change breaks a consumer | High | Versioning rules; CI compatibility check against `main` |
| Event loop (A → B → A) | Medium | `causation_depth` cap; CI cycle detection on the manifest graph |
| Consumers rely on ordering that is not guaranteed | Medium | Documented explicitly; `aggregate_version` pattern for order-sensitive handlers |
| Ports become a general RPC backdoor | Medium | Read-only, nullable, batch-only, reviewed; write ports rejected in review |
| Users confused by eventual consistency | Medium | Explicit UI states; no false promises of instant cross-product results |

## Future extraction path

This is the extraction enabler. On extraction: the outbox publishes to a real broker topic instead of an in-process dispatcher, and port adapters become HTTP clients behind the **same `contracts/` interfaces**. No caller changes. Consumers already tolerate duplicates, reordering and delay, so the semantics do not shift — which is precisely why they were chosen now.

## Related

- [07-cross-product-integration.md](../07-cross-product-integration.md), [02-dependency-rules.md](../02-dependency-rules.md)
- [ADR-001](ADR-001-modular-monolith-first.md), [ADR-008](ADR-008-transactional-outbox.md)
