# Workeasy360

> **One Ecosystem to Run Your Entire Business.**

**This repository is the ecosystem platform. It contains no business
products.**

Workeasy360 is the shared multi-tenant kernel that business products plug
into: one account, one organization, one identity, one permission system, one
subscription relationship, one app launcher. CRM, Books, Inventory, Projects,
Marketing, Support, HR, Payroll and Commerce are **separate applications in
their own repositories**, sold through this platform's catalog and integrating
through its contracts.

```
                        Workeasy360 (this repository)
         identity · organizations · authz · entitlements · catalog
         pricing · subscriptions · billing · events · notifications
         files · audit · observability · partners · app launcher
                                   │
        ┌──────────────┬───────────┼───────────┬──────────────┐
        ▼              ▼           ▼           ▼              ▼
      CRM           Books      Inventory   Projects        …
   (separate      (separate    (separate   (separate
    repo)          repo)        repo)       repo)
```

**Current status (Phase 12, 2026-09-30): production hardening done; go-live
readiness FAIL.** The platform side passes its checks, but four products,
DNS, email and payment providers are not ready. The platform checks include
browser journeys for all four products, backup/restore/PITR, outage tests, the
upgrade rehearsal and the security scans. Go-live blockers, owners and the path
to YES: [docs/phase-12/phase-12-status.md](docs/phase-12/phase-12-status.md).
Deployment: [docs/phase-12/04-deployment.md](docs/phase-12/04-deployment.md).

*Earlier status (Phase 5):* **a new customer can create their organization,
become its owner, find it through a scalable organization directory and
reach the launcher** ([docs/phase-5/](docs/phase-5/00-overview.md); evidence in
[phase-5-status.md](docs/phase-5/phase-5-status.md): 1,121 backend tests and
41 frontend tests, 0 failed, fresh database).

**Phase 4: a user can sign in once and launch entitled,
seated products across the ecosystem.** Phase 0 (architecture), Phase 1
(platform foundations), Phase 2 (commercial core) and Phase 3 (remote
product SSO, an OpenID Provider with an integration registry, key rotation
and an HTTP entitlement API) are built and tested. Phase 4 adds the
unified frontend shell (`frontend/`, Next.js) — login, organization chooser,
a data-driven app launcher, and seat/member administration — and closes the
seat-enforcement gap Phase 3's audit identified: a per-seat product with
every seat assigned now refuses an unseated member at the OIDC protocol
level, not only in the UI. **1,010 backend tests, 0 failed, 0 skipped**
(fresh database); **29 frontend tests, 0 failed** (clean install). Audited
adversarially — [docs/phase-4/14-opus-audit.md](docs/phase-4/14-opus-audit.md).

No product features live here, and none ever will — `products/` is a reserved,
permanently empty namespace with an unconditional build check.

**Building a product?** Start with the
[Product Integration Guide](docs/phase-3/09-product-integration-guide.md). You
need an issuer URL, a client id, a credential and a JWT library — nothing from
this codebase.

- [ARCHITECTURE.md](ARCHITECTURE.md) — the rules. **Read this before making a structural change.**
- [16 — Ecosystem / Product Boundary](docs/architecture/16-ecosystem-product-boundary.md) — **what belongs here and what does not**
- [ADR-017](docs/architecture/adrs/ADR-017-products-are-separate-applications.md) — why products are separate applications
- [ADR-018](docs/architecture/adrs/ADR-018-openid-connect-for-product-sso.md) — why OpenID Connect, and where it lives
- [ADR-021](docs/architecture/adrs/ADR-021-launcher-access-decision-and-seat-projection.md) — the launcher's access decision and platform layer P4
- [docs/phase-4/](docs/phase-4/00-overview.md) — the frontend, the launcher, seat enforcement, in full
- [docs/phase-3/](docs/phase-3/00-overview.md) — the remote integration contracts, in full
- [docs/architecture/](docs/architecture/) — the full design, 17 documents and 21 ADRs
- [docs/phase-3R/](docs/phase-3R/) — the scope audit and what it kept

