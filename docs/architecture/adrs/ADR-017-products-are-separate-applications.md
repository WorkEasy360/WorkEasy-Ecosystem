# ADR-017 — Products are separate applications, not packages in this repository

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Product direction + Architecture
**Supersedes:** the in-repository placement of products assumed by
[ADR-001](ADR-001-modular-monolith-first.md) and documented in
[01](../01-domain-boundaries.md), [02](../02-dependency-rules.md),
[07](../07-cross-product-integration.md) and [14](../14-repository-structure.md).
**Does not supersede:** ADR-001's *kernel* argument — platform, integration and
commercial remain one deployable modular monolith.

---

## Context

Phase 0 designed Workeasy360 as a modular monolith in which product domains
(`products/crm/`, `products/books/`, …) live beside the platform kernel,
separated by import contracts rather than by process boundaries. Phase 3
implemented CRM under that design: ~9,900 lines, 17 models, 53 routes, 30
permissions, 8 event schemas, all of it inside this repository.

It worked. All 11 import-linter contracts held; the product imported nothing
from `commercial/` and reached entitlements only through a `contracts/` port;
tenant isolation held across 17 new tables. **The architecture was not the
problem.**

The **product strategy** changed. Workeasy360 is to be the shared ecosystem
platform — an app hub — and each business product is to be built, deployed,
versioned and potentially owned by a different team, in its own repository.

Keeping product code here under the new strategy would mean:

- a product's release cadence coupled to the platform's, and vice versa;
- one test suite, one migration history and one deploy for unrelated domains;
- every product team holding write access to the platform kernel;
- the platform repository growing without bound as products are added.

## Decision

**Product business domains are separate applications in separate
repositories. This repository contains the platform only.**

1. All CRM domain code is removed from this repository (models, migrations,
   services, selectors, APIs, permissions, tasks, events, tests, docs).
2. `products/` remains as a **reserved, permanently empty namespace**, purely
   so the independence contracts and the placeholder fitness test have modules
   to bind to. Any file other than `__init__.py` appearing there fails the
   build, unconditionally.
3. Products integrate through **versioned, network-reachable contracts** —
   never a Python import, never a shared database. The forbidden edges are
   specified in [16 §5–6](../16-ecosystem-product-boundary.md).
4. `commercial/catalog` keeps `Product`, `Feature`, plans, price books and
   bundles. **Selling** a product stays a platform concern; **implementing**
   one does not. `Product(code="crm")` is a row, not a module reference —
   which is what [01 §4](../01-domain-boundaries.md) already required.
5. Platform hardening discovered while building CRM is **kept**, and recorded
   as such in [Phase 3R status](../phase-3R/phase-3R-status.md).

## Alternatives considered

**Keep CRM here and extract it later.** Rejected. The extraction cost grows
monotonically with every feature, every migration and every test; and the
entire point of the Phase 0 boundary work was to make extraction cheap
*while it still is*. Nine thousand lines is the cheapest this will ever be.

**Delete `products/` entirely.** Rejected, narrowly. The directory is what
`test_products_are_structural_placeholders_only` and four import-linter
contracts bind to; removing it would delete the very guard that stops a
product reappearing here. An empty, documented, test-enforced namespace is a
stronger boundary than no namespace. Revisit if the guard is ever expressed
another way.

**Keep the CRM event schemas in `contracts/` for future products to consume.**
Rejected. Nothing consumes them; they describe one product's internals; and
`contracts/` is the layer with the strictest purity rules in the system. A
genuinely shared cross-product event contract must be a deliberate,
separately-governed artefact — not a file that accumulates whatever the
newest product happened to publish.

**Build SSO, the product registry and the launcher now.** Rejected as scope.
This was a scope correction, not a feature phase. The gaps are specified in
[16 §7, §8, §14](../16-ecosystem-product-boundary.md) so the next phase
starts from a known set rather than rediscovering them.

## Consequences

**Positive**

- The repository states one thing and means it: this is the platform.
- Products gain independent release, scaling, ownership and technology
  choices; one product's outage cannot take the ecosystem down (16 §10).
- The platform shrinks by ~10,500 lines and 17 tables, and its test suite
  covers only platform behaviour.
- `F-1` (no product imports another product) becomes structurally true rather
  than merely enforced.

**Negative / accepted**

- Cross-product features now need a network contract where an import would
  have done. This is the cost being paid deliberately.
- Seven capabilities that exist in-process have no remote form yet (16 §14).
  Until they do, **no separate product can actually be served** — the
  boundary is correct but incomplete, and that is the next phase's work.
- A product's tenant isolation is now the product's responsibility. The
  platform's five-layer model protects platform data only. This needs to
  become a certification checklist, not an assumption.
- ADR-013's `workeasy360.` namespace and the layer rules are unchanged, but
  [14 Repository Structure](../14-repository-structure.md) now describes a
  layout wider than this repository. It carries a correction banner.

## Enforcement

| Rule | Enforced by |
|---|---|
| No product code in this repository | `test_products_are_structural_placeholders_only` (unconditional) |
| No product package is an installed app | `test_no_product_apps_are_installed` |
| No platform module branches on a product code | `test_no_platform_module_branches_on_a_product_code` |
| No product-domain routes | `test_api_exposes_no_product_domain_routes` |
| No product-domain event schemas in `contracts/` | `test_contracts_declares_no_product_domain_events` |
| No product imports another product | import-linter `products-are-independent` |
| Products never import `commercial` | import-linter `products-never-see-commercial` |
