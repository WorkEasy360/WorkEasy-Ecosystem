# ADR-009 — Tenant Data Isolation

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture / Security

---

## Context

Every tenant's data shares a database with every other tenant's. A single missing `WHERE organization_id = ...` is a data breach — the one class of defect from which the business does not recover.

Application-level filtering alone is insufficient not because developers are careless, but because the failure is silent: a missing filter produces *more* results, not an error, and code review does not reliably catch a missing clause in a large codebase over years.

## Decision

**Five layers of defence, each assuming the one above it has failed. PostgreSQL Row-Level Security is the layer that survives application bugs.**

1. **Request boundary** — `TenantContext` resolved from a verified `Membership`, never from a header or request body. Fails closed.
2. **ORM** — `TenantScopedManager` **raises** `MissingTenantContext` rather than returning an empty queryset.
3. **PostgreSQL RLS** — a policy on every tenant table, with `FORCE ROW LEVEL SECURITY`, both `USING` and `WITH CHECK`, bound to `current_setting('app.organization_id', true)`.
4. **Schema constraints** — composite foreign keys `(child.parent_id, child.organization_id) → parent(id, organization_id)` make cross-tenant references **unrepresentable**.
5. **Detection** — canary tenants, CI fitness tests, audited `unscoped` usage, query-level assertions in non-production, cross-tenant probing alerts.

Supporting decisions:

- The application connects as a **non-owner, non-superuser role** with `NOBYPASSRLS`; startup asserts this and refuses to run otherwise.
- The tenant variable is set with **`set_config(..., is_local => true)` inside an explicit transaction** — never a session-level `SET` — because PgBouncer transaction pooling would otherwise leak it to the next tenant.
- Cross-module foreign keys are limited to a **frozen allowlist**: `Organization`, `User`, `File`.
- Isolation extends beyond PostgreSQL: Redis key prefixes, S3 key prefixes, mandatory search filters, task headers, log fields.

## Alternatives considered

### A. Application filtering only

- **Pro:** simple; no database configuration; no RLS performance question.
- **Con:** one missed filter is a breach, and the failure is silent. Raw SQL, admin tooling, data scripts and bulk operations all bypass any ORM-level convention.
- **Rejected:** unacceptable risk concentration on perfect human discipline.

### B. Schema per tenant

- **Pro:** strong isolation; familiar.
- **Con:** migrations run thousands of times with partial-failure states; PostgreSQL catalog degrades past a few thousand schemas; `search_path` switching is its own bug class; cross-tenant reporting needs ETL; connection pooling becomes complicated.
- **Rejected:** the migration blast radius is disqualifying for a platform deploying schema changes constantly across a dozen products.

### C. Database per tenant

- **Pro:** strongest isolation; simple mental model; per-tenant backup and restore.
- **Con:** operationally impossible at target scale — thousands of connections, pools, backups and migration runs.
- **Rejected**, but retained as an option for a small number of enterprise customers, which the model already supports via `shard_key`.

### D. Application scoping + RLS + composite FKs (selected)

## Why selected

1. **Defence in depth against the realistic failure.** The realistic failure is an application bug, and RLS is enforced below the application — including for raw SQL, admin tools and ad-hoc scripts.
2. **Fail-closed by construction.** An unset session variable makes the policy predicate `NULL`, which is not `TRUE`, so zero rows match. The safe outcome is the default outcome.
3. **Composite FKs eliminate a class RLS does not cover.** RLS stops you *reading* another tenant's row; it does not stop you *pointing at* one. A composite FK makes that write fail at the database.
4. **Raising rather than returning empty.** Silent emptiness looks like "no data" and ships; an exception is loud and is caught in development.
5. **One migration path.** Shared schema keeps deployment tractable.
6. **Cheap cross-tenant analytics**, which per-tenant models make expensive.

## Consequences

**Positive**

- A missing application filter is caught by the database rather than becoming a breach.
- Cross-tenant references cannot exist in the schema.
- One migration run, one backup strategy.
- Isolation is testable in CI with canary tenants.

**Negative, accepted**

- **Every tenant-scoped request runs inside a transaction**, because `SET LOCAL` requires one. `ATOMIC_REQUESTS` is not used; the tenancy middleware opens the transaction explicitly.
- Transaction-mode pooling forbids server-side cursors, prepared statements, `LISTEN/NOTIFY` and session-level `SET`. All are avoidable; all are documented in [10](../10-performance-concurrency.md).
- RLS adds a predicate to every query. Negligible **provided** every index on a tenant table leads with `organization_id` — enforced by a fitness test.
- Composite FKs are not generated by Django and require `RunSQL` via a migration helper.
- Two database roles (owner for migrations, application for runtime) add deployment complexity.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| App connects as the table owner, silently disabling RLS | **Critical** | `FORCE ROW LEVEL SECURITY` + startup assertion on `rolsuper`/`rolbypassrls` |
| Session variable leaks across PgBouncer-pooled transactions | **Critical** | `set_config(..., true)` only; integration test through real PgBouncer; documented session-pooling fallback (Risk R-01) |
| A new model omits `TenantScopedModel` | High | System check `W360.E001`; migration-state check `W360.E002` |
| `unscoped` manager misused | High | Module allowlist, `unscoped_access(reason=...)` context manager, audit + metric |
| Index missing `organization_id` prefix | Medium | Fitness test; slow-query monitoring |
| Isolation gaps outside PostgreSQL (Redis, S3, search) | High | Prefixing wrappers, mandatory filters, canary probes across all stores |
| Cross-tenant task execution | High | Tenant headers mandatory; `requires_tenant=False` needs CODEOWNERS review |

## Future extraction path

If a customer requires physical isolation, the path exists without a data-model change: `Organization.shard_key` and `residency_region` are present, tenant routing is centralised in `platform/tenancy`, and every row already carries `organization_id`. The change is a database router, not a migration of the model.

RLS remains valuable even in a per-tenant database: it prevents a routing bug from exposing the wrong tenant.

## Related

- [03-multi-tenancy.md](../03-multi-tenancy.md), [12-security.md](../12-security.md), [10-performance-concurrency.md](../10-performance-concurrency.md)
- [ADR-002](ADR-002-multi-tenant-organization-model.md), [15-risk-register.md](../15-risk-register.md)
