# Phase 2 — Final Adversarial Audit

**Date:** 2026-09-22
**Scope:** Whether Phase 2 is genuinely safe to freeze for Phase 3.
**Method:** Read the implementation, not the documentation; form a hypothesis
about a silent failure; write a transactional test that would expose it; run
it against real PostgreSQL with RLS enforced as the non-owner role.

---

# PHASE 2 OPUS AUDIT: **PASS**

Two P0s and four P1s found and fixed, all with regression tests. Baseline 627
tests; 647 now pass. No unresolved P0. No unresolved P1 that must precede
Phase 3.

---

## 1. Why this audit looked where it did

Phase 2's own P0 was `unscoped_access()` relaxing the ORM manager while
leaving RLS untouched, so a cross-tenant read returned **zero rows** instead
of failing, and an empty desired state expired every entitlement.

That defect was fixed at the three call sites where it was found. This audit
asked the obvious follow-up: **is the shape gone, or only those instances?**

It was only those instances. The same shape existed in two other places, one
of them live, and the mechanism that produced it — two isolation layers that
can disagree, with disagreement manifesting as an empty result rather than an
error — had no general guard at all.

A third finding is the inverse failure and is worse: a scope that was entered
correctly **stayed** entered after it should have closed, silently widening
what the caller could read.

## 2. P0 findings

### P0-1 · A nested tenant scope leaked its organization to the caller

**What.** `set_config(..., true)` is reverted when the **transaction** ends,
not when a block ends. A `SAVEPOINT` that is *released* keeps the value for
the remainder of the enclosing transaction; only a savepoint *rollback*
reverts it. Verified directly:

```sql
BEGIN;
SELECT set_config('app.organization_id','AAA',true);
SAVEPOINT sp1;
SELECT set_config('app.organization_id','BBB',true);
RELEASE SAVEPOINT sp1;
SELECT current_setting('app.organization_id', true);   --> BBB
```

Django's `transaction.atomic()` is a savepoint whenever one is already open,
so **every nested scope was one of these**. `organization_db_scope`,
`tenant_db_scope` and the bare `set_tenant_variable()` in `apply_entitlements`
all set the variable and never put it back.

**How it survived a spike written specifically about this.** The R-01
verification spike has a case named *"savepoint rollback restores the outer
tenant"*, and it passes. Savepoint **rollback** does restore it. Savepoint
**release** does not, and nothing tested release — which is the path every
successful nested block takes. The spike checked the safe half of the
semantics and the name made it read like the whole.

**Impact.** After an inner scope for organization B exited, every subsequent
statement in the caller's transaction ran under B's RLS policy while the ORM
filter still named A. The two disagreed and the database won. Proven, not
inferred: a query the code believed was scoped to A returned B's rows.

```
PROBE-2 visible={ea37f712-…}  expected={d025d98a-…}
```

**Reachability in Phase 2.** Latent, not live — and the reason is worth
recording, because it is the thing that must not soften. Phase 2 has no code
path that acts on a *foreign* organization from inside a request, and the
persistence chokepoint refuses one if it is attempted:
`reconcile(other_org)` under an active context for `this_org` raises
`TenantContextMismatch` at the snapshot write. Pinned by
`test_provisioning_a_foreign_organization_is_refused_not_misapplied`.

The **read** half was reachable, because `notify_commercial_change` swallows
every listener exception: a reconcile for a foreign organization would leak
the scope, fail at the write, have its failure logged and discarded, and leave
the request reading the wrong tenant. Phase 3's Super Admin, partner surfaces
and payment webhooks are all exactly that shape.

**Fix.** The scope helpers now capture what they found and put it back.
Capture is folded into the same statement as the write, from a `MATERIALIZED`
CTE — materialising it is what makes the ordering defined, since PostgreSQL
may inline an ordinary CTE and evaluate `current_setting` after the
`set_config` beside it. Restore is skipped on the two conditions where it is
provably a no-op: the scope opened the outermost transaction (its COMMIT
discards the setting anyway), or the value did not change (the common case —
a request reconciling its own organization).

