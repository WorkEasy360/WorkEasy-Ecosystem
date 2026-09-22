# Workeasy360 — Architecture Rules

**Read this before making any structural change.**
Full documentation: [docs/architecture/](docs/architecture/) · Decisions: [docs/architecture/adrs/](docs/architecture/adrs/)

---

## 1. What Workeasy360 is

> The shared multi-tenant **ecosystem platform** for a family of **independently commercializable** business products (CRM, Books, Inventory, Projects, …), sold through direct and partner channels that are **commercial constructs — never forks or separate deployments**.

**This repository is the platform. Products are separate applications in their own repositories** ([ADR-017](docs/architecture/adrs/ADR-017-products-are-separate-applications.md) · [16 — Ecosystem / Product Boundary](docs/architecture/16-ecosystem-product-boundary.md)). Business-domain code — contacts, deals, invoices, ledgers, stock items, tasks, employees — never lives here.

Four rules follow, and everything else in this document exists to protect them:

1. **A product must ship, run and be sold without any other product.**
2. **A product must never know how a capability was purchased.** It asks "is this org entitled?" and gets a yes/no.
3. **A product integrates through a versioned contract** — never an import, never a shared database, never a platform ORM model. Since Phase 3 that contract is live: OpenID Connect for identity, an HTTP endpoint for entitlement ([docs/phase-3/](docs/phase-3/00-overview.md)).
4. **A partner never receives a copy of the product.** Partners get pricing, branding and relationship metadata on the same running system.

## 2. The module map

```
contracts/     L0   interfaces, event schemas, value objects. NO internal deps, NO Django.
platform/      L1   tenancy, identity, organizations, authz, entitlements,
                    feature_flags, audit, files, notifications, workflows,
                    ecosystem (the remote product front door — ADR-018)
integration/   L2   events + outbox, webhooks, connectors, idempotency
commercial/    L3   catalog, pricing, subscriptions, billing, partners,
                    commissions, provisioning
products/      L3   RESERVED AND EMPTY. Products are separate applications
                    (ADR-017); any file here fails the build.
interfaces/    L4   routing and composition only. No business logic.
```

**Dependencies point downward. Never sideways at L3. Never upward.**

| From | May import | May NOT import |
|---|---|---|
| `contracts/` | nothing internal | everything internal |
| `platform/<m>` | `contracts`, strictly lower platform layers | platform siblings, and everything above |
| `integration/` | `contracts`, `platform` | `commercial`, `products`, `interfaces` |
| `commercial/` | `contracts`, `platform`, `integration` | `products`, `interfaces` |
| `products/<p>` | — (reserved, empty) | everything — a product is a separate application |
| `interfaces/` | everything | — (but no business logic) |

Inside `platform/`: **P0** `tenancy`, `observability` → **P1** `identity`, `organizations` → **P2** `authz`, `entitlements`, `feature_flags`, `audit` → **P3** `files`, `notifications`, `workflows`, `ecosystem`. Lower layers only; siblings communicate via events.

## 3. The eight forbidden edges

| # | Forbidden | Instead |
|---|---|---|
| **F-1** | `products/a` importing `products/b` — for any reason, including type hints | Domain event (writes) or `contracts/` port (reads) |
| **F-2** | `products/*` importing `commercial/*` | `entitlements.check()` / `.feature()` |
| **F-3** | `commercial/*` importing `products/*` | `ProductCode` strings + the manifest registry |
| **F-4** | `platform/*` importing anything above it | Events; the publisher supplies resolved content |
| **F-5** | Same-layer imports inside `platform/` | Events, or orchestration one layer up |
| **F-6** | ORM access across any module boundary | The owning module's selectors, returning DTOs |
| **F-7** | Business logic in views, serializers, admin, tasks or `interfaces/` | Application services |
| **F-8** | Django signals across modules | Explicit service call or an outbox event |

**Cross-module foreign keys** are limited to a frozen allowlist: `Organization`, `User`, `File`. Everything else is a soft reference `(product, entity, id)`. Adding a fourth requires an ADR.

## 4. Architecture fitness rules

These are enforced in CI. A PR that violates one does not merge.

### Boundaries

