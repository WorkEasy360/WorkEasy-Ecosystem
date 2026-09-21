# Phase 1 Implementation Summary

**Date:** 2026-09-21
**Scope:** Platform foundations. **No product features.**

---

## 1. What was built

The shared kernel every future product consumes: identity, organizations,
memberships, permissions, entitlements, notifications, files, audit, security,
observability — plus the mechanisms that keep products independent (outbox,
events) and the enforcement that keeps the boundaries real (import contracts,
system checks, fitness tests).

| Metric | Value |
|---|---|
| Python files | 185 |
| Source lines (excl. tests, migrations) | 11 967 |
| Test lines | 5 018 |
| Spike lines | 1 983 |
| Django models | 24 (15 tenant-scoped, 9 control-plane) |
| Migrations | 10 |
| Tests | **378 passing** |
| Import contracts | **9 kept, 0 broken** |
| System checks | W360.E001–E009, all passing |

## 2. Repository structure

```
Workeasy360/
├── ARCHITECTURE.md                the rules
├── docker-compose.yml             PostgreSQL · PgBouncer · Redis · RabbitMQ
├── .importlinter                  9 dependency contracts
├── Makefile · pyproject.toml · .env.example · .gitignore
│
├── backend/
│   ├── manage.py
│   ├── conftest.py                the role swap that makes RLS tests real
│   ├── config/
│   │   ├── env.py                 typed config with fail-early validation
│   │   ├── settings/{base,development,test,production,migration}.py
│   │   └── urls.py · asgi.py · wsgi.py · celery.py
│   │
│   ├── workeasy360/               ← ADR-013: nested to avoid shadowing stdlib `platform`
│   │   ├── contracts/             L0 — no Django, no internal imports
│   │   ├── platform/              L1 — P0a observability, P0b tenancy, P1…P3
│   │   ├── integration/           L2 — outbox, events
│   │   ├── commercial/            L3 — provisioning (sole entitlement writer)
│   │   ├── products/              L3 — nine empty placeholders
│   │   └── interfaces/            L4 — routing, error mapping
│   │
│   └── tests/
│       ├── isolation/  architecture/  integration/  security/  spikes/
│
├── infra/{postgres,pgbouncer}/    two roles · transaction-mode pooling
└── docs/{architecture,phase-1}/
```

## 3. Implementations 1–18

| # | Deliverable | Where | Notes |
|---|---|---|---|
| 1 | Project foundation | `config/`, `workeasy360/` | Five settings modules. `Env` reports **every** configuration problem at once, so a bad deploy is fixed in one pass. Production refuses to boot on 11 distinct misconfigurations. |
| 2 | Custom identity | `platform/identity` | `AbstractBaseUser`, email-as-identifier, normalised (NFKC + lowercase) with the original preserved. `+tag` and dots **not** stripped — over-normalisation merges distinct mailboxes. Argon2id. `AuthFactor` with WebAuthn fields from day one. **No membership fields**, asserted by a test. |
| 3 | Organization / tenancy | `platform/organizations`, `platform/tenancy` | `Organization`, `Membership`, `Invitation`. `TenantScopedModel` **raises** rather than returning empty. `unscoped` requires `unscoped_access(reason=…)`. |
| 4 | PostgreSQL RLS | `tenancy/migrations_helpers.py`, `tenancy/0001` | All 16 tenant tables: enabled, **FORCED**, `USING` + `WITH CHECK`. Composite FKs make cross-tenant references unrepresentable. `authz_role` gets the asymmetric D-001 policy. |
| 5 | R-10 suspension block | middleware + `TenantScopedModel` | **Two chokepoints**, plus all four bulk paths. One documented exemption (audit). |
| 6 | RBAC | `platform/authz` | Permission / Role / RolePermission / RoleAssignment. 25 code-declared permissions. Deny-by-default. Compiled sets, version-keyed cache. Admin **excludes** billing. |
| 7 | Entitlement read foundation | `platform/entitlements` | `check()` / `feature()` / `quota()` / `has_seat()`. Request-local → shared cache → snapshot → live fallback. Write path importable only by provisioning. |
| 8 | Audit | `platform/audit` | Append-only by **grants and a trigger**, not discipline. Hash-chained. Survives organization deletion. |
| 9 | Notifications | `platform/notifications` | Persist intent, queue, return. Never inline. Channel abstraction, bounded retries, failures recorded. |
| 10 | Files | `platform/files` | Private by default. Magic-byte verification against the declaration. Server-generated tenant-prefixed keys. HMAC-signed short-lived URLs. Quarantine until scanned. |
| 11 | Celery | `config/celery.py`, `tenancy/tasks.py` | Three queue groups, per-task routing. `TenantTask` carries context and **refuses to start** without it. Bounded jittered retries, no nesting. |
| 12 | Outbox | `integration/events` | Event written in the business transaction. `SKIP LOCKED` relay. Stale-claim recovery. `ProcessedEvent` dedupe. Dead letter with full envelope. |
| 13 | Observability | `platform/observability` | JSON logs, correlation spine, redaction, cardinality-guarded metrics, four health endpoints. |
| 14 | Security baseline | settings + `interfaces/api/errors.py` | Secure cookies, CSRF, CORS allowlist, HSTS, trusted hosts, safe error responses, admin/staff separation. |
| 15 | Database safety | `config/settings/base.py` | `CONN_MAX_AGE=0`, statement/lock/idle timeouts, cursors and prepared statements disabled, migration checks in CI. |
| 16 | Dependency enforcement | `.importlinter`, `tenancy/checks.py`, `tests/architecture/` | 9 contracts + 9 system checks + 21 fitness tests. The linter is itself tested. |
| 17 | Testing baseline | `conftest.py`, `tests/` | pytest + real PostgreSQL as the **non-owner role**, through PgBouncer. 378 tests, negative cases throughout. |
| 18 | CI gates | `.github/workflows/ci.yml` | Format, lint, typing, architecture, secret scan, SAST, dependency audit, full suite, spikes. |