On the error path the restore is deliberately **not** issued: the savepoint is
about to roll back, which reverts the setting by itself, and a statement on a
connection whose last one failed would raise a second error on top of the
real one.

**What it also exposed.** Two existing tests only passed because of the leak:

- `test_revocation_takes_effect_after_version_bump` deleted a role assignment
  through `unscoped_access` with **no** database scope. That DELETE matched
  zero rows. The test asserted that a revocation nobody performed had no
  effect — and passed, because the leaked variable from the preceding
  `in_tenant` block made the delete work. Now asserts `deleted == 1`.
- `test_rescheduling_a_pending_plan_change_leaves_one_open_line` evaluated a
  lazily-built queryset **after** its scope closed. A queryset carries its
  Layer 2 filter but not its Layer 3 scope, so escaping the scope silently
  returns zero rows. Assertions moved inside the block.

The `in_tenant` fixture was reimplementing the middleware's mechanism rather
than calling it, which is how the leak survived a green suite. It now calls
`tenant_db_scope` directly.

### P0-2 · `TenantTask` establishes Layer 2 and not Layer 3

**What.** `TenantTask` re-establishes the tenant *context* across the queue
boundary — that is R-07, and it works. Nothing establishes the RLS variable,
because a base class cannot choose a task's transaction boundaries. So a task
that queries its own tenant's rows satisfies the ORM filter, matches zero
rows at the policy, and reads that as an answer.

**Impact, live.** `platform.notifications.deliver` is the only `TenantTask` in
the system. Proven:

```
PROBE-4 attempt_delivery under task-shaped context -> 'missing'
```

Every notification dispatched to a real worker would have reported `missing`
and returned success. No error, no retry, no delivery. The suite could not
see it: `CELERY_TASK_ALWAYS_EAGER` runs the task inline, inside the caller's
request scope — the one place the variable is already set.

**Fix, in two parts.** The task enters `organization_db_scope` itself (it must
be the task, not the service: `attempt_delivery` cannot know the organization
until it has read the row it needs the scope to see).

And the general guard: `TenantScopedManager.get_queryset()` now asserts that
the **database** scope matches the context it is filtering by, and raises
`MissingTenantContext` naming Row-Level Security if it does not. Free on the
scoped path — the scope helpers record what they bound, so it is a
`ContextVar` comparison. A caller that took no scope pays one round trip to
confirm, and is then told it is broken rather than handed an empty list.

This converts the entire class from silent to loud, for requests, tasks,
management commands, shells and anything Phase 3 adds.

## 3. P1 findings

### P1-1 · The notification sweeper found nothing, always

`sweep_pending_deliveries` scanned `NotificationDelivery.unscoped` — a tenant
table — with no scope, so it matched zero rows on every run and logged
nothing, because `if due:` was false.

Three defects in one job:

1. The cross-tenant scan returns nothing (the P0-1 shape, enumeration half).
2. It was **not in `CELERY_BEAT_SCHEDULE`**, so nothing ran it.
3. `deliver_notification.apply_async` refuses to dispatch without tenant
   context, so if it *had* found a row it would have raised.

`09-failure-resilience.md` and `notifications/services.py` both name this job
as the reason a broker outage delays a notification rather than losing one.
That recovery path did not exist.

Rewritten in the shape the commercial sweeps use: enumerate organizations
from the control plane, enter each one's scope, dispatch under a routing-only
tenant context. Added to the beat schedule.

Two details in that context are deliberate. The actor ids are **nil**, not
borrowed, for the reason `audit.record_system` gives — a fabricated actor puts
a name against work nobody did — and a nil UUID cannot collide with a real
membership. The organization's status is **read, not assumed**: defaulting to
`active` would have let a suspended organization be written to from a
background path while the request path correctly refused, creating a second
R-10 write-block exemption by accident and with no ADR. That is the shape of
P2-6, and the sweeper was built not to be it.

