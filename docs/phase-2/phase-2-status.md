# Phase 2 — Exit Gate Assessment

**Date:** 2026-09-21
**Assessed by:** Implementation
**Repository state at start:** Phase 1 complete. 379 tests passing. No
commercial domain.

---

# PHASE 2 STATUS: **PASS**

---

## 1. Exit criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Product catalog works | ✅ | `Product` + lifecycle; 40 catalog tests |
| 2 | Features work | ✅ | Three kinds, key/product binding validated in one place |
| 3 | Plans work | ✅ | `Plan` → `PlanVersion` → `PlanFeature`; features referenced, never embedded |
| 4 | Pricing preserves historical correctness | ✅ | Price rise leaves the old row, the old quote and the old item unchanged |
| 5 | Bundles work | ✅ | Plan versions, not products; nested bundles impossible by schema |
| 6 | Bundle expansion is atomic/idempotent | ✅ | Interrupted expansion leaves nothing; 8-way race produces 3 lines |
| 7 | Subscription lifecycle is controlled | ✅ | Explicit transition table; no arbitrary status assignment anywhere |
| 8 | Items preserve commercial history | ✅ | Immutable version + immutable price + denormalised snapshot |
| 9 | Trials work | ✅ | A status, not a flag; one per product per org from durable history |
| 10 | License/seat foundation works | ✅ | No duplicate model; the service layer `SeatGrant`/`SeatAssignment` needed |
| 11 | Concurrent seat assignment cannot overallocate | ✅ | **9 taken, 8 threads → 1 assigned, 10 total** |
| 12 | Upgrade/downgrade rules are explicit | ✅ | Direction classified; timing by direction; seat conflict policy documented |
| 13 | Cancellation semantics are explicit | ✅ | Period-end default, immediate available, auto-renew stopped |
| 14 | Renewal foundation works | ✅ | Period roll catches up in one pass; month-ends do not drift; **no payment simulated** |
| 15 | Provisioning is the sole entitlement writer | ✅ | Import contract + AST sweep over the whole tree |
| 16 | Reconciliation is idempotent | ✅ | Same rows twice; rebuilds from nothing after total deletion |
| 17 | Drift invariants are tested | ✅ | Detected, repaired, signalled — and silent when there is none |
| 18 | Commands are retry/idempotency safe | ✅ | Ledger + unique constraint; a failed command does not burn its key |
| 19 | Money never uses floats | ✅ | No float or decimal columns; `int` enforced; `Money` throughout |
| 20 | Unsupported FX fails closed | ✅ | No conversion anywhere; mismatches raise |
| 21 | Tenant commercial data passes isolation | ✅ | Five new tenant models, covered automatically by the parametrised suite |
| 22 | Object authorization denies by default | ✅ | Two barriers on control plane; scoped manager closes IDOR |
| 23 | Outbox integration is transaction-safe | ✅ | Published in the business transaction; rollback discards |
| 24 | No uncontrolled N+1 | ✅ | 13 pinned counts, including growth assertions |
| 25 | Concurrency tests pass | ✅ | 17 tests, real threads, 11 contention points |
| 26 | Failure-path tests pass | ✅ | 14 tests, injected failures |
| 27 | Operations are observable | ✅ | 6 metrics, structured events, correlation preserved |
| 28 | Sensitive changes are audited | ✅ | Asserted over the source by AST |
| 29 | Cleanup performed safely | ✅ | 1 table, 1 service, 6 methods, 2 duplicates removed — all verified |
| 30 | No unresolved P0 | ✅ | 2 found, 2 fixed, both with regression tests |
| 31 | Full Phase 1 + Phase 2 suite passes | ✅ | **647 passed, 0 failed, 0 skipped** after the Opus audit (627 at implementation sign-off) |

## 2. Results

