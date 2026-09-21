# Phase 0 — Exit Gate Assessment

**Date:** 2026-09-21
**Assessed by:** Architecture
**Repository state at start:** empty directory — greenfield, no existing system to preserve or migrate

---

# PHASE 0 STATUS: **PASS**

---

## 1. Exit criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | System boundaries documented | ✅ | [00-system-context.md](00-system-context.md) — 10 actor types, 10 external system classes, 7 trust boundaries, control/data plane split |
| 2 | Dependency direction explicit | ✅ | [02-dependency-rules.md](02-dependency-rules.md) — 5 layers, 8 forbidden edges, import-linter config, 9 system checks |
| 3 | Tenant model defined | ✅ | [03-multi-tenancy.md](03-multi-tenancy.md) — Organization/User/Membership, 5 isolation layers, lifecycle, isolation beyond PostgreSQL |
| 4 | Identity and authorization defined | ✅ | [04-identity-authz.md](04-identity-authz.md) — 7-step decision chain, RBAC with scoping, 7 personas, break-glass |
| 5 | Commercial model defined | ✅ | [05-commercial-model.md](05-commercial-model.md) — versioned catalog, segmented price books, bundles, capability-based partners |
| 6 | Subscription/entitlement model defined | ✅ | [06-entitlements.md](06-entitlements.md) — read model, single writer, 7-state lifecycle, seats, billing-failure invariant |
| 7 | Partner model defined | ✅ | [05-commercial-model.md](05-commercial-model.md) §5 — capabilities, relationships, branding, commissions, payouts |
| 8 | Cross-product integration rules defined | ✅ | [07-cross-product-integration.md](07-cross-product-integration.md) — 4 mechanisms, outbox, idempotency, versioning, DLQ |
| 9 | Data ownership defined | ✅ | [08-data-ownership.md](08-data-ownership.md) — full matrix, collision analysis for Product/Invoice/Customer/Address/Currency/User |
| 10 | Failure boundaries defined | ✅ | [09-failure-resilience.md](09-failure-resilience.md) — 4 tiers, per-dependency behaviour, degradation matrix, bulkheads |
| 11 | Concurrency strategy documented | ✅ | [10-performance-concurrency.md](10-performance-concurrency.md) — 13 contention points, control selection, idempotency surfaces |
| 12 | Observability architecture documented | ✅ | [11-observability.md](11-observability.md) — correlation spine, 4 signals, health checks, SLO burn alerting, audit |
| 13 | Security architecture documented | ✅ | [12-security.md](12-security.md) — 14 threats, SSRF, uploads, webhooks, secrets, admin, fail-closed table |
| 14 | ADRs completed | ✅ | [adrs/](adrs/) — ADR-001 to ADR-012, each with context, decision, alternatives, rationale, consequences, risks, extraction path |
| 15 | Architecture fitness rules documented | ✅ | [ARCHITECTURE.md](../../ARCHITECTURE.md) §4 — 48 rules across 7 categories, each with an enforcement mechanism |
| 16 | No unresolved P0 architecture risks | ✅ | [15-risk-register.md](15-risk-register.md) — 8 P0 risks identified, all resolved by decision with fallbacks; 3 carry mandatory verification spikes |

## 2. Deliverables produced

```
ARCHITECTURE.md                              root — the rules, 48 fitness checks
docs/architecture/
  README.md                                  index and reading order
  PHASE-0-STATUS.md                          this document
  00-system-context.md      01-domain-boundaries.md    02-dependency-rules.md
  03-multi-tenancy.md       04-identity-authz.md       05-commercial-model.md
  06-entitlements.md        07-cross-product-integration.md
  08-data-ownership.md      09-failure-resilience.md   10-performance-concurrency.md
  11-observability.md       12-security.md             13-deployment-evolution.md
  14-repository-structure.md                 15-risk-register.md
  debt-register.md          allowed-event-cycles.yml
  adrs/ADR-001 … ADR-012                     12 decision records
```

Two documents beyond the requested list: **14 (Repository Structure)** covering Task 14, and **15 (Risk Register)** covering Task 17. Both were required by the brief's tasks but absent from the deliverables list.

## 3. Where this departs from the brief

Four changes to the proposed structure, each argued in [01-domain-boundaries.md](01-domain-boundaries.md) §2:

| Change | Reason |
|---|---|
| **`entitlements` moved from `commercial/` to `platform/`** | The single most consequential change. With entitlements in `commercial/`, `products → commercial` becomes a legal import edge, and a product will eventually branch on `plan.name`. Moving the read model to `platform/` lets us ban that edge outright. |
| **Added a dependency-free `contracts/` package** | Without it, "products talk through contracts" is unenforceable, because the contract would live inside the implementing module. |
| **`platform/` internally layered (P0–P3)** | A flat platform develops cycles immediately (`identity ↔ notifications`). Layers plus events prevent it structurally. |
| **`integration/` = mechanism; API composition moved to `interfaces/`** | Each module owns its own API surface; only versioning, routing and throttling are cross-cutting. |

One notable modelling decision beyond the brief: **`PartnerAccount` uses composable capability grants rather than a type enum**, because real partner programmes produce hybrids and type-branching privilege logic is where data breaches come from.

## 4. Decisions now frozen for Phase 1

These may only be changed by a new ADR superseding the existing one.