Each dispatch is isolated, because the outage this job recovers from is a
broker outage and one failed dispatch must not abandon the batch — the
delivery stays pending for the next pass.

### P1-2 · Commercial history on a subscription was rewritable in place

`tenancy.0003` made published catalog and pricing rows immutable by trigger.
It did not protect the subscription rows that quote them — and the
denormalised snapshot on a subscription item exists *precisely* so history
survives a catalog row being superseded, closed, or restored from a different
backup. A snapshot a `QuerySet.update()` can rewrite provides none of that.

```
PROBE-5 item rows rewritten=1  subscription rows rewritten=1
        now unit=1 currency=USD
```

Exit criterion 8 ("Items preserve commercial history") and the security
review's answer to "can historical pricing mutate?" were both true of the
catalog and false of the copy.

The currency case is the sharpest: `Subscription.currency` is fixed at
creation and every item snapshot is denominated in it. Changing one without
the other leaves a subscription whose lines are priced in a currency it does
not settle in — internally consistent on both sides, detectable by nothing.

**Fix.** `tenancy.0004` freezes the commercial columns of `subscriptions_item`
and the currency of `subscriptions_subscription` by trigger, in the same shape
`make_immutable_when_published` already uses. `removed_at` stays mutable —
closing a line is how supersession is recorded, exactly as `valid_to` is for a
published price — as do every lifecycle column the state machine moves.

### P1-3 · The per-request entitlement memo was never installed

`entitlement_request_scope()`'s docstring said "Installed by the tenancy
middleware and by TenantTask". Neither installed it. `grep` found it only in
tests and in the R-14 benchmark, which installs it by hand.

So the benchmark measured a configuration production did not have: every
entitlement check was a cache-backend round trip, and "75 repeat checks cost
0 queries" was true of the benchmark and not of a request.

**Fix.** `EntitlementRequestScopeMiddleware`, registered inside
`TenancyMiddleware`. It lives in `platform/entitlements` rather than in the
tenancy middleware because `platform/tenancy` sits below it in the F-5
layering contract and may not import it — the same constraint means
`TenantTask` cannot install it, which the docstring now says instead of
claiming otherwise.

### P1-4 · A replayed command could return `None`

`_resolve_item_ref` returned `None` when a replay's `result_ref` was empty or
its row had been removed, making the caller's next attribute access the place
the failure surfaced. Now raises `CommercialRecordNotFound` naming the ref.

## 4. P2 findings

| # | Finding | Status |
|---|---|---|
| P2-1 | **Seven declared metrics had no emitter**, including `unscoped_query_total`, which `unscoped.py` told the reader it incremented, and `tenant_context_missing_total`, the alarm 11-observability.md says pages on any non-zero value | **Fixed.** All seven wired, plus `test_every_named_metric_has_an_emitter` so a constant added without a call site fails in CI rather than during an incident |
| P2-2 | `unscoped_db_scope()` had no caller anywhere and was a hazard in its own right: nested, it would have blanked the caller's scope | **Removed** |
| P2-3 | The failure-mode review claimed timestamps "come from PostgreSQL via `timezone.now()`". They come from the application server's clock | **Corrected**, with the real reason skew is tolerable |
| P2-4 | Subscription `status` has no database-level state machine; a direct `UPDATE` could set any value | **Accepted.** See §13 |
| P2-5 | "One seat per member per pool" spanning two grants is guaranteed by lock ordering rather than by a constraint | **Accepted.** See §13 |
| P2-6 | **A suspended organization cannot be notified at all.** `notify()` writes a `Notification` row, which the R-10 write block refuses for a read-only organization — so `platform.organization.suspended`, the template whose entire purpose is to tell a customer they have been suspended, cannot be sent under the state it describes. Verified: `OrganizationNotWritable: Cannot save Notification: organization is suspended` | **Reported, not fixed.** See below |
| P2-7 | The six P2s carried by Phase 2 (rate limiting, AAL step-up, over-limit sweep, `W360.E005`, O(organizations) sweeps, catalog caching) | Unchanged, triggers unchanged |