## 4. Frozen Phase 0 decisions — all preserved

| # | Decision | How it is held |
|---|---|---|
| 1 | Entitlements under `platform/`, not `commercial/` | `platform/entitlements`; `products → commercial` is a linter-forbidden edge |
| 2 | `commercial/provisioning` is the sole entitlement writer | `services/write.py` is a forbidden import target for everything else; an AST test asserts it independently |
| 3 | Products consume read APIs only | `contracts/ports/entitlements.py` has no write method, and none will be added |
| 4 | `contracts/` is dependency-free | Contract + a bare-interpreter subprocess test asserting no Django in `sys.modules` |
| 5 | `platform/` P0–P3 layering | Layer contract, refined to P0a/P0b by ADR-015 — an added ordering constraint, not a relaxation |
| 6 | `integration/` holds mechanism | Outbox and events only; knows nothing about business domains |
| 7 | `interfaces/` owns composition | Routing and error mapping; no business logic |
| 8 | PartnerAccount uses capability grants | Not implemented (Phase 2); nothing built contradicts it |
| 9 | Modular monolith first | One image, one database, one migration set |
| 10 | No cross-product ORM access | AST scan + independence contract |
| 11 | Tenant isolation fails closed | Verified at every layer; see [rls-verification.md](rls-verification.md) |
| 12 | Three queue groups, task-level routing | `('critical','default','heavy')`; W360.E007 requires explicit routing |

## 5. Deviations from Phase 0 — three, each with an ADR

All three were forced by implementation, and each is narrower or stricter than
what it amends.

### [ADR-013](../architecture/adrs/ADR-013-package-namespace.md) — modules nested under `workeasy360/`

A top-level `platform/` package **shadows the Python standard library
`platform` module**, breaking `platform.system()` — which Django, psycopg and
pip all call. Verified empirically before deciding.

Directory names and every layer rule are unchanged; only the import prefix
differs.

### [ADR-014](../architecture/adrs/ADR-014-membership-fk-allowlist.md) — `Membership` added to the FK allowlist

Phase 0's own models require it: `RoleAssignment.membership` (04 §4.4) and
`SeatAssignment.membership` (06 §6) both cross a module boundary to a target
the three-entry allowlist does not include. Phase 0 mandated both the rule and
three violations of it.

Added **narrowly**: `platform/` and `commercial/` only. Products still use
`identity.User`, as the original allowlist intends. Enforced by W360.E003.

### [ADR-015](../architecture/adrs/ADR-015-platform-p0-ordering.md) — P0 split into P0a and P0b

`tenancy` must log; `observability` must stamp `organization_id`. As same-layer
siblings, F-5 forbade both directions, and neither events nor an L4
orchestrator can inject a field into a synchronous log call.

`observability` becomes P0a, strictly below `tenancy` (P0b), and imports
nothing internal but `contracts`. Higher layers **push** through registries:
log context via `contracts/ports/observability.py`, health probes via
`observability/health.py`. This *adds* an ordering constraint rather than
removing one.

## 6. Architecture notes discovered in implementation

Three behaviours worth recording, because each is easy to get wrong in the
same direction:

**`unscoped_access()` relaxes Layer 2 only.** RLS does not care about the ORM.
A cross-tenant writer must also enter `organization_db_scope(org_id)` —
per organization, never many tenants in one transaction. Found when
provisioning's writes were refused by `WITH CHECK`, which is defence-in-depth
working.

**`base_manager_name` must not be the scoped manager.** Django uses
`_base_manager` for INSERT, `refresh_from_db` and FK descriptor fetches.
Pointing it at the scoped manager makes all three raise without tenant
context. Left unset, so Django creates a plain manager for its own use — safe,
because RLS still filters those queries.

**`current_setting` returns `''`, not NULL, after a reset.** `''::uuid` raises
`22P02`, so the policy predicate uses `NULLIF(current_setting(…), '')::uuid`.
Without it, a reset connection errors on every query instead of returning zero
rows.

## 7. Commands

```bash
make setup      # virtualenv + dependencies
make up         # infrastructure
make migrate    # as the owner role
make test       # 378 tests
make ci         # everything CI runs
make spikes     # the three verification spikes
```
