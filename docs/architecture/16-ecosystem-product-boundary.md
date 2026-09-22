# 16 — Ecosystem / Product Boundary

**Status:** Accepted (Phase 3R — scope correction)
**Owner:** Architecture
**Last reviewed:** 2026-09-22
**Supersedes:** the in-repository product assumption in
[01](01-domain-boundaries.md) §5, [02](02-dependency-rules.md) §2,
[07](07-cross-product-integration.md) and [14](14-repository-structure.md) §2–3.
See [ADR-017](adrs/ADR-017-products-are-separate-applications.md).

---

## 1. Purpose

Phase 0–2 assumed every product would be a package inside this repository
(`products/crm/`, `products/books/`, …). Phase 3 built CRM that way, and it
worked — 11/11 import contracts held, tenancy held, entitlements held. The
design was sound; the **product strategy changed**.

Workeasy360 is now **only the shared ecosystem platform**. Products — CRM,
Books, Inventory, Projects, Marketing, Support, HR, Payroll, Commerce — are
built, deployed and versioned as **separate applications in their own
repositories**. This repository holds the platform they plug into and nothing
a single product owns.

This document defines that boundary: what the platform owns, what a product
owns, and the contract between them.

## 2. The one-line rule

> **The platform owns identity, tenancy, access and commerce.
> A product owns its business domain and its own data store.
> They meet at a versioned contract, never at an import and never at a table.**

## 3. Ownership split

| Concern | Owner | Notes |
|---|---|---|
| User identity, credentials, MFA, sessions | **Platform** | One account across the ecosystem |
| Organization, membership, org lifecycle | **Platform** | `organization_id` is the ecosystem tenant identity |
| Roles, permission registry, permission checks | **Platform** | Products *declare* a namespace; platform *stores and evaluates* |
| Product catalog, plans, features, price books, bundles | **Platform** (`commercial/`) | `Product(code="crm")` is a **SKU**, not code |
| Subscriptions, billing, licensing, seats | **Platform** | |
| Entitlements (the read model products check) | **Platform** (`platform/entitlements`) | |
| Events, outbox, idempotency, webhooks | **Platform** (`integration/`) | Transport and durability |
| Notifications delivery, file storage, audit trail | **Platform** | Capability, not trigger |
| Observability primitives, health, correlation | **Platform** | Ecosystem health; product-internal health stays product-side |
| Partner / reseller / white-label / affiliate | **Platform** (`commercial/`) | Commercial constructs, never forks |
| App registration, app directory, launcher metadata | **Platform** (`platform/launcher`, Phase 4) | |
| Contacts, deals, invoices, ledgers, items, tasks, employees | **Product** | Never in this repository |
| Product HTTP routes, serializers, screens | **Product** | Platform routes nothing product-shaped |
| Product database schema and migrations | **Product** | Its own store (§9) |
| Product background jobs | **Product** | Its own worker fleet |
| The business *reason* to notify someone | **Product** | Platform owns *delivery* |

**Test for any new code in this repository:** would *two independent
products* both need this, for reasons that are not a coincidence? If not, it
belongs to the product.

## 4. What a product may depend on

A product integrates through a **stable, versioned, network-reachable
contract** — never by importing Python from this repository, never by
connecting to the platform database, never by reading a platform ORM model.

| Need | Mechanism | Status |
|---|---|---|
| Who is the caller? | Token introspection / identity assertion | **Gap — §8** |
| Which organization? | `organization_id` claim in the token | **Gap — §8** |
| May this org use me? | Entitlement verification endpoint | Model exists (`platform/entitlements`); HTTP surface is a **gap** |
| May this user do X? | Permission evaluation endpoint, or claims in the token | Registry exists (`platform/authz`); HTTP surface is a **gap** |
| Tell the ecosystem something happened | Publish a versioned event | Outbox + registry exist (`integration/events`) |
| React to something | Subscribe to a versioned event / receive a webhook | Outbox exists; webhook delivery is a **gap** |
| Store a file | File contract | Port exists (`contracts/ports/files.py`); HTTP surface is a **gap** |
| Notify a user | Notification contract | Port exists (`contracts/ports/notifications.py`); HTTP surface is a **gap** |
| Describe myself to the launcher | Product manifest registration | **Gap — §7** |

The `contracts/` package already states these as protocols. What does not yet
exist is the **remote** form of them. That is the single largest piece of
work the next platform phase owns, and it is deliberately not built here
(this was a cleanup, not a feature phase).

### 4.1 The pattern that makes it work today