**Why P2-6 was not fixed here.** Nothing in production calls `notify()` yet —
`grep` finds only tests — so it is latent rather than live. And the fix is not
this audit's to make unilaterally: it needs either a second
`tenancy_write_block_exempt` model or a restructuring of notification writes,
and D-003 states plainly that `AuditEvent` is *"the only exemption; a second
requires an ADR"*. The argument for one is strong and symmetrical with D-003's
— the moment a customer most needs to be told something is the moment they can
be told nothing, and dunning email is how a suspended customer becomes a
paying one again. That is an architecture-review decision, not an audit fix.

**Trigger: the first code that notifies a suspended organization**, which is
the dunning process a payment provider will drive. `suspend_for_non_payment`
already exists and is the entry point it will use.

Recorded here rather than silently patched, and the sweeper added in P1-1 was
deliberately built to carry each organization's **real** status rather than
assuming `active` — which would have created exactly this exemption by
accident, from a background path, with no ADR.

## 5. The required adversarial question

> *What other bugs could exist that would leave the entire test suite green
> while producing silently incorrect production state?*

Six realistic categories for this codebase. Four were found to contain real
defects; all six now have a guard.

**1 · Two isolation layers that disagree, where disagreement is an empty
result.** The Phase 2 P0, and both P0s here. *Now guarded generally* by the
scope assertion in `TenantScopedManager`, rather than at the call sites where
instances were found.

**2 · A test-only mechanism that is more permissive than production.**
`django_db` shares one transaction, so a `set_config` from any earlier fixture
survives. That is what hid P0-1 and two broken tests. *Guarded* by the
`in_tenant` fixture now calling the production helper instead of
reimplementing it, and by the leak being fixed at the source.

**3 · A mechanism that only runs in eager mode.** `CELERY_TASK_ALWAYS_EAGER`
runs a task inline, inside the caller's transaction and scope — the one
environment in which a task that needs its own scope does not need one. That
hid P0-2 completely. *Guarded* by the two notification tests, which run the
task the way a worker does: context from the message, nothing else.

**4 · A documented control that is not wired.** Three instances: the
entitlement request scope, seven metrics, and the notification sweeper's beat
entry. Each was believed because it was written down. *Guarded* for metrics by
an AST test; the other two are now wired and tested.

**5 · A lazily-built queryset evaluated outside its scope.** Carries its
Layer 2 filter, not its Layer 3 scope, so it silently returns zero rows.
Found in one test. *Guarded* by the scope assertion firing at queryset
construction and by the leak fix making the boundary real.

**6 · An invariant held only in Python where the database could hold it.**
Found in the subscription snapshot (P1-2). *Guarded* by the new triggers; the
two remaining instances are argued in §13 rather than assumed.

## 6. Audit-by-audit results

