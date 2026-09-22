# Phase 3R — Platform Scope Audit & Cleanup

**Date:** 2026-09-22
**Baseline commit:** `34cf7a3` (Phase 2: commercial core)
**Type:** scope correction + architecture cleanup. No features added.
**Decision of record:** [ADR-017](../architecture/adrs/ADR-017-products-are-separate-applications.md)
**New boundary specification:** [16 — Ecosystem / Product Boundary](../architecture/16-ecosystem-product-boundary.md)

---

## 1. What changed and why

Phase 3 built CRM inside this repository, and it passed: 869 tests, 11/11
import contracts, tenant isolation across 17 new tables. The **architecture
was not the problem** — the **product strategy changed**. Workeasy360 is the
shared ecosystem platform; each business product is a separate application in
its own repository.

This phase removed CRM's business domain from the platform, kept the platform
hardening that Phase 3 uncovered, and put guards in place so product code
cannot drift back in unnoticed.

## 2. Classification of the repository as audited

| Layer | Classification | Disposition |
|---|---|---|
| `contracts/` (enums, errors, values, ports, event envelope/registry) | Platform core | **Kept** |
| `contracts/events/product_events.py` | Product domain | **Removed** |
| `platform/` — tenancy, observability, identity, organizations, authz, entitlements, audit, files, notifications, feature_flags, workflows | Platform core | **Kept** |
| `platform/tenancy/migrations/0005_crm_isolation.py` | Product domain | **Removed** |
| `integration/` — events/outbox, idempotency, webhooks, connectors | Ecosystem integration | **Kept** |
| `commercial/` — catalog, pricing, billing, subscriptions, provisioning, partners, commissions | Commercial core | **Kept** |
| `interfaces/` — api/v1, admin, partner | Platform core | **Kept** (one product route removed) |
| `products/crm/**` | Product domain | **Removed** |
| `products/{books,inventory,…}/__init__.py` | Reserved namespace | **Kept, re-documented** |
| `tests/` — architecture, isolation, integration, performance, security, observability | Platform core | **Kept** (CRM suites removed, guards added) |
| `tests/spikes/` | Verification evidence | **Kept** |

No dead or obsolete code was found outside the product-domain set. Nothing was
removed on suspicion alone.

## 3. What was removed

| Category | Detail |
|---|---|
| Python files | 79 (all of `products/crm/` except `__init__.py`, plus `contracts/events/product_events.py`, `platform/tenancy/migrations/0005_crm_isolation.py`, 3 cross-cutting CRM test modules) |
| Models | 17 (`Company`, `Contact`, `CompanyContact`, `Lead`, `Pipeline`, `PipelineStage`, `Deal`, `DealStageHistory`, `DealContact`, `Activity`, `Note`, `Tag`, `CompanyTag`, `ContactTag`, `LeadTag`, `DealTag`, `CustomFieldDefinition`) |
| Tables | 17 (`crm_*`), plus their RLS policies, composite FKs, append-only trigger and indexes |
| Migrations | 2 (`products/crm/migrations/0001_initial.py`, `platform/tenancy/migrations/0005_crm_isolation.py`) |
| API routes | 53, under `/api/v1/orgs/<slug>/crm/` |
| Permissions | 30 (`crm.company.*`, `crm.contact.*`, `crm.lead.*`, `crm.deal.*`, `crm.pipeline.*`, `crm.activity.*`, `crm.note.*`, `crm.customfield.*`, `crm.bulk.manage`, `crm.report.view`) |
| Celery tasks | 1 (`crm.activities.sweep_overdue`) + its Beat entry + `W360_BEAT_CRM_OVERDUE_SWEEP_S` |
| Event schemas | 8 (`crm.company.created`, `crm.contact.created`, `crm.lead.converted`, `crm.deal.created`, `crm.deal.stage_changed`, `crm.deal.won`, `crm.deal.lost`, `crm.activity.completed`) |
| Notification templates | 1 (`crm.activity.assigned`) |
| Metrics | 1 (`crm_overdue_activities_found_total`) |
| Settings | `PRODUCT_APPS` entry, Beat schedule entry |
| Test fixtures | `crm_org`, `crm_two_orgs` (root `conftest.py`), `ALL_CRM_PERMISSIONS` |
| Docs | 17 Phase 3 documents archived with a SUPERSEDED banner |

## 4. Phase 3 platform improvements **preserved** — and why each is generic

These were discovered while building CRM. None of them is about CRM.