> **Superseded by the final adversarial audit.** See
> [opus-audit.md](opus-audit.md): two further P0s and four P1s were found and
> fixed after this assessment, and the suite is now 647 tests. The numbers
> below are the implementation sign-off figures, kept as the record of what
> was believed at that point.

```
pytest            627 passed, 0 failed, 0 skipped        139 s
ruff check        All checks passed
ruff format       211 files already formatted
mypy              Success: no issues in 17 source files
bandit -ll        0 high, 0 medium, 0 #nosec
pip-audit         No known vulnerabilities found
lint-imports      11 contracts kept, 0 broken
manage.py check   no issues (0 silenced)
check --deploy    6 issues — byte-identical to the Phase 1 baseline
makemigrations    No changes detected
```

The `--deploy` result was verified against a clean `git worktree` at the
Phase 1 commit rather than assumed: same six warnings, all from the
development settings profile, none introduced by Phase 2.

## 3. Findings

### P0 — 2 found, 2 fixed

| # | Finding | Fix |
|---|---|---|
| 1 | **Cross-tenant reads returned silently empty.** `unscoped_access()` relaxes the ORM manager but not RLS; with no tenant variable a query matches zero rows. `reconcile()` from a worker read no subscriptions, computed an empty desired state, and **expired every entitlement for every organization, hourly** | Every cross-tenant selector now enters `organization_db_scope` too. The identical defect in Phase 1's `bump_permission_version` fixed the same way |
| 2 | **Two concurrent first purchases created two subscriptions.** A row lock cannot protect a row that does not exist yet | Partial unique constraint `(organization) WHERE status <> 'expired'` plus an `IntegrityError` re-read |

Both were invisible to the non-transactional suite, because `django_db`
shares one outer transaction in which an earlier `set_config(..., true)`
survives. Both were found by writing tests with `transaction=True`.

### P1 — 5 found, 5 fixed

| # | Finding | Fix |
|---|---|---|
| 1 | `commercial.subscription.manage` was `dangerous`, demanding AAL 2, which no session provides — **no customer could buy anything** | Removed, with the reasoning recorded in `permissions.py` |
| 2 | A view re-parsed a `UUID` Django had already converted → **HTTP 500 on the IDOR path** | Views take `UUID` directly |
| 3 | Reconciliation was an N+1 across three tables: **51 statements, +13 per line** | Bulk upserts and set-based staleness; under 40, growth within bound |
| 4 | Celery Beat unconfigured, so no time-based transition would ever fire | `CELERY_BEAT_SCHEDULE` added — see Task 37 below |
| 5 | **Rescheduling a pending plan change raised `IntegrityError`.** The outgoing line starts at the period end; an upgrade defaulted to "immediate" and tried to close it before it opened. The database check constraint refused the window — defence in depth working, but the caller got a 500-shaped error instead of the thing they asked for | A change now takes effect no earlier than the line it changes. The superseded line gets a zero-width window, which `is_effective_at` never matches. Found by the final adversarial pass, not by a user |

### P2 — 6, documented and carried

| # | Finding | Trigger |
|---|---|---|
| 1 | No rate limiting on the commercial API | Before first production customer |
| 2 | No step-up (AAL 2) on any commercial operation | Session/MFA policy wiring |
| 3 | The over-limit seat deadline is not swept | Seat-management UI |
| 4 | `W360.E005` (feature key ↔ product manifest) unimplemented | The first product module |
| 5 | Both sweeps are O(organizations) | 5 000 organizations |
| 6 | No catalog caching | A production slow log, not a benchmark |

### Fixed during the final adversarial pass

Two defects found by asking "what happens if the customer does this twice?"
rather than by a failing test:

| Finding | Fix |
|---|---|
| Activating an already-active subscription with a fresh idempotency key published a spurious `activated` event and bumped the optimistic token, invalidating every concurrent caller's read for a change that did not happen | Short-circuit on the self-transition. Covered by `test_activating_an_already_active_subscription_is_a_no_op`, which uses a *fresh* key so the ledger cannot mask the question |
| Rescheduling a pending plan change — P1-5 above | Clamped, with two tests: one for the window, one asserting the computation still grants the product exactly once while a change is pending |