| # | Area | Result |
|---|---|---|
| 1 | Tenancy / RLS | **2 defects** (P0-1, P0-2). Every `unscoped_access`, `organization_db_scope`, raw cursor, `set_config` and task enumerated and classified by intended / ORM / database scope |
| 2 | System job safety | **1 defect** (P1-1). Both commercial sweeps correct; the notification sweeper was not |
| 3 | Pricing history | **1 defect** (P1-2), in the subscription snapshot rather than in pricing. Catalog and price immutability triggers verified live. No float anywhere; unsupported FX raises |
| 4 | Subscription state machine | Clean. Transition table is total, self-transitions are no-ops, effective dating respected, cancel-at-period-end and immediate both correct, replays return the original result |
| 5 | Bundles | Clean. Duplicate items refused at draft, expansion atomic and idempotent, `uq_one_open_item_per_product` proved under 8-way contention |
| 6 | Licensing / seats | Clean. Lock-all-grants-in-id-order plus in-transaction count plus partial unique. See §13 for the one invariant that stays in the lock |
| 7 | Entitlement single writer | Clean. Import contract, AST sweep, and F-6 (no module outside `platform/entitlements` may import its models) together close it |
| 8 | Entitlement reconciliation | **Hardened.** Three selectors now assert the scope they opened took effect, at zero query cost |
| 9 | Idempotency | **1 defect** (P1-4). Claim-in-transaction, fingerprint mismatch refused, key not burned on failure — all verified |
| 10 | Transaction boundaries | Clean. No external I/O in a transaction (AST-asserted), events published in the business transaction, audit failure fails the operation |
| 11 | Outbox / delivery | Clean. `SKIP LOCKED` claim, claim committed before publishing, stale-claim sweeper, bounded attempts, DLQ |
| 12 | Authorization | Clean. Two barriers on the control plane, scoped manager closes IDOR, uniform 404 |
| 13 | Database invariants | **1 defect** (P1-2), fixed with a trigger. Two accepted, argued in §13 |
| 14 | Concurrency | Clean. Re-ran with real threads; the new restore adds no contention (it is one `set_config` on a connection already held) |
| 15 | Performance | Two pinned counts raised by one statement each, for the restore. Both documented against the defect they prevent |
| 16 | Failure / cascade | **1 defect** (P1-1's missing per-dispatch isolation). Bounded retries with full jitter, time limits, `expires` below each interval, per-item isolation everywhere else |
| 17 | Observability | **1 defect** (P2-1). Seven metrics wired; cardinality guard unchanged |
| 18 | Codebase weight | One dead helper removed. Everything else added is load-bearing and tested |
| 19 | Phase 1 carryover | See §14 |

## 7. Silent-failure findings

Every one of these returned a plausible answer rather than an error.

| Where | Looked like | Actually was |
|---|---|---|
| A nested scope's caller | "my own tenant's rows" | another tenant's rows |
| `deliver_notification` | "that delivery does not exist" | RLS matched zero rows |
| `sweep_pending_deliveries` | "nothing is pending" | RLS matched zero rows |
| A revocation test's DELETE | "revoked, no effect" | deleted nothing |
| A queryset evaluated after its scope | "no open lines" | three lines, invisible |
| `_resolve_item_ref` | `None` | the replay had lost its answer |
| Seven metrics | "the alarm is at zero" | the alarm cannot fire |

## 8. Pricing / history findings

Historical correctness now holds at three levels rather than two:

1. The pinned `PlanVersion` and `PlanPrice` are immutable by trigger.
2. The denormalised snapshot on the item is immutable by trigger (**new**).
3. `Subscription.currency` cannot drift from the lines denominated in it
   (**new**).

A price rise still publishes a new row and closes the old one. `close_price`
still works; `removed_at` on a line still works. Both are pinned by tests that
would fail if the freeze listed one column too many.

## 9. Security findings

No new authorization defect. The control-plane split holds: a customer
administrator cannot reach catalog or pricing management, and staff status
alone does not grant it either.

The cross-tenant read in P0-1 is a security finding as much as a correctness
one, and is the most serious thing in this audit. It required no attacker: an
ordinary code path in the next phase would have produced it.

## 10. Failure-resilience findings

The notification sweeper failing on the outage it exists to recover from
(P1-1) is the only new one. Fixed with per-dispatch isolation.

Degradation directions re-checked and unchanged: nothing in the dependency
matrix causes a revocation, because revocation requires a positive durable
decision read by a computation that only runs when the database is available.

## 11. Tests added

**20 net new tests**, 627 → **647**.

| Where | Tests | What they pin |
|---|---|---|
| `tests/isolation/test_rls_isolation.py` | 6 | Nested scope does not leak; does not widen what the caller reads; error path leaves the caller scoped; outermost scope needs no restore; a tenant query with no database scope **raises**; a raw `set_tenant_variable` still satisfies the assertion |
| `tests/integration/test_commercial_failure_modes.py` | 3 | Reading another organization does not rescope the caller; provisioning a foreign organization is refused rather than misapplied; a cross-tenant selector verifies its own scope |
| `platform/notifications/tests` | 3 | The delivery task works with no ambient scope; the sweeper finds work without a tenant variable; the sweeper survives a broker outage |
| `commercial/subscriptions/tests` | 4 | The snapshot cannot be rewritten; closing a line still works; currency cannot drift; lifecycle columns stay mutable |
| `tests/observability/test_correctness_alarms.py` | 3 | Unscoped access counted; missing context counted; suspension block counted |
| `platform/observability/tests` | 1 | Every declared metric has an emitter |

Two existing tests were corrected to assert what they claimed (§2).

## 12. Architecture deviations

**None introduced.** No new ADR, no new debt-register row, no import-linter
contract weakened — `lint-imports` reports 11 kept, 0 broken, unchanged.

One layering constraint was *respected* at a cost worth recording: the
entitlement request scope could not be installed by the tenancy middleware or
by `TenantTask`, because `platform/tenancy` sits below `platform/entitlements`
in F-5. A separate middleware was added rather than the contract relaxed.

The three modules Phase 2 added to the `unscoped` allowlist are unchanged.
`platform/notifications/tasks.py` was already on it.

## 13. Accepted residual risk

Two invariants remain held outside the schema. Both are argued rather than
assumed.

**Subscription `status` has no database state machine.** A trigger encoding
`TRANSITIONS` would be a second implementation of the table in SQL, and the
audit's own rule is not to maintain fragile hand-written lists. What prevents
arbitrary mutation instead: nothing outside `commercial/` may import
`subscriptions.models` (import-linter), no module may query another module's
tables (F-6, AST), and `system.py` — the only writer without an actor — takes
no caller-supplied status. The freeze trigger now covers the columns where a
wrong value is *unrecoverable*; status is recoverable by correcting it.

**One seat per member per pool, across two grants in that pool.** The unique
constraint is `(seat_grant, membership)`, so it cannot span grants; the
guarantee comes from `select_for_update` over **every** grant in the pool, in
id order, before the existence check. Two concurrent assignments for the same
member therefore serialise on a shared row. Making it a constraint would mean
denormalising `pool_key` onto `SeatAssignment`. **Trigger for doing so:** the
seat-management UI, which is the first thing that will assign seats outside
this service.

## 14. Phase 1 carryover — trigger reached?

| Item | Trigger reached | Evidence |
|---|---|---|
| Circuit breakers | **NO** | Phase 2 still adds no external dependency; `commercial/` imports no HTTP client, AST-asserted. SMTP remains the only external call |
| Backpressure / load shedding | **NO** | No public traffic. Both async tasks are scheduled, not demand-driven |
| Rate limiting | **NO** | Nothing publicly exposed. Unchanged from Phase 2's assessment |
| Entitlement-version query | **NO — but the measurement was wrong** | The R-14 budget was being measured with a request scope that production did not install (P1-3). Now installed; the Phase 1 trigger (p95 > 4 ms or hit ratio < 0.95) still needs production metrics that do not exist |
| Breach-corpus passwords | **NO** | Untouched, no Phase 2 or audit trigger |
| Malware scanning | **NO** | Untouched, no Phase 2 or audit trigger |
| Chaos / dependency-outage testing | **NO** | No staging environment exists. Fault injection at unit level proves the code paths, not the operational behaviour |

Nothing was built without trigger evidence.

## 15. Quality gates

Run on a **dropped and recreated** test database.

```
pytest            647 passed, 0 failed, 0 skipped
ruff check        All checks passed
ruff format       214 files already formatted
mypy              Success: no issues in 17 source files
bandit -ll        0 high, 0 medium, 0 #nosec
pip-audit         No known vulnerabilities found
lint-imports      11 contracts kept, 0 broken
manage.py check   no issues (0 silenced)
check --deploy    6 issues — byte-identical to the Phase 1/2 baseline
makemigrations    No changes detected
```

## 16. Files changed

**Production code**

| File | Change |
|---|---|
| `platform/tenancy/db.py` | Scope helpers capture and restore (`_db_scope`, `_bind_tenant_variable`, `_restore_tenant_variables`); `assert_tenant_scope`; `unscoped_db_scope` removed |
| `platform/tenancy/models.py` | Scope assertion in `TenantScopedManager`; three metrics wired |
| `platform/tenancy/unscoped.py` | `unscoped_query_total` wired |
| `platform/tenancy/tasks.py` | `task_failures_total`, `task_retries_total` wired |
| `platform/tenancy/migrations_helpers.py` | `freeze_columns()` |
| `platform/tenancy/migrations/0004_commercial_history_immutable.py` | **New.** Two freeze triggers |
| `platform/notifications/tasks.py` | Delivery task enters its own scope; sweeper rewritten per organization with per-dispatch isolation and a nil-actor routing context carrying the real organization status |
| `platform/organizations/selectors.py` | `organization_status()`, so a cross-tenant job reads a status rather than assuming one |
| `platform/entitlements/middleware.py` | **New.** Installs the per-request memo |
| `platform/entitlements/selectors.py` | Scope assertions in `active_grants`, `stored_snapshot` |
| `platform/entitlements/services/read.py` | `entitlement_check_duration_seconds` wired; docstring corrected |
| `platform/entitlements/services/write.py` | `apply_entitlements` uses `organization_db_scope` instead of a bare `set_tenant_variable` |
| `commercial/subscriptions/selectors.py` | Scope assertions in `commercial_state`, `subscriptions_due_for_attention` |
| `commercial/subscriptions/services.py` | `_resolve_item_ref` raises instead of returning `None` |
| `integration/events/tasks.py` | `outbox_pending_count` gauged from the relay |
| `config/settings/base.py` | Entitlement middleware registered; notification sweeper added to the beat schedule |

**Tests**

| File | Change |
|---|---|
| `conftest.py` | `in_tenant` calls `tenant_db_scope` instead of reimplementing it |
| `tests/isolation/test_rls_isolation.py` | +6 |
| `tests/integration/test_commercial_failure_modes.py` | +3 |
| `tests/observability/` | **New package**, +3 |
| `tests/performance/test_commercial_queries.py` | Two bounds raised by one, each documented |
| `platform/observability/tests/test_observability.py` | +1 |
| `platform/notifications/tests/test_notifications.py` | +3 |
| `platform/authz/tests/test_authz.py` | Revocation test now enters a scope and asserts the delete |
| `commercial/subscriptions/tests/test_subscriptions.py` | +4; reschedule test asserts inside its scope |

**Documentation**

`docs/phase-2/opus-audit.md` (this file), `performance-review.md`,
`failure-mode-review.md`, `phase-2-status.md`.

## 17. Unresolved debt

D-001 through D-006 unchanged and within their review dates. No new entry —
nothing in this audit required an exception.

The six P2s Phase 2 carried are unchanged. The two accepted residuals in §13
are new and carry triggers.

## 18. What is frozen for Phase 3

Everything Phase 2 froze (`phase-2-status.md` §8) stands. Four additions,
each of which this audit had to establish rather than assume:

32. **A tenant database scope is restored on exit.** Entering one is
    reversible; a scope that outlives its block is a cross-tenant read.
33. **A tenant-scoped query asserts both layers.** Application scoping
    without database scoping raises, naming RLS. Fail-closed means *raise*,
    never *return nothing*.
34. **A background job establishes its own database scope.** `TenantTask`
    carries the context; Layer 3 is the task's own responsibility, because a
    base class cannot choose a task's transaction boundaries.
35. **A commercial fact is immutable once written.** Not only the catalog row
    it quotes — the snapshot too, and the currency it is denominated in.
    `removed_at` and the lifecycle columns are the exceptions, and they are
    listed in one migration.

---

**Phase 3 has not begun.**