| # | Change | Why it is platform-generic |
|---|---|---|
| 1 | **Entitlement runtime registry** — `register_gate()` / `get_gate()` / `reset_gate()` in `contracts/ports/entitlements.py`, registered by `platform/entitlements/apps.py::ready()` | This is the mechanism that lets *any* caller reach entitlements without naming an implementation. It is the same shape `platform/observability/health.py::register_probe` already used. Under ADR-017 it becomes load-bearing: because no caller names the implementation, replacing the in-process gate with an HTTP client is a registration change, not a caller change. It is the single most important thing Phase 3 left behind. |
| 2 | **`.env` leakage fix in the production-settings security tests** (`tests/security/test_security_baseline.py`) | A developer's local `.env` — created by the documented `cp .env.example .env` workflow — was backfilling, via `Env.load_dotenv()`'s `setdefault`, the very setting each test had just deleted to prove production refuses to start without it. The tests passed while asserting nothing. This is a **false-green in a security gate** and has nothing to do with CRM. |
| 3 | **`constraint_name` override on `add_tenant_fk`** (`platform/tenancy/migrations_helpers.py`) | The default constraint name is derived from table and column alone, so any table needing two composite FKs on one column collides on it. That is a sharp edge in a platform DDL helper, not a CRM fact. Kept, docstring made product-neutral, and now covered by `platform/tenancy/tests/test_migrations_helpers.py` so it is not untested surface. |
| 4 | **Outbox unknown-event-type test no longer depends on a product** (`integration/events/tests/test_outbox.py`) | It asserted that `crm.deal.won` was unregistered — making a platform test depend on a product never registering an event. It now uses a namespace no product can ever claim. Strictly more robust. |
| 5 | **`requirements-dev.txt` corruption fix** | The file ended with a stray `` `   ` `` line that would break `pip install -r`. Unrelated to CRM; a real defect. |

## 5. Architecture guards added (STEP 24)

Five fitness tests now hold the boundary. Each was verified to **fail on a
real violation** and to **not fire on legitimate catalog data**:

| Test | Holds |
|---|---|
| `test_products_are_structural_placeholders_only` | `products/` stays empty. **Unconditional** — Phase 3's `IMPLEMENTED_PRODUCTS` exemption is gone and no replacement should be added. |
| `test_no_product_apps_are_installed` | `PRODUCT_APPS == []`; no `.products.` package is an installed Django app. |
| `test_no_platform_module_branches_on_a_product_code` | No `if code == "crm"` anywhere outside `products/`. AST-based over `==` and `match`, so docstrings, comments and `Product(code="crm")` fixture data do not trip it. |
| `test_api_exposes_no_product_domain_routes` | No product-named route is served by the platform (24 routes enumerated, so not vacuous). |
| `test_contracts_declares_no_product_domain_events` | No product-namespace event schema is registered (17 schemas registered across `identity`, `organizations`, `entitlements`, `commercial`). |

`test_only_non_product_namespaces_register_permissions` was **reverted** to
`{"platform", "commercial"}` — Phase 3 had widened it to admit `crm`.

## 6. Migration safety decision (STEP 5)

**Decision: clean removal. No forward "drop table" migration was written.**

Evidence, all three independently sufficient:

1. **Never committed.** `products/crm/migrations/0001_initial.py` and
   `platform/tenancy/migrations/0005_crm_isolation.py` were untracked in git
   at `34cf7a3`. No branch, tag or commit in this repository has ever
   contained them.
2. **No deployment path exists.** `.github/workflows/ci.yml` defines
   `format-and-lint`, `typing`, `architecture`, `secret-scan`, `sast`,
   `dependency-audit`, `tests` and `gate` — and no deploy, release or
   registry-push job. `infra/` contains only local PgBouncer and Postgres
   development configuration.
3. **The only database that ever held `crm_*` tables** is the local
   developer Docker volume (`w360_pgdata`), which was dropped and rebuilt
   from zero as part of this phase's verification.

This is the greenfield/pre-production case. Writing a forward migration to
drop tables that exist in no deployed environment would add permanent noise
to the migration graph in exchange for nothing. Django's migration history
was **not** edited — the two files simply never existed outside a working
tree.

`platform/tenancy/migrations/` therefore ends at `0004_commercial_history_immutable`,
with no gap.

## 7. Product-domain residue, classified (STEP 33)

A repository-wide search for `crm`, `company`, `contact`, `lead`, `deal`,
`pipeline` leaves the following. Each has a documented reason to exist.