## 4. Task 37 — Phase 1 carried risks, reassessed

The brief asks specifically whether Phase 2 introduces a trigger for each.
Answered with evidence, not with a list.

| Item | Trigger reached | Evidence | Action |
|---|---|---|---|
| **Celery Beat** | **YES** | Two things now have no other trigger: the commercial clock (trials, cancellations and period rolls happen because time passed — no request, no actor) and drift repair (the recovery mechanism the whole provisioning design rests on). Without a scheduler a cancelled customer keeps access indefinitely | **Implemented.** Five entries, idempotent tasks, `expires` below each interval |
| **Circuit breakers** | **NO** | Phase 2 adds no external dependency. `commercial/` imports no HTTP client, asserted by an AST test. The only external call in the system is still SMTP | Carried. Trigger unchanged: the first payment provider or connector |
| **Backpressure / load shedding** | **NO** | No public traffic, no queue depth to shed. The commercial commands are all synchronous and bounded; the two async tasks are scheduled, not demand-driven | Carried. Trigger: before first production customer |
| **Rate limiting** | **NO — but closer** | The commercial API is the first surface where a replayed request costs money. The idempotency ledger makes replay *cheap to refuse* rather than expensive to serve, which is the specific risk. Nothing is publicly exposed | Carried. Trigger: public exposure |
| **Entitlement-version query** (the Phase 1 P1: the first check costs one query) | **NO** | Measured again with the new sources: 75 repeat checks still cost **0** queries, and the per-request budget is unchanged. The Phase 1 trigger — p95 > 4 ms or hit ratio < 0.95 — needs production metrics that do not exist | Carried, deliberately |

Two other Phase 1 carried items were **fixed incidentally**, because Phase 2
exercised them: `bump_permission_version` (the P0-1 class of defect) and the
vacuous text-based G6 check.

The two Phase 1 P1s with owners — breach-corpus passwords and malware
scanning — are untouched. Neither has a Phase 2 trigger.

## 5. New models and tables

**Twelve new tables.** Five tenant-scoped, seven control-plane.

| Table | Plane | Purpose |
|---|---|---|
| `catalog_product` | control | Sellable SKU |
| `catalog_feature` | control | Sellable capability |
| `catalog_plan` | control | Plan identity |
| `catalog_plan_version` | control | Immutable commercial terms |
| `catalog_plan_feature` | control | What a version grants |
| `catalog_bundle` | control | Composition identity |
| `catalog_bundle_version` | control | Immutable composition |
| `catalog_bundle_item` | control | One granted plan version |
| `pricing_price_book` | control | A segment |
| `pricing_plan_price` | control | An immutable list price |
| `pricing_price_tier` | control | A band |
| `pricing_contract_price` | **tenant** | A negotiated rate |
| `billing_account` | **tenant** | Billing identity; fixes currency |
| `subscriptions_subscription` | **tenant** | The commercial relationship |
| `subscriptions_item` | **tenant** | One commercialised line |
| `idempotency_record` | **tenant** | The command ledger |

One field added to an existing model: `SeatAssignment.over_limit_since`.

## 6. Migrations

Seven, all forward-only and all reversible:

| Migration | Contents |
|---|---|
| `catalog.0001_initial` | Eight control-plane tables |
| `pricing.0001_initial` | Three control-plane, one tenant |
| `billing.0001_initial` | `BillingAccount` |
| `subscriptions.0001_initial` | Two tenant tables, 8 check constraints |
| `subscriptions.0002_…` | One open subscription per organization |
| `idempotency.0001_initial` | The command ledger |
| `entitlements.0002_…` | `over_limit_since` + its partial index |
| `tenancy.0003_commercial_isolation` | RLS ×5, grants ×11, composite FKs ×2, immutability triggers ×3, child-freeze triggers ×3 |