`contracts/ports/entitlements.py` carries a **runtime registry**
(`register_gate` / `get_gate`). `platform.entitlements` registers its concrete
implementation at `AppConfig.ready()`; a caller asks `get_gate()` and never
learns who answered. `platform/observability/health.py::register_probe` uses
the identical shape.

This is the in-process rehearsal of the remote contract: the *caller* side of
the code does not name an implementation. Replacing the local gate with an
HTTP client is a registration change, not a caller change. **Preserve this
pattern.** It is what makes §8 a bounded piece of work rather than a rewrite.

## 5. What a product must never do

| # | Forbidden | Why |
|---|---|---|
| P-1 | Import any `workeasy360.*` module | Welds the product's release to the platform's |
| P-2 | Connect to the platform database, with any credential | Bypasses RLS, tenancy and every guarantee above it |
| P-3 | Read or write a platform ORM model | Same, one layer up |
| P-4 | Store its own copy of a user's credentials | One identity, one place |
| P-5 | Branch on plan name, bundle name or subscription state | It asks "entitled?" and gets yes/no ([ADR-005](adrs/ADR-005-product-entitlement-model.md)) |
| P-6 | Invent its own organization identifier | `organization_id` is the ecosystem identity (§9) |
| P-7 | Register a permission the platform has not been told about | A grantable permission with nothing enforcing it is worse than none |
| P-8 | Import another product | Unchanged from F-1; now enforced by repository separation as well |

## 6. What the platform must never do

| # | Forbidden | Why |
|---|---|---|
| E-1 | Contain a product's business models, services, routes or tasks | This cleanup's entire subject |
| E-2 | Branch on a specific product code (`if product == "crm"`) | Adding a product must not require editing the platform |
| E-3 | Hard-code a product's business permissions in the runtime registry | Products declare; the platform stores |
| E-4 | Hold a foreign key into a product's schema | `(product_code, entity, id)` soft reference instead |
| E-5 | Require a product to share its database | Products own their stores |
| E-6 | Block a platform operation because one product is unhealthy | §10 |

**E-2 is audited and currently clean.** After this cleanup no module in
`platform/`, `commercial/`, `integration/` or `interfaces/` branches on a
product code. `"crm"` survives only as (a) illustrative text in docstrings,
(b) catalog *data* in test fixtures — both legitimate: `Product(code="crm")`
is a row, not a branch.

## 7. Product registration (the model, not yet the implementation)

**Audited first, as required:** Phase 0–2 gives roughly half of what is
needed. `commercial.catalog.Product` already carries `code`, `name`,
`description`, `status`, `licensing_mode`, `sort_order` and lifecycle
timestamps, and `Feature` already carries the sellable capability keys. That
is the **commercial** half — what a product costs and what it sells.

What does not exist is the **ecosystem** half — what a product *is* and where
it *lives*:

| Field | Purpose | Exists today? |
|---|---|---|
| `code` | Stable identity (`crm`) | ✅ `catalog.Product.code` |
| `display_name`, `description` | Launcher presentation | ✅ `catalog.Product` |
| `icon` | Launcher presentation | ❌ |
| `launch_url` | Where the launcher sends the user | ❌ |
| `health_url` | Ecosystem health aggregation | ❌ |
| `version` | Deployed product version, for support | ❌ |
| `required_entitlement` | Product code to check; normally `code` | ⚠️ implied |
| `permission_namespace` | Prefix a product may register under | ⚠️ convention only |
| `declared_permissions` | Product's privilege surface | ❌ (registry is in-process) |
| `declared_features` | Feature keys it honours | ✅ `catalog.Feature` |
| `publishes` / `consumes` | Event contract | ⚠️ schemas registered in-process |
| `availability_status` | Hide / show / "coming soon" | ⚠️ `ProductStatus` is commercial, not deployment |

**Design intent for the next phase** — an `ecosystem/product_registry`
module holding a `RegisteredProduct` control-plane row (not tenant-scoped),
written through an authenticated registration API a product calls at deploy
time, keyed by `code`, and joined to `catalog.Product` by that same code —
never by foreign key, for exactly the reason
[01 §4](01-domain-boundaries.md) gives for `ProductCode` being a string.

Phase 0's `products/<p>/manifest.py` idea
([14 §4](14-repository-structure.md)) is the right *content* in the wrong
*place*: a manifest cannot be a Python module in this repository if the
product is not in this repository. It becomes a **payload the product
submits**, validated against a schema in `contracts/`.