### Structure
1. Modular monolith — one codebase, one deployment, many tenants, many products.
2. Six-layer module map: `contracts` → `platform` → `integration` → `commercial` / `products` → `interfaces`.
3. Dependencies point downward only; no sideways edges at L3.
4. The eight forbidden edges (F-1 … F-8).
5. Cross-module foreign keys limited to `Organization`, `User`, `File`.
6. The canonical module layout: `models / domain / services / selectors / api / tasks / events`.

### Tenancy and identity
7. `Organization` is the tenant; `User` is global; `Membership` grants all access.
8. Shared database, shared schema, `organization_id` on every tenant row.
9. Five isolation layers, with PostgreSQL RLS (`FORCE`, `USING` + `WITH CHECK`) as the backstop.
10. Tenant variable set only via `set_config(..., is_local => true)` inside a transaction.
11. Composite foreign keys make cross-tenant references unrepresentable.
12. Application connects as a non-owner, `NOBYPASSRLS` role; startup asserts it.
13. Platform owns all authentication; no product implements any part of it.
14. RBAC with organization/product/resource scoping; permissions are code-declared.
15. Seven personas, with Platform Super Admin holding **no default access to tenant business data**.

### Commercial and entitlement
16. Entitlements are a `platform/` read model; `commercial/provisioning` is the sole writer.
17. Entitlement checks precede permission checks; seats are checked separately.
18. Overlapping grants resolve **most generous wins**.
19. **Uncertainty never changes access** — revocation requires a positive durable decision.
20. `suspended` is read-only, not lockout.
21. Published plan versions and prices are immutable; subscriptions pin their price.
22. Negotiated prices are org-scoped `ContractPrice`, never rows in the global price book.
23. Money is `(integer minor units, CurrencyCode)`, always.
24. Partners are one account with composable capabilities; partner data access is customer-issued.
25. Commission is an append-only ledger with reversals and a holdback.

### Integration and operations
26. Cross-product writes are asynchronous domain events; reads are nullable, batch-only ports.
27. Transactional outbox; at-least-once delivery; idempotent consumers via `ProcessedEvent`.
28. Per-aggregate ordering only; no global ordering guarantee.
29. Events are versioned past-tense facts, with schemas in `contracts/`.
30. Four dependency criticality tiers; a Tier-N failure never fails a lower-tier operation.
31. Mandatory explicit timeouts; bounded jittered retries; no nesting; no external I/O in transactions.
32. Queue and connection-pool bulkheads; prioritised load shedding; per-product kill switches.
33. Keyset pagination by default.
34. Correlation ID propagated through request → task → event → webhook.
35. Audit is append-only, hash-chained, customer-visible, never sampled.
36. `platform/identity`, `organizations`, `authz`, `entitlements`, `tenancy` are **never extracted**.

## 5. Risks carried into Phase 1

All eight P0 risks are resolved by decision with documented fallbacks. Three require verification before feature development, and **these are Phase 1 Sprint 0 entry conditions**:

| Spike | Risk | Question | Fallback if it fails |
|---|---|---|---|
| **RLS + PgBouncer** | R-01 | Does `set_config(..., true)` hold under real transaction pooling with interleaved tenants? | Session pooling for the web tier — costs connections, not correctness |
| **Broker decision** | R-09 | SQS or AmazonMQ, given ordering, priority and visibility-timeout needs? | Consumers are already order-tolerant, so either works; this is a cost/ops choice |
| **Entitlement latency** | R-14 | Does the check path meet 1 ms p50 / 5 ms p95 on realistic data? | Widen the budget or deepen request-local caching |

Nine P1 risks carry owners and due milestones ([15-risk-register.md](15-risk-register.md) §4). The two most likely to bite:

- **R-10 — suspension enforcement** must be structural (middleware + `TenantScopedModel`), not per-view. Needs a design review before the first product write path ships.
- **R-12 — operational load.** Start with **three** queue groups, not seven. Task-level queue assignment exists from day one, so splitting later is a deployment change.

Eleven P2 items are documented accepted limitations with revisit triggers.

## 6. What Phase 0 deliberately did not do

- No product features. No CRM objects, no chart of accounts, no stock valuation.
- No code. No models, no migrations, no scaffolding.
- No microservice decomposition. Extraction *criteria* and *procedure* are defined; extraction is not done.
- No speculative infrastructure. Search is PostgreSQL FTS until measured otherwise; multi-region, sharding, CQRS and GraphQL are deferred with explicit triggers.
- No technology substitutions. The stack in the brief is well matched, and PostgreSQL RLS in particular is load-bearing for the tenancy model.

## 7. Recommended Phase 1 sequence

Not part of Phase 0 scope; included because the ordering follows from the dependency graph.

1. **Sprint 0** — the three verification spikes, plus repository scaffolding, CI with import-linter and system checks, and the fitness-test harness. Enforcement must exist before there is code to enforce it on.
2. `contracts/` + `platform/tenancy` + `platform/observability` — the base everything else needs.
3. `platform/identity` + `platform/organizations` + `platform/authz`.
4. `platform/entitlements` + `integration/events` (outbox).
5. `commercial/catalog` + `pricing` + `subscriptions` + `provisioning`.
6. **First product**, end to end, as the proof that the boundaries hold.
7. Second product — the real test, because it is the first time the event path carries weight.

The second product is where the architecture is actually validated. If adding it requires changing the first, something in Phase 0 was wrong and should come back to Architecture.

---

**Phase 1 has not begun. No implementation work has been started.**
