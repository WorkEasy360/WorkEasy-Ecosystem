# 15 — Architecture Risk Register (Threat & Failure Review)

**Status:** Accepted (Phase 0)
**Owner:** Architecture
**Last reviewed:** 2026-09-21

---

## 1. Purpose

The output of the Phase 0 threat review, concurrency review, failure-mode review, data-isolation review, dependency review and scalability review. Nothing is hidden here; where the architecture is weak, it says so.

## 2. Severity definitions

| Level | Meaning | Gate |
|---|---|---|
| **P0** | Could cause data breach, data loss, financial error, or make the architecture unworkable. | **Must be resolved before Phase 0 exit.** Resolution means a decided mechanism *and* a fallback if the mechanism fails verification. |
| **P1** | Significant risk to reliability, maintainability or cost. Carries into Phase 1 with an owner and a due milestone. | Tracked, not blocking |
| **P2** | Known limitation or accepted trade-off. Revisited on a trigger. | Documented |

**"Resolved by decision"** means the architectural choice removes the risk, and a verification task is scheduled. It does not mean the code exists — no code exists in Phase 0.

---

## 3. P0 risks

### R-01 — RLS session variable leaking across pooled connections

**Review:** data isolation · dependency
**Scenario:** PgBouncer in transaction pooling mode returns a server connection to the pool at `COMMIT`. A session-level `SET app.organization_id` would persist on that connection and be inherited by the next transaction — belonging to a different tenant. Result: silent cross-tenant reads with no error.

**Resolution:** `set_config('app.organization_id', ..., is_local => true)` only, always inside an explicit transaction, applied centrally in `platform/tenancy` — never by application code. Every tenant-scoped request therefore runs in a transaction; `ATOMIC_REQUESTS` is not used.

**Verification (Phase 1, Sprint 0 — mandatory):** an integration test running interleaved tenant transactions through a **real PgBouncer in transaction mode**, asserting zero leakage, plus a test asserting zero rows when the variable is unset. Local development and CI run PgBouncer for parity.

**Fallback if verification fails:** run the web tier through PgBouncer in **session pooling** mode with a smaller pool, keeping transaction pooling for worker tiers. Costs connections, not correctness.

**Status: Resolved by decision. Verification scheduled.** → [ADR-009](adrs/ADR-009-tenant-data-isolation.md), [03](03-multi-tenancy.md)

---

### R-02 — RLS silently inactive because the application owns the tables

**Review:** data isolation
**Scenario:** PostgreSQL table owners bypass RLS unless `FORCE ROW LEVEL SECURITY` is set. Django migrations typically run as the same role the application uses. Every policy would exist, every test of policy *definition* would pass, and no policy would ever be enforced.

**Resolution:** three independent controls — (a) `FORCE ROW LEVEL SECURITY` on every tenant table, applied by the migration helper; (b) two database roles, `w360_migrator` (owner) and `w360_app` (`NOBYPASSRLS`, non-owner); (c) a **startup assertion** querying `pg_roles` for `rolsuper` and `rolbypassrls` that refuses to boot if either is true. CI runs as `w360_app`, so isolation tests exercise real enforcement.

**Status: Resolved by decision.** → [ADR-009](adrs/ADR-009-tenant-data-isolation.md)

---

### R-03 — Billing failure corrupting authorization state

**Review:** failure-mode · concurrency
**Scenario:** a payment-provider timeout, a duplicated webhook, or out-of-order provider events cause entitlements to be revoked or granted incorrectly. Paying customers lose access; cancelled customers retain it.

**Resolution:** entitlements are a **derived read model**; `compute_entitlements()` is a pure deterministic function of durable database state; `apply_entitlements()` is idempotent and diff-based; `commercial/provisioning` is the sole writer. The governing invariant: **absence of information never revokes and never grants.** Revocation requires a positive durable decision. Webhook handlers verify, persist and return 200 without touching entitlements. Scheduled reconciliation detects drift and alerts.