`makemigrations --check` is clean.

## 7. Architecture deviations

Two, both narrower than what they amend, both with an ADR and a debt-register
row.

| ADR | Change | Why |
|---|---|---|
| [016](../architecture/adrs/ADR-016-intra-commercial-foreign-keys.md) | `commercial/*` may hold FKs to `commercial/*` | Phase 0's own model definitions specify them, and `PROTECT` is the database-level mechanism behind "historical pricing cannot mutate". ADR-006's extraction path moves catalog and pricing together, so the extractability concern F-6 protects does not arise |
| D-006 | Three modules added to the `unscoped` allowlist | Clock-driven transitions have no actor and must work for suspended organizations. RLS is never relaxed; each enters `organization_db_scope` for one organization |

**No frozen Phase 0 or Phase 1 decision was weakened.** The sixteen frozen
items in the brief were each checked:

| # | Frozen decision | Status |
|---|---|---|
| 1 | Modular monolith | Held |
| 2 | `contracts/` dependency-free | Held — new enums and errors import nothing |
| 3 | `products → commercial` forbidden | Held, and now asserted by AST as well |
| 4 | Entitlements live in `platform/` | Held |
| 5 | `commercial/provisioning` sole entitlement writer | Held, enforced three ways |
| 6 | Entitlement reads deny by default | Held |
| 7 | Tenant isolation fail-closed | Held — and P0-1 was a case where it was *too quiet*, now fixed |
| 8 | RLS as defence-in-depth | Held; five new tables, all FORCEd |
| 9 | `prepare_threshold=None` | Untouched |
| 10 | `CONN_MAX_AGE=0` | Untouched |
| 11 | AmazonMQ/RabbitMQ broker | Untouched |
| 12 | Three queue groups | Held — new tasks use `critical`, `default`, `heavy` |
| 13 | `AuditEvent` the only write-block exemption | Held; no second exemption added |
| 14 | Entitlement latency budget is per-request | Held and re-measured |
| 15 | Composable partner capability grants | Untouched; partner selection deliberately absent |
| 16 | Composite tenant FKs registry-verified | Held — the registry-derived test covered the new ones automatically |

## 8. Decisions frozen for Phase 3

Changing any of these requires a new ADR.

### Catalog and pricing
1. Identity is a **code**; nothing keys off a name.
2. A published `PlanVersion`, `BundleVersion` or `PlanPrice` is immutable,
   enforced by a database trigger that **raises**. `effective_to` / `valid_to`
   are the only mutable columns, because closing is how supersession is
   recorded.
3. Children of a published parent are frozen too.
4. Price books are the only segmentation mechanism. A new market is rows.
5. `PlanPrice` has **no currency column**; the book is the single source.
6. `resolve_price` is the only price computation. Order: contract → *(partner,
   absent)* → regional → default → **raise**. No fallback, ever.
7. Backdating a price supersession is refused.
8. Usage pricing cannot be published until metering exists.

### Subscriptions
9. **One subscription per organization**, enforced by a partial unique
   constraint.
10. **At most one open line per product per subscription**, enforced by a
    partial unique constraint.
11. Items are effective-dated and never deleted. That one mechanism covers
    plan changes, period-end downgrades and scheduled cancellation.
12. An item carries a full commercial snapshot in addition to its pinned
    references.
13. Bundle purchase produces one **priced** bundle line and N **derived**
    plan lines. Derived lines carry no money; the bundle is billed once.
14. Upgrades are immediate, downgrades and lateral moves take effect at the
    period end, unless the caller overrides.
15. An immediate downgrade that would strand assigned seats is **refused**;
    a scheduled one is permitted and flags the excess.
16. Cancellation defaults to period end and sets `renewal_policy = none`.
17. The seven subscription statuses map one-to-one onto entitlement statuses,
    in exactly one function.