**Built in Phase 3.** The design intent above is implemented as
`platform/ecosystem.ProductIntegration`, joined to `catalog.Product` by
`product_code` with no foreign key, exactly as specified. The manifest became
a payload the product submits, validated against
`contracts/values/manifest.py`. See
[01 Product Registry](../phase-3/01-product-registry.md) and
[02 Product Manifest](../phase-3/02-product-manifest.md).

## 8. Authentication / SSO boundary

Products are separate applications, so a user authenticating to CRM must be
authenticating to *Workeasy360*.

**Current state:** identity, sessions, MFA/AAL and membership resolution all
exist and are correct, but they are **in-process** — `TenantContext` is
resolved by middleware from a session, inside this application. There is no
token a separate application could present or verify.

**What a separate product needs, and does not yet have:**

1. **An authentication assertion** — a signed, short-lived, audience-scoped
   token naming the user, the organization and the AAL reached.
2. **Verification** — either local signature verification against a published
   key set, or an introspection endpoint. Never a shared database password
   (P-2), never a shared secret per product.
3. **Organization context in the token**, not in a header — the platform
   already refuses header-supplied organization (TB-1/TB-2); a separate
   product must inherit that property, not re-open the hole.
4. **Scopes**, so a token minted for CRM cannot act as the user against Books.
5. **Revocation** with a bounded staleness window, and a rule for what a
   product does when it cannot reach the platform (§10).

**Evolvability assessment: yes, safely.** The current model already separates
"who you are" (`identity.User`, global) from "where you are acting"
(`organizations.Membership`, per-org) from "how strongly you proved it"
(AAL). Those are exactly the three claims a token must carry, so this is an
*additional front door* onto an unchanged model — not a remodelling.

**Built in Phase 3**, and the evolvability assessment above held: it was an
additional front door onto an unchanged model. All five requirements are met
— a signed, short-lived, audience-scoped assertion; local verification against
a published key set; organization context in the token rather than in a
header; per-product audiences; and revocation with a bounded, documented
staleness window. See [03 SSO Architecture](../phase-3/03-sso-architecture.md)
and [ADR-018](adrs/ADR-018-openid-connect-for-product-sso.md).

## 9. Tenant boundary

**`organization_id` (UUID) is the ecosystem tenant identity.** It is minted
by `platform/organizations`, it is stable for the life of the organization,
and it is the only tenant identifier any product may use (P-6).

A product owns its own store and puts `organization_id` on its own rows. It
does **not** get a foreign key to `organizations.Organization` — it cannot,
across a database boundary — so referential integrity becomes the product's
responsibility, enforced by:

- treating an unknown `organization_id` as "not found", never as "allow";
- checking entitlement on every request rather than trusting a local cache
  indefinitely;
- consuming `organization.suspended` / `organization.purged` platform events
  to stop serving and to honour deletion.

The five-layer isolation model in [03](03-multi-tenancy.md) remains **the
platform's** guarantee for **platform** data. A product must implement
equivalent isolation in its own store. The platform should publish that
requirement as a certification checklist rather than assume it — a product
that leaks tenant data damages the ecosystem's trust, not only its own.

**What the platform must not do:** require a product's database to share
platform ORM models, a schema, a connection or a migration history.

## 10. Failure isolation across the ecosystem

One product being down must never degrade the platform or another product.

| If this fails | Then | Platform stays up? |
|---|---|---|
| A product's application | Its launcher tile shows *unavailable*; it cannot be opened | ✅ |
| A product's `health_url` | Tile marked *unknown*; **never** fails platform readiness | ✅ |
| A product's event consumer | Events accumulate in that subscription; outbox retries with backoff, then DLQ | ✅ |
| RabbitMQ | Publishing still commits (outbox is in the business transaction); relay resumes | ✅ — delayed, not lost |
| Redis | Entitlement checks fall through to the database; slower, still correct | ✅ |
| Notification provider | Deliveries queue; `notifications-sweep-pending` retries | ✅ |
| A future AI provider | Tier 3 — reported by `/healthz/deep`, never by `/healthz/ready` | ✅ |

**Rules this makes binding:**

1. Product health probes are **aggregated for display**, never wired into
   `/healthz/ready`. The existing readiness endpoint already refuses Tier
   3 dependencies ([09 §5](09-failure-resilience.md)) — product health is
   Tier 3 *by definition*, because the platform does not need a product to
   function.
2. Every platform→product call carries explicit connect and read timeouts
   (E1), and the launcher renders from cached registration data so it draws
   even with every product unreachable.
3. Login, organization management, billing, subscription changes and the app
   launcher must each be provable with **zero** products reachable.

**Must remain true with every product offline:** login · org switching ·
membership administration · catalog browsing · purchase · subscription
management · billing · the launcher itself.