**Status: Resolved by decision.** → [ADR-005](adrs/ADR-005-product-entitlement-model.md), [06](06-entitlements.md)

---

### R-04 — Product boundary erosion

**Review:** dependency
**Scenario:** under deadline pressure, someone imports `products.books.models` into CRM. It works, it is fast, it ships. Within two years products cannot be sold or deployed independently, and the core product vision is dead. This is how every modular monolith fails, and it fails socially, not technically.

**Resolution:** automated enforcement, not discipline — import-linter `independence` and `forbidden` contracts in CI; AST-based cross-product ORM checks; system checks for cross-module foreign keys; `CODEOWNERS` on `contracts/`, `platform/` and `.importlinter`; a new edge requires an ADR; suppressions carry a ticket reference and **fail CI after 90 days**.

**Residual:** enforcement can be weakened by someone editing `.importlinter`. Mitigated by CODEOWNERS review and by making that file's history a review point.

**Status: Resolved by decision.** → [ADR-001](adrs/ADR-001-modular-monolith-first.md), [02](02-dependency-rules.md)

---

### R-05 — Cross-tenant references written within a valid tenant context

**Review:** data isolation
**Scenario:** RLS prevents reading another tenant's row but does not prevent writing a row in *your* tenant that references one — e.g. an activity in tenant A pointing at a deal ID belonging to tenant B, obtained by enumeration or a bug. RLS alone leaves this open.

**Resolution:** composite foreign keys. Parents carry `UNIQUE (id, organization_id)`; children reference `(parent_id, organization_id)`. A cross-tenant reference becomes **unrepresentable** in the schema. Django does not generate these, so they are applied by a `RunSQL` migration helper, with a fitness test asserting every tenant-model FK targeting another tenant model has one.

**Residual:** the helper is custom tooling that must be correct. Covered by its own tests.

**Status: Resolved by decision.** → [ADR-009](adrs/ADR-009-tenant-data-isolation.md)

---

### R-06 — Cascading failure from an unbounded external call

**Review:** failure-mode · dependency
**Scenario:** Python HTTP clients default to no timeout. One slow provider consumes every worker thread across all products; the platform stops. A slow dependency is more dangerous than a dead one.

**Resolution:** mandatory explicit timeouts enforced by system check `W360.E008`; a documented timeout table; circuit breakers on all external dependencies; bounded jittered retries with no nesting; queue and connection-pool bulkheads; prioritised load shedding; the rule forbidding external I/O inside a database transaction, enforced by a test-suite wrapper.

**Status: Resolved by decision.** → [ADR-012](adrs/ADR-012-failure-isolation-strategy.md), [09](09-failure-resilience.md)

---

### R-07 — Silent tenant-scope loss in background tasks

**Review:** data isolation · concurrency
**Scenario:** contextvars do not propagate into Celery workers. A task that queries tenant data without re-establishing context either runs unscoped (if the ORM allowed it) or against whatever context leaked. Background jobs are less exercised in review than request paths, so this is where scoping bugs hide.

**Resolution:** `TenantTask` base class serialises tenant context into task headers at publish and re-establishes it in `before_start`; a task without context **refuses to start and dead-letters**. Cross-tenant tasks must set `requires_tenant = False` explicitly, are CODEOWNERS-reviewed, and must iterate organizations entering context per organization. `TenantScopedManager` raises rather than returning empty, so an unscoped query is loud. Metric `tenant_context_missing_total` pages on any non-zero value.

**Status: Resolved by decision.** → [03](03-multi-tenancy.md)

---

### R-08 — Money represented as floating point

**Review:** concurrency · correctness
**Scenario:** a float amount somewhere in pricing, invoicing, commission or ledger code produces rounding errors that compound. Customers are billed incorrectly; partner commissions do not reconcile; ledgers do not balance. Extremely expensive to correct retroactively.