| # | Rule | Enforced by |
|---|---|---|
| A1 | No cross-product imports | import-linter `independence` |
| A2 | No `products → commercial` imports | import-linter `forbidden` |
| A3 | No `commercial → products` imports | import-linter `forbidden` |
| A4 | `contracts/` imports nothing internal and not Django | import-linter + bare-interpreter test |
| A5 | Layer order is respected | import-linter `layers` |
| A6 | `platform/` internal layers are respected | import-linter `layers` |
| A7 | No cross-module ORM access | AST scan |
| A8 | No cross-module FK outside the allowlist | System check `W360.E003` |
| A9 | No cross-module Django signal receivers | System check `W360.E009` |
| A10 | Only `commercial/provisioning` imports entitlement write services | import-linter `forbidden` |
| A11 | `products/` holds no code; no product package is an installed app | Fitness tests (unconditional — no exemption list) |
| A12 | No platform module branches on a product code | AST scan over `==` and `match` |
| A13 | The platform serves no product-domain route | Fitness test over the URL resolver |
| A14 | `contracts/` declares no product-domain event schema | Fitness test over the event registry |
| A15 | `platform/ecosystem` never imports `commercial/`; the catalog is reached through a `contracts/` port | import-linter `ecosystem-never-sees-commercial` |
| A16 | No product-supplied URL is ever fetched by the platform | Import scan over `platform/ecosystem` |
| A17 | Product health never reaches `/healthz/live`, `/healthz/ready` or `/healthz/startup` | Probe-registry test + a readiness test with an empty registry |
| A18 | Every `platform.integration.*` write permission is `dangerous` | Registry test |
| A19 | The issued token claim set matches the advertised one | Fitness test against the discovery document |

### Tenancy and isolation

| # | Rule | Enforced by |
|---|---|---|
| B1 | Every model with `organization_id` inherits `TenantScopedModel` | `W360.E001` |
| B2 | Every `TenantScopedModel` has an RLS policy with `FORCE`, `USING` and `WITH CHECK` | `W360.E002` + migration-state test |
| B3 | Every index on a tenant table leads with `organization_id` | Fitness test |
| B4 | Every tenant FK to another tenant model has a composite FK | Fitness test |
| B5 | The application role is non-owner and `NOBYPASSRLS` | Startup assertion |
| B6 | Tenant context is set only via `set_config(..., true)` in a transaction | Code review + PgBouncer leakage test |
| B7 | `unscoped` manager use is allowlisted, reason-tagged and audited | AST scan + runtime audit |
| B8 | Every tenant model has an isolation test proving B cannot read A | Fitness test (test must exist) |
| B9 | Celery tasks carry tenant context unless `requires_tenant = False` | `TenantTask` + CODEOWNERS |
| B10 | Redis keys and S3 object keys are tenant-prefixed | Wrapper clients; raw client access linted |

### Identity, authorization, entitlement

| # | Rule | Enforced by |
|---|---|---|
| C1 | No product defines a user model or implements authentication | Fitness test |
| C2 | No product implements billing, pricing or subscription logic | Fitness test + A2 |
| C3 | No product bypasses `entitlements.check()` for gated capability | Entitlement test per product API |
| C4 | Permissions follow `<product>.<resource>.<action>` and are code-declared | Registry check |
| C5 | Feature keys sold in the catalog are declared by their owning product | `W360.E005` |
| C6 | Every endpoint has an authorization test per relevant persona | Generated authorization matrix |
| C7 | Checks live in application services, not only in views | Code review + task-path tests |
| C8 | No code branches on plan or role *names* | AST scan for known literals |

### Events and integration

| # | Rule | Enforced by |
|---|---|---|
| D1 | Cross-module events are versioned (`<type>.v<n>`) | Registry check `W360.E006` |
| D2 | Event schema changes are backward-compatible or bump the version | CI diff against `main` |
| D3 | Events are published via the outbox, in the business transaction | Code review + no-broker-in-transaction test |
| D4 | Event handlers are idempotent and have a duplicate-delivery test | Fitness test (test must exist) |
| D5 | Events are past-tense facts, never commands | Naming check + review |
| D6 | Publish/consume graphs have no undeclared cycles | CI graph check |
| D7 | Contract ports are read-only, nullable and batch-only | Code review |

### Resilience and concurrency

| # | Rule | Enforced by |
|---|---|---|
| E1 | Every external call has explicit connect and read timeouts | `W360.E008` |
| E2 | No external I/O inside a database transaction | Test-suite DB wrapper |
| E3 | Every Celery task declares a queue and time limits | `W360.E007` |
| E4 | Retries are bounded, jittered and never nested | Code review |
| E5 | Background jobs are idempotent where retries are possible | Code review + tests |
| E6 | `transaction.on_commit` is not used for anything that must not be lost | AST scan |
| E7 | A Tier-N dependency never fails a lower-tier operation | Degradation-matrix tests |
| E8 | List endpoints use keyset pagination with a bounded page size | Fitness test |
| E9 | Hot endpoints have query-count assertions (N+1 guard) | Fitness test |

### Data and money

| # | Rule | Enforced by |
|---|---|---|
| F1 | Money is `(integer minor units, CurrencyCode)`; never a float | Fitness test on field types and payloads |
| F2 | Financial and audit records are append-only; corrections are reversals | Schema grants + review |
| F3 | Every module implements export and purge contracts | Release checklist + contract test |
| F4 | PII fields are annotated at the schema level | Fitness test on designated models |
| F5 | Published catalog versions and prices are immutable | DB rule + application guard |

### Code organisation