## 11. Integration flow (governing architecture)

```
Independent product repository
        │  builds, deploys, versions on its own cadence
        ▼
Registers with Workeasy360           ── code, name, icon, launch_url,
        │                               health_url, permission namespace,
        │                               declared features, publishes/consumes
        ▼
Listed in the Product Catalog        ── commercial/catalog: Product + Features
        │
        ▼
Packaged into Plans / Bundles        ── commercial/catalog + pricing
        │
        ▼
Organization purchases               ── commercial/subscriptions
        │
        ▼
Entitlement activated                ── commercial/provisioning writes
        │                               platform/entitlements (sole writer)
        ▼
App appears in the launcher          ── ecosystem/app_directory (planned)
        │
        ▼
User launches the app                ── redirect to launch_url with an
        │                               authentication assertion (§8)
        ▼
Product verifies the assertion       ── signature/introspection; never a
        │                               shared database credential
        ▼
Product verifies entitlement         ── "may this org use me?" → yes/no,
        │                               never "what plan are they on?"
        ▼
Product serves its own domain data   ── its own store, its own migrations,
                                        its own isolation
```

## 12. Target user flow

```
Log in to Workeasy360  →  select organization  →  App Launcher
                                                      │
                    ┌─────────────────────────────────┼───────────────────┐
                    ▼                                 ▼                   ▼
             entitled + healthy              available, not entitled   unavailable
                  "Open"                      "Activate / Purchase"    hidden or
                                                                     "coming soon"
```

The **launcher is a platform capability**. The **app is not.** Building the
launcher UI was explicitly out of scope for this cleanup.

## 13. Consequences for this repository

1. `products/` is a **reserved, permanently empty namespace**. It exists so
   the import-linter independence contracts and
   `test_products_are_structural_placeholders_only` have modules to bind to.
   A product package that acquires any file other than `__init__.py` fails
   the build. This is now an unconditional assertion — Phase 3's
   `IMPLEMENTED_PRODUCTS` exemption is gone.
2. `contracts/events/product_events.py` does **not** exist. Product event
   schemas are owned by products. A genuinely shared cross-product contract
   would be a deliberate, versioned, separately-governed artefact, not a file
   that accumulates whatever the newest product needed.
3. `commercial/catalog` keeps `Product`, `Feature`, plans, price books and
   bundles. Selling a product is a platform concern; **implementing** it is
   not.
4. Illustrative `crm.*` strings in docstrings and test fixture data stay.
   They are examples and rows, not dependencies — and `Product(code="crm")`
   is precisely what the catalog is *for*.

## 14. Open gaps (next platform phase)

> **Updated 2026-09-22 (Phase 3).** G-1, G-2 and G-3 are **closed**, and so is
> G-8 from the Phase 3R carry-forward list. See
> [docs/phase-3/](../phase-3/phase-3-status.md),
> [ADR-018](adrs/ADR-018-openid-connect-for-product-sso.md),
> [ADR-019](adrs/ADR-019-product-service-authentication.md) and
> [ADR-020](adrs/ADR-020-signing-keys-and-the-control-plane-code.md).

| # | Gap | Blocks | Status |
|---|---|---|---|
| G-1 | No remote authentication assertion / SSO (§8) | Any separate product serving a user | **Closed** — OpenID Connect provider in `platform/ecosystem` |
| G-2 | No HTTP entitlement-verification endpoint | Any separate product checking access | **Closed** — `POST /api/v1/integrations/entitlements/verify` |
| G-3 | No product registry / manifest submission (§7) | Launcher, health aggregation, permission declaration | **Closed** for registry and manifest; permission declaration remains G-7 |
| G-4 | No app directory / launcher metadata API | Launcher | **Closed** — Phase 4, `GET /api/v1/orgs/<slug>/apps` ([docs/phase-4/02-app-directory.md](../phase-4/02-app-directory.md)) |
| G-5 | No outbound webhook delivery to product subscribers | Product event consumption | Open (P2). **Will reach the SSRF trigger** — the platform currently fetches nothing |
| G-6 | No HTTP surface for files or notifications | Products using those capabilities | Open (P2) |
| G-7 | Permission registry is in-process only | Products declaring their privilege surface | Open (P2) |
| G-8 | Product-side tenant isolation unverified by the platform | Ecosystem trust | **Closed** — [certification checklist](../phase-3/10-product-certification-checklist.md) §C, evidence-backed |

None of these are regressions — every one is work the in-repository design
never needed. They are listed so the next phase starts from a known set.