| Where | What | Why it stays |
|---|---|---|
| `conftest.py` (commercial fixtures) | `Product(code="crm")`, `crm.pipelines.max`, plan `crm_starter` / `crm_professional` | **Catalog data, not code.** This is precisely what the catalog is for — a SKU with a code and feature keys. The fixtures exercise catalog, pricing, subscription and provisioning behaviour; the product code is an opaque string to all of them. |
| `commercial/`, `platform/`, `contracts/` docstrings | `crm.deal.won`, `crm.pipelines.max`, `crm.deal.update` as illustrative examples of event, feature and permission key shapes | Naming-convention documentation. CRM is the canonical example product in every architecture document; an example is not a dependency. Verified by AST that no branch compares against them. |
| `platform/authz/permissions.py` | `EXAMPLE_PRODUCT_PERMISSIONS` including six `crm.*` keys | Explicitly **unregistered** and asserted so. Restored from Phase 1 — it is the naming convention, and under ADR-017 CRM is exactly the kind of product that will declare permissions through the registration contract rather than here. |
| `platform/observability/middleware.py` | `/api/v1/orgs/{slug}/crm/deals/{id}` in a docstring about route templating | Illustrates why paths are templated before becoming metric labels. Cardinality documentation. |
| `docs/architecture/**` | CRM used throughout as the example product | Frozen Phase 0 design documents. Correction banners added where they assert products live *in this repository*. |
| `docs/phase-3-crm-superseded/**` | Everything | Archived historical audit evidence, every file banner-marked SUPERSEDED. |

No product-domain **code** remains.

## 8. Answers to the exit questions

| Question | Answer |
|---|---|
| Can CRM be developed in a completely separate repository? | **Yes** — nothing in this repository refers to CRM code. |
| Can Books / Inventory / each product be developed separately? | **Yes** — identically; none was ever started here. |
| Can each product deploy independently? | **Yes**, structurally. It needs the remote contracts in §14 of doc 16 before it can *serve users*. |
| Can all products share Workeasy360 identity? | **Model: yes.** Mechanism: not yet — no token/assertion exists (gap G-1). |
| Can each use one organization identity? | **Yes** — `organization_id` is stable, platform-minted ecosystem identity. |
| Can Workeasy360 determine product access? | **Yes** — `platform/entitlements` is complete and is the single read model. |
| Can products be sold individually? | **Yes** — `commercial/catalog` + `pricing` + `subscriptions`. |
| Can products be bundled? | **Yes** — `Bundle`, `BundleItem`, most-generous-wins resolution. |
| Can the platform display products in one app ecosystem? | **Not yet** — no product registry or launcher metadata (gaps G-3, G-4). Specified in doc 16 §7, deliberately not built. |
| Can one product fail without crashing the ecosystem? | **Yes**, by construction — the platform holds no reference to any product. Readiness already refuses Tier-3 dependencies, and product health is Tier 3 by definition. |
| Can a new product be added without editing core product-specific conditionals? | **Yes** — verified by AST that no such conditional exists anywhere in the platform. |

## 9. Carried forward

Every gap below is work the in-repository design never needed. None is a
regression. Full detail in [16 §14](../architecture/16-ecosystem-product-boundary.md).

| # | Gap | Severity |
|---|---|---|
| G-1 | No remote authentication assertion / SSO | P1 for the next phase |
| G-2 | No HTTP entitlement-verification endpoint | P1 for the next phase |
| G-3 | No product registry / manifest submission | P1 for the next phase |
| G-4 | No app directory / launcher metadata API | P2 |
| G-5 | No outbound webhook delivery to product subscribers | P2 |
| G-6 | No HTTP surface for files or notifications | P2 |
| G-7 | Permission registry is in-process only | P2 |
| G-8 | Product-side tenant isolation is unverified by the platform; needs a certification checklist rather than an assumption | P2 |

None of these blocks the cleanup. All of them block *serving a separate
product*, which is the next phase's subject.

## 10. Decisions frozen for the next platform phase

1. **Products are separate applications.** Not negotiable without a new ADR
   superseding ADR-017.
2. **`products/` stays as a reserved, empty namespace** with an
   unconditional fitness test. Do not add an exemption; if a product needs
   code, it needs a repository.
3. **The catalog keeps `Product`/`Feature`/plans/bundles.** Selling a product
   is a platform concern; implementing one is not.
4. **`organization_id` is the ecosystem tenant identity.** Products reference
   it and own their own records.
5. **Contracts are reached through registries, never by naming an
   implementation.** `get_gate()` is the pattern; the remote forms follow it.
6. **Product event schemas do not live in `contracts/`.** A genuinely shared
   cross-product contract must be separately governed and versioned.
7. **Product health is Tier 3.** It may never influence `/healthz/ready`.
8. **The platform must be provable with zero products reachable** — login,
   org management, catalog, purchase, billing, launcher.