| # | Rule | Enforced by |
|---|---|---|
| G1 | No `utils.py`, `helpers.py`, `common.py`, `misc.py` | Filename check |
| G2 | No `products/shared/` module | Path check |
| G3 | `domain/` contains no Django imports | import-linter |
| G4 | Views call at most one application service | Fitness test |
| G5 | Selectors start from a `visible_*` predicate, not `.objects.all()` | Fitness test |
| G6 | No writable serializer uses `fields = "__all__"` | AST scan |
| G7 | No raw SQL built with f-strings | AST scan |

Run locally: `make lint-arch` · `make test-arch` · `python manage.py check --deploy`

## 5. The escape hatch

Rules are occasionally wrong. To add an exception:

1. Write an ADR: the edge, why, what was tried, the cost of not having it.
2. Architecture review (`CODEOWNERS`).
3. Add it **narrowly** to `.importlinter` — specific module to specific module, never a wildcard.
4. Add a dated entry to `docs/architecture/debt-register.md` with an owner and a review date.

Suppressions (`# noqa: arch`) require a ticket reference and **fail CI after 90 days**. This is deliberately more work than doing it correctly.

## 6. Where code goes

```
<module>/
  models/      ORM: structure and invariants only
  domain/      Pure Python. No Django. Fast tests.
  services/    Use cases. Own transactions. Publish events.
  selectors/   Read queries. Return DTOs. No writes.
  api/         Thin HTTP adapters
  tasks/       Thin Celery wrappers
  events/      published.py (schemas) + handlers.py
  authz.py     visible_* predicates and object rules
  manifest.py  product code, features, publishes, consumes
```

Services are `verb_noun` (`mark_deal_won`). Selectors are `noun_phrase` (`deals_for_pipeline`). The name tells you whether it writes.

## 7. Checklist before you open a PR

- [ ] Which single module **owns** this? Is it written only by that module?
- [ ] Does it cross a forbidden edge? (`make lint-arch`)
- [ ] Tenant-owned? Then `TenantScopedModel`, RLS policy, `organization_id`-leading index, composite FK, isolation test.
- [ ] Entitlement checked before permission, in the **service**, not only the view?
- [ ] New event? Versioned, schema in `contracts/`, declared in the manifest, handler idempotent with a duplicate test.
- [ ] External call? Explicit timeouts, bounded retries, outside any transaction.
- [ ] Task? Explicit queue, time limits, idempotent, tenant context.
- [ ] Money? Integer minor units with a currency.
- [ ] List endpoint? Keyset pagination, `visible_*` predicate, query-count assertion.
- [ ] **If this module were extracted to its own service in three years, what breaks?**

That last question is the cheapest architectural test available. "Nothing — it only uses contracts and events" means the boundary is right.

## 8. Document index

| Doc | Covers |
|---|---|
| [00 System Context](docs/architecture/00-system-context.md) | Actors, external systems, trust boundaries, control vs data plane |
| [01 Domain Boundaries](docs/architecture/01-domain-boundaries.md) | Modules and what each owns |
| [02 Dependency Rules](docs/architecture/02-dependency-rules.md) | Allowed and forbidden edges, enforcement |
| [03 Multi-Tenancy](docs/architecture/03-multi-tenancy.md) | Organization model, five isolation layers, RLS |
| [04 Identity & Authz](docs/architecture/04-identity-authz.md) | Authentication, RBAC, the seven personas, break-glass |
| [05 Commercial Model](docs/architecture/05-commercial-model.md) | Catalog, pricing, bundles, partners, commissions |
| [06 Entitlements](docs/architecture/06-entitlements.md) | Subscriptions, entitlement lifecycle, seats |
| [07 Cross-Product Integration](docs/architecture/07-cross-product-integration.md) | Events, outbox, ports, idempotency |
| [08 Data Ownership](docs/architecture/08-data-ownership.md) | Ownership matrix, name-collision analysis |
| [09 Failure & Resilience](docs/architecture/09-failure-resilience.md) | Tiers, bulkheads, degradation matrix |
| [10 Performance & Concurrency](docs/architecture/10-performance-concurrency.md) | Pools, pagination, contention points, idempotency |
| [11 Observability](docs/architecture/11-observability.md) | Logs, traces, metrics, health, alerting, audit |
| [12 Security](docs/architecture/12-security.md) | Threat model, secrets, SSRF, uploads, webhooks, admin |
| [13 Deployment & Evolution](docs/architecture/13-deployment-evolution.md) | Topology, scaling path, extraction criteria |
| [14 Repository Structure](docs/architecture/14-repository-structure.md) | Folder layout, layer responsibilities, naming |
| [15 Risk Register](docs/architecture/15-risk-register.md) | P0/P1/P2 risks and open verification |
| [16 Ecosystem / Product Boundary](docs/architecture/16-ecosystem-product-boundary.md) | **What belongs in this repository and what does not** |
| [ADR-001 … ADR-020](docs/architecture/adrs/) | The twenty foundational decisions |
| [Phase 3 — Remote Integration](docs/phase-3/00-overview.md) | How an external product connects: registry, SSO, entitlement API, key management |
| [Product Integration Guide](docs/phase-3/09-product-integration-guide.md) | **Start here if you are building a product** |
