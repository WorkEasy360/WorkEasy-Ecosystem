# Workeasy360

> **One Ecosystem to Run Your Entire Business.**

One multi-tenant platform hosting multiple **independently commercializable**
product domains (CRM, Books, Inventory, Projects, …) on one shared kernel,
sold through direct and partner channels that are commercial constructs —
never forks or separate deployments.

**Status: Phase 1 complete.** The platform foundations exist and are tested.
No product features have been built.

- [ARCHITECTURE.md](ARCHITECTURE.md) — the rules. **Read this before making a structural change.**
- [docs/architecture/](docs/architecture/) — the full design, 16 documents and 15 ADRs
- [docs/phase-1/](docs/phase-1/) — what was built, what was measured, what is carried

---

## Getting started

Requires Docker and Python 3.10+.

```bash
make setup      # virtualenv and dependencies
make up         # PostgreSQL, PgBouncer, Redis, RabbitMQ
make migrate    # schema, RLS policies and grants, as the owner role
make test       # 378 tests
```

`make help` lists everything else.

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
├── integration/   L2   transactional outbox, events
├── commercial/    L3   provisioning (the sole entitlement writer)
├── products/      L3   placeholders only in Phase 1
└── interfaces/    L4   routing and composition. No business logic.
```

**Dependencies point downward. Never sideways at L3. Never upward.**

Enforced, not encouraged: nine import-linter contracts, nine Django system
checks and twenty-one fitness tests run on every PR. The linter is itself
tested, because a contract that never fails is indistinguishable from no
contract.

> The modules are nested under `workeasy360/` because a top-level `platform`
> package shadows the Python standard library module of that name, breaking
> `platform.system()` — which Django, psycopg and pip all call. Directory
> names and every layer rule are exactly as documented
> ([ADR-013](docs/architecture/adrs/ADR-013-package-namespace.md)).

## Before you open a PR

```bash
make ci     # lint, typing, import contracts, system checks, full suite
```

Then the checklist in [ARCHITECTURE.md](ARCHITECTURE.md) §7. The last question
on it is the cheapest architectural test available:

> If this module were extracted to its own service in three years, what breaks?

"Nothing — it only uses contracts and events" means the boundary is right.

## The three things most likely to catch you out

1. **`unscoped_access()` relaxes the ORM only.** PostgreSQL RLS still applies.
   A cross-tenant write also needs `organization_db_scope(org_id)`, entered
   per organization.
2. **Tenant queries need a transaction.** The RLS variable is transaction-local
   by design; outside one, every tenant query returns zero rows.
3. **Entitlement before permission.** "Your plan does not include Books" is a
   sales conversation; "you lack permission" is an admin one. Merging them
   produces a support ticket engineering cannot resolve.
