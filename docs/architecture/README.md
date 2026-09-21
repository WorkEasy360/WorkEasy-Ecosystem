# Workeasy360 Architecture Documentation

**Phase 0 — Architecture Foundation.** Status: complete. See [PHASE-0-STATUS.md](PHASE-0-STATUS.md).

Start with [ARCHITECTURE.md](../../ARCHITECTURE.md) at the repository root — it holds the rules every developer must know. These documents hold the reasoning behind them.

## Reading order

**New to the project?** 00 → 01 → 02 → [ARCHITECTURE.md](../../ARCHITECTURE.md). That is enough to contribute safely.

**Building a product module?** Add 03, 04, 06, 07, 14.

**Working on platform or commercial?** Read everything.

**On call?** 09, 11, and the degradation matrix in 09 §5.

## Documents

| # | Document | Answers |
|---|---|---|
| 00 | [System Context](00-system-context.md) | Who uses this, what it talks to, where trust changes |
| 01 | [Domain Boundaries](01-domain-boundaries.md) | What modules exist and what each owns |
| 02 | [Dependency Rules](02-dependency-rules.md) | What may import what, and how it is enforced |
| 03 | [Multi-Tenancy](03-multi-tenancy.md) | How tenants are isolated, in five layers |
| 04 | [Identity & Authorization](04-identity-authz.md) | Who you are, what you may do |
| 05 | [Commercial Model](05-commercial-model.md) | What we sell, how it is priced, how partners fit |
| 06 | [Entitlements](06-entitlements.md) | How purchase becomes access |
| 07 | [Cross-Product Integration](07-cross-product-integration.md) | How products talk without depending on each other |
| 08 | [Data Ownership](08-data-ownership.md) | Who owns each entity; why "Invoice" is two things |
| 09 | [Failure & Resilience](09-failure-resilience.md) | What happens when things break |
| 10 | [Performance & Concurrency](10-performance-concurrency.md) | Budgets, pools, contention, idempotency |
| 11 | [Observability](11-observability.md) | How to diagnose an incident without guessing |
| 12 | [Security](12-security.md) | Threat model and controls |
| 13 | [Deployment & Evolution](13-deployment-evolution.md) | How it runs, how it scales, when to extract |
| 14 | [Repository Structure](14-repository-structure.md) | Where code goes |
| 15 | [Risk Register](15-risk-register.md) | What we know is risky |

## Architecture Decision Records

| ADR | Decision |
|---|---|
| [001](adrs/ADR-001-modular-monolith-first.md) | Modular monolith first |
| [002](adrs/ADR-002-multi-tenant-organization-model.md) | Organization as tenant, user global, membership as junction |
| [003](adrs/ADR-003-shared-identity.md) | Shared identity across all products |
| [004](adrs/ADR-004-rbac-authorization.md) | RBAC with scoping |
| [005](adrs/ADR-005-product-entitlement-model.md) | Entitlements as a platform read model |
| [006](adrs/ADR-006-product-catalog-and-bundles.md) | Versioned catalog, segmented price books |
| [007](adrs/ADR-007-cross-product-communication.md) | Events for writes, ports for reads |
| [008](adrs/ADR-008-transactional-outbox.md) | Transactional outbox |
| [009](adrs/ADR-009-tenant-data-isolation.md) | Five-layer tenant isolation |
| [010](adrs/ADR-010-partner-reseller-whitelabel-model.md) | Capability-based partner model |
| [011](adrs/ADR-011-shared-ai-platform.md) | Shared AI platform behind a port |
| [012](adrs/ADR-012-failure-isolation-strategy.md) | Tiered dependencies and failure isolation |

## The five ideas that matter most

If you remember nothing else:

1. **Products never import products.** Events for writes, ports for reads. This is what makes them independently sellable.
2. **Entitlements live in `platform/`, not `commercial/`.** That is what lets us ban `products → commercial` outright, which is what keeps packaging out of product code.
3. **Tenant isolation is five layers deep**, because the application will eventually contain a bug that forgets to filter, and RLS is what survives it.
4. **Uncertainty never changes access.** A payment timeout is not a failed payment. Revocation requires a positive, durable decision.
5. **Every external call has a timeout.** Slow dependencies cause more outages than dead ones.

## Changing these documents

Structural changes require an ADR and Architecture review: new top-level modules, changes to layer or isolation rules, new forbidden-edge exceptions, new infrastructure, changes to identity/authz/entitlement contracts, service extraction, new external providers handling customer data.

Ordinary work does not: new products, new features, new events, new roles, performance tuning. A governance process that gates daily development gets routed around.