**Resolution:** money is always `(integer minor units, CurrencyCode)` via `contracts.values.Money`, which refuses cross-currency arithmetic. Commission rates are integer basis points. A fitness test rejects `FloatField` on any monetary column and bare `Decimal` amounts in event payloads and serializers.

**Status: Resolved by decision.** → [05](05-commercial-model.md)

---

## 4. P1 risks

### R-09 — Broker capability mismatch (Celery + SQS)

**Review:** dependency · scalability
Celery's SQS transport has real limitations: no broker-side priority, no remote control or worker events, visibility-timeout semantics that interact awkwardly with long tasks, and FIFO-queue throughput caps if ordering is needed. Our design assumes per-aggregate ordering via partition key, which standard SQS does not provide.

**Mitigation:** consumers are idempotent and order-tolerant by design, so ordering is a *preference*, not a requirement. **Phase 1 Sprint 0 decision spike** comparing SQS against AmazonMQ (RabbitMQ) on ordering, priority, observability and operational cost, before any queue code is written.
**Owner:** Platform · **Due:** Phase 1 Sprint 0

### R-10 — Organization suspension enforced inconsistently

**Review:** threat · failure-mode
`suspended` must block every write path — API, tasks, event handlers, workflows, connectors. Implementing this as a per-view check guarantees paths will be missed, and a missed path means a non-paying customer keeps writing, or worse, an inconsistent half-suspended state.