### Provisioning
18. `compute_entitlements` is pure and reads only durable state, through
    selectors.
19. `apply_entitlements` is idempotent and diff-based, with the whole
    organization in one transaction.
20. `source_ref` is per **item**, not per subscription.
21. A stale `SeatGrant` is **zeroed, never deleted** — deleting would cascade
    to assignments and silently revoke access.
22. Over-limit assignments are **flagged, never revoked**, most-recent-first,
    and the flag clears itself.
23. Drift is measured against the snapshot, repaired, and signalled.
24. Two triggers, one implementation: the immediate hook is a latency
    optimisation; the scheduled pass is the correctness mechanism.

### Cross-cutting
25. Every commercial command takes a **caller-supplied** idempotency key,
    claimed inside the business transaction.
26. Cross-tenant selectors enter `organization_db_scope` themselves. A
    caller is never trusted to have done it.
27. Cross-tenant enumeration reads the **control-plane** `Organization`
    table, never a tenant table.
28. `commercial/subscriptions/system.py` is the only system write pathway,
    and audits every operation with `ActorKind.SYSTEM` and a reason.
29. Control-plane writes require **both** the permission and
    `User.is_staff`.
30. Commercial internal layering: `catalog < pricing < billing <
    subscriptions < provisioning`.
31. Celery Beat is a required component. Every scheduled task is idempotent
    and carries an `expires` below its interval.

## 9. What Phase 2 deliberately did not do

- **No CRM, Books, Inventory or any product.** The nine product packages
  still contain `__init__.py` and nothing else, asserted by a test.
- **No reseller, white-label or affiliate anything.** `commercial/partners`
  and `commercial/commissions` remain empty. Partner price books can be
  *created* and are deliberately **not selectable**.
- **No payment provider, and nothing that pretends to be one.** No
  `BillingEvent`, no `BillingAttempt`, no `PaymentProviderCustomer` — all
  three considered and rejected as guesses, with one trigger recorded for all
  of them. No method anywhere records money received.
- **No customer-facing billing UI.** The API exists so that Phase 7's Super
  Admin has services to call rather than business logic to embed.
- **No invoicing, proration or tax calculation.** `tax_behaviour` is
  recorded; no rate is stored anywhere.
- **No speculative infrastructure.** No caching added, no circuit breakers,
  no rate limiting — each with a written trigger it has not reached.

## 10. Recommended Phase 3 sequence

Not part of Phase 2 scope; the ordering follows from the dependency graph and
from what is now load-bearing.

1. **Session/MFA policy wiring.** It blocks step-up on the two `dangerous`
   control-plane permissions, which are currently unusable over HTTP.
2. **The first product, end to end.** It is the proof that the boundaries
   hold, and the only thing that makes `W360.E005` implementable.
3. **Payment provider integration**, which closes three carried items at once
   (billing events, circuit breakers, the provider customer reference).
4. **The second product** — the real test, because it is the first time the
   event path carries weight.

Phase 0 §7 is right that the second product is where the architecture is
actually validated. Phase 2 has made the commercial side ready for it: a
product needs no commercial change to be sold, because selling it is a row in
`catalog_product` and a row in `pricing_plan_price`.

## 11. Final adversarial audit

This assessment was itself audited before the freeze. See
[opus-audit.md](opus-audit.md).

**Result: PASS.** Two P0s (a nested tenant scope leaking its organization to
the caller; `TenantTask` establishing application scoping without database
scoping) and four P1s, all fixed with regression tests. Four decisions added
to the frozen list. No architecture deviation, no new debt-register entry.

The most important correction to this document: §3's P0 table records the
cross-tenant-read defect as *fixed*. Three of its instances were. The
**shape** was not, and two more instances existed — one of them live in the
notification path. Phase 3 should read the audit's §5 rather than this §3.

---

**Phase 3 has not begun.**