---

## Getting started

Requires Docker, Python 3.10+ and Node 20+.

```bash
make setup      # virtualenv and dependencies
make up         # PostgreSQL, PgBouncer, Redis, RabbitMQ
make migrate    # schema, RLS policies and grants, as the owner role
make test       # the full platform suite

cd frontend
cp .env.local.example .env.local   # point BACKEND_ORIGIN at the backend above
npm install
npm run dev                         # the platform shell, at localhost:3000
```

`make help` lists everything else. `frontend/README.md` has the frontend's
own scripts.

## Why the local stack looks like it does

Three pieces of the development environment are not conveniences — they exist
so that local behaviour matches production in the places where a difference
would be invisible until it mattered.

**Two database roles.** `w360_migrator` owns the schema; `w360_app` is a
non-owner with `NOBYPASSRLS` and is what the application connects as. If the
application owned its tables it would bypass Row-Level Security unless
`FORCE` were set, and a single missed `FORCE` would silently disable tenant
isolation everywhere. The application asserts this at startup and refuses to
boot otherwise.

**PgBouncer in transaction mode.** The same pooling mode as production,
because the tenant variable's safety depends on transaction semantics
([R-01](docs/phase-1/rls-verification.md)). Running without it locally would
hide an entire class of failure.

**The test suite runs as the non-owner role.** Migrations run as the owner,
then the connection is rebound. A suite running as the table owner passes
every isolation test while production is wide open.

## Layout

```
backend/workeasy360/
├── contracts/     L0   interfaces, event schemas, value objects. No Django.
├── platform/      L1   tenancy · observability · identity · organizations
│                       authz · entitlements · audit · files · notifications
├── integration/   L2   transactional outbox, events, idempotency
├── commercial/    L3   catalog · pricing · subscriptions · billing
│                       provisioning (the sole entitlement writer)
├── products/      L3   RESERVED AND EMPTY — products are separate
│                       applications (ADR-017). A file here fails the build.
└── interfaces/    L4   routing and composition. No business logic.
```

**Dependencies point downward. Never sideways at L3. Never upward.**

Enforced, not encouraged: eleven import-linter contracts, nine Django system
checks and twenty-five fitness tests run on every PR. The linter is itself
tested, because a contract that never fails is indistinguishable from no
contract.

> The modules are nested under `workeasy360/` because a top-level `platform`
> package shadows the Python standard library module of that name, breaking
> `platform.system()` — which Django, psycopg and pip all call. Directory
> names and every layer rule are exactly as documented
> ([ADR-013](docs/architecture/adrs/ADR-013-package-namespace.md)).

## Building a product for Workeasy360?

It does not go in this repository. Start with
[16 — Ecosystem / Product Boundary](docs/architecture/16-ecosystem-product-boundary.md):
it defines what the platform owns, what you own, what you may depend on, and
what you must never do. Note §14 — several remote contracts a separate
product needs are specified but **not yet built**.

## Before you open a PR

```bash
make ci     # lint, typing, import contracts, system checks, full suite
```

Then the checklist in [ARCHITECTURE.md](ARCHITECTURE.md) §7. The last question
on it is the cheapest architectural test available:

> If this module were extracted to its own service in three years, what breaks?

"Nothing — it only uses contracts and events" means the boundary is right.

## The four things most likely to catch you out

1. **Product-domain code does not belong here.** Contacts, invoices, stock
   items, tasks, employees — if one product owns it, it is not platform.
   Five fitness tests enforce this; `products/` must stay empty.
2. **`unscoped_access()` relaxes the ORM only.** PostgreSQL RLS still applies.
   A cross-tenant write also needs `organization_db_scope(org_id)`, entered
   per organization.
3. **Tenant queries need a transaction.** The RLS variable is transaction-local
   by design; outside one, every tenant query returns zero rows.
4. **Entitlement before permission.** "Your plan does not include Books" is a
   sales conversation; "you lack permission" is an admin one. Merging them
   produces a support ticket engineering cannot resolve.