**Mitigation:** enforcement must be **structural**, at two chokepoints: the tenancy middleware (rejects unsafe HTTP methods) and `TenantScopedModel.save()`/`delete()` (raises when the context's organization is not write-eligible). A fitness test asserts no product implements its own suspension check. The design is decided; the specific mechanism needs a design review in Phase 1 because `save()`-level enforcement interacts with bulk operations.
**Owner:** Platform · **Due:** before the first product write path ships

### R-11 — Tenant purge incomplete or non-resumable

**Review:** data isolation · failure-mode
Purge spans every module's tables plus S3, search and caches, across potentially hundreds of millions of rows. A naive cascade will exceed statement timeouts and leave partial state — the worst outcome, since the customer believes their data is gone.

**Mitigation:** purge is a resumable workflow with per-module checkpoints, executed in reverse dependency order, with an explicit retention allowlist (audit, billing, commission). Every module must implement export and purge contracts as a **release requirement**. Needs a verification harness that proves completeness on a seeded tenant.
**Owner:** Platform · **Due:** before first production customer

### R-12 — Operational load of seven queues and multiple pools for a small team

**Review:** dependency · scalability
The bulkhead design is correct but assumes operational maturity we do not yet have. Seven worker services, multiple pools, PgBouncer, breakers and shedding thresholds are a lot of surface for a small team.

**Mitigation:** start with **three** queue groups (`default`, `events`, `billing`) and split further on measured evidence. The *code-level* queue assignment is per-task from day one, so splitting later is a deployment change, not a code change. Autoscale on queue age. Runbook for every alert.
**Owner:** SRE · **Due:** Phase 1

### R-13 — Event schema drift between publisher and consumer

**Review:** dependency
Schemas live in `contracts/`, but nothing at runtime forces a published payload to match its registered schema. A publisher adding a field with a changed meaning breaks consumers silently.

**Mitigation:** validate payloads against the registered schema **at publish time** (fail fast, in the publisher's transaction) and at consume time; CI compatibility check against `main`; a metric per `(event_type, version)` so deprecation can be retired safely.
**Owner:** Platform · **Due:** with the first published event

### R-14 — Entitlement check latency on the hot path

**Review:** scalability · concurrency
Several entitlement and authorization checks per request, at a 1 ms p50 budget, is aggressive. If the cache under-performs, every endpoint in the platform slows simultaneously.

**Mitigation:** request-local cache first, then version-keyed Redis, then a single-row snapshot. Benchmark as a Phase 1 Sprint 0 task with a realistic dataset before products are built on it. Metric `entitlement_cache_hit_ratio` with an SLO.
**Owner:** Platform · **Due:** Phase 1 Sprint 0

### R-15 — Break-glass process becomes routine

**Review:** threat
The control depends on break-glass being rare and reviewed. If support cannot work without it, it will be used constantly, reviews will become rubber-stamping, and the boundary that makes a compromised staff account survivable will be gone.

**Mitigation:** invest in **non-impersonating diagnostics** — a tenant health dashboard, structured error context, configuration views that show settings without business data. Track break-glass rate as a product metric; a rising rate means the diagnostic tooling is inadequate, not that the policy is wrong. Monthly review.
**Owner:** Security + Support · **Due:** ongoing

### R-16 — Analytics read model underspecified

**Review:** scalability
Cross-product reporting is assigned to an event-fed analytics read model, but its storage, schema and consistency guarantees are not designed. Customers will ask for cross-product reports early, and the pressure to "just join the tables" will be strong — which would silently destroy the boundaries.

**Mitigation:** design in Phase 1 before the second product ships. Until then, cross-product reporting is explicitly **not offered** rather than implemented by joining across modules. The prohibition is more important than the solution.
**Owner:** Architecture · **Due:** before the second product

### R-17 — Idempotency coverage gaps

**Review:** concurrency
Idempotency is specified per surface, but any missed mutating path can double-charge, double-post or double-pay under retry.

**Mitigation:** a checklist applied at code review for every mutating endpoint, task and handler; the `ProcessedEvent` helper makes the event path idempotent by default; payout and payment paths additionally carry database-level unique constraints so the defence does not depend on application logic.
**Owner:** Platform · **Due:** Phase 1

---

## 5. P2 risks and accepted limitations

| # | Risk | Accepted because | Revisit trigger |
|---|---|---|---|
| R-18 | `Role` has a nullable `organization_id` — the one plane-rule exception | Two parallel tables plus a polymorphic join is materially worse; made safe by a partial RLS policy that forbids the app role from writing system roles | If another nullable-tenant table is proposed |
| R-19 | Postgres FTS may not scale | Sufficient to millions of rows per tenant; `SearchIndex` port exists for a clean swap | p95 > 1 s, or cross-product search is sold |
| R-20 | Single region | Multi-AZ covers reliability; residency is a contract requirement we do not yet have | First residency requirement; `residency_region` already exists |
| R-21 | Organization hierarchy semantics deferred | Recursive RLS predicates are a correctness and performance hazard | First multi-entity customer; explicit `OrganizationLink` grants |
| R-22 | Books duplicates a minimal party record rather than requiring CRM | Required for independent sale; deduplication offered as a feature when both are entitled | If duplication causes measured data-quality problems |
| R-23 | Eventual consistency visible to users across products | Inherent to independent products; must be designed into the UI | If a workflow genuinely requires synchronous cross-product results — then it is one product |
| R-24 | Single database is a shared resource | Bulkheads, pools, replica, statement timeouts; scaling path documented | Primary > 70 % after tuning |
| R-25 | Sub-partner depth capped at 2 | Deeper trees make commissions unexplainable | A distributor contract requires depth 3 |
| R-26 | No native mobile app | Responsive web first; the public API treats a native client like any other | Market demand |
| R-27 | AI abstraction may lag provider features | Narrow port; documented escape hatch under review | A specific capability blocks a product requirement |
| R-28 | Three feature kinds only (no JSON config) | Keeps packaging comparable and displayable | A genuine requirement no combination of the three can express |

---

## 6. Review-by-review summary

| Review | Principal finding | Status |
|---|---|---|
| **Threat** | Highest risks are cross-tenant exposure (R-01, R-02, R-05), staff over-access (R-15) and partner over-reach. Controls: five isolation layers, default-no-access for staff, customer-issued partner grants. | Addressed |
| **Concurrency** | Thirteen contention points identified with a mitigation each ([10](10-performance-concurrency.md)). Highest: seat allocation, document numbering, stock levels, ledger balances. Ledgers are append-only with snapshots rather than mutable balances. | Addressed |
| **Failure-mode** | Only PostgreSQL primary loss is a true outage. Degradation matrix defines every other combination. Largest residual is untested failure paths — hence failure tests as release gates. | Addressed |
| **Data isolation** | Application filtering alone is insufficient; RLS plus composite FKs plus detection is the answer. R-01 is the single most dangerous operational detail and carries a mandatory spike. | Addressed with verification pending |
| **Dependency** | Layering is acyclic by construction; products are mutually independent; the one structural change from the brief (entitlements into `platform/`) is what makes `products → commercial` bannable. | Addressed |
| **Scalability** | Steps 1–5 of the scaling path cover a very large range. The realistic near-term limit is operational capacity (R-12), not technical capacity. | Addressed |

---

## 7. Phase 1 Sprint 0 entry conditions

Three verification spikes must complete before feature development begins. None changes the architecture; each proves a mechanism it depends on.

1. **RLS + PgBouncer spike (R-01)** — interleaved-tenant leakage test through a real PgBouncer; decide transaction vs session pooling for the web tier.
2. **Broker decision spike (R-09)** — SQS vs AmazonMQ on ordering, priority, visibility timeouts, observability and cost.
3. **Entitlement latency benchmark (R-14)** — measure the check path against the 1 ms p50 / 5 ms p95 budget with a realistic dataset.

## 7b. Phase 3R — risks opened by the product-boundary correction

Added 2026-09-22 by [ADR-017](adrs/ADR-017-products-are-separate-applications.md).
Products are now separate applications, so seven capabilities that exist
in-process have no remote form. None is a regression — the in-repository
design never needed them — but each blocks serving a separate product. Full
detail in [16 section 14](16-ecosystem-product-boundary.md).

| ID | Risk | Severity | Mitigation / owner |
|---|---|---|---|
| R-20 | **No authentication assertion.** A separate product has no way to learn who the caller is or which organization they act in. Building this badly is the single largest security risk in the ecosystem — a token that omits the organization, or accepts one from a header, re-opens TB-1/TB-2 at the ecosystem level. | **P1** | Design before any product integrates. Organization must be a signed claim, never a header. Audience-scoped, short-lived, revocable. Architecture. |
| R-21 | **No remote entitlement verification.** Products cannot check access over the network. | **P1** | The `get_gate()` registry already isolates callers from the implementation, so this is a new adapter, not a redesign. Architecture. |
| R-22 | **No product registry.** The platform cannot describe, locate, health-check or launch a product. | **P1** | Specified in [16 section 7](16-ecosystem-product-boundary.md). Not implemented. Architecture. |
| R-23 | **Product-side tenant isolation is unverified.** The platform's five layers protect platform data only; a product's own store is its own responsibility, and a leak there damages ecosystem trust regardless of whose code caused it. | **P2** | Publish a certification checklist rather than assume compliance. Do not grant a product production entitlement data until it passes. Architecture + Security. |
| R-24 | **Product health could be wired into readiness by mistake.** An engineer adding product health to `/healthz/ready` would make every product a hard dependency of the platform — the exact failure ADR-012 exists to prevent. | **P2** | Product health is Tier 3 **by definition**; readiness already refuses Tier 3. Keep it that way and review any change to `health.py::readiness`. |
| R-25 | **Documentation drift.** Documents 01, 02, 07, 08 and 14 describe an in-repository product layout. They carry correction banners, but a reader who skips the banner is misled. | **P2** | Rewrite on next substantive edit of each. Until then doc 16 is authoritative. Architecture. |

## 8. Related documents

- [03-multi-tenancy.md](03-multi-tenancy.md), [09-failure-resilience.md](09-failure-resilience.md), [10-performance-concurrency.md](10-performance-concurrency.md), [12-security.md](12-security.md)
- [ARCHITECTURE.md](../../ARCHITECTURE.md)
