# Phase 2 Concurrency Review

**Date:** 2026-09-21
**Method:** Real threads, real connections, real commits. Nothing mocked.

---

## 1. Why the tests look the way they do

`SELECT ... FOR UPDATE` behaves identically to a plain `SELECT` when nothing
else is running. A uniqueness constraint never fires in a single-threaded
test. A `ContextVar` looks fine until two requests interleave.

So a "concurrency test" that mocks concurrency proves the code runs, not that
the invariant holds. Every test in
`tests/integration/test_commercial_concurrency.py` runs
`pytest.mark.django_db(transaction=True)` with a `threading.Barrier`, so the
workers arrive together and genuinely commit.

That choice found two defects a non-transactional suite had hidden — see §4.

## 2. Contention points and what was proved

| # | Point | Mechanism | Result |
|---|---|---|---|
| 1 | **Seat allocation** | `select_for_update` on all grants (id order) + in-transaction count + partial unique constraint | pool of 10, 9 taken, 8 threads → **1 assigned, 7 refused, 10 total** |
| 2 | **Same member assigned twice** | partial unique `(seat_grant, membership) WHERE released_at IS NULL` | 8 threads → **1 seat consumed, 8 successes** (idempotent) |
| 3 | **Duplicate command delivery** | `IdempotencyRecord` unique `(organization, scope, key)` | 8 threads, one key → **1 item, 1 subscription, same id returned** |
| 4 | **First purchase race** | partial unique `(organization) WHERE status <> 'expired'` + `IntegrityError` re-read | 2 threads, different products → **1 subscription, 2 lines, no duplicate product** |
| 5 | **Bundle expansion** | idempotency key + `uq_one_open_item_per_product` | 8 threads, one key → **3 lines exactly, products `[books, crm]`** |
| 6 | **Concurrent activation** | row lock + idempotency + state machine | 8 threads → all return `active`, one transition |
| 7 | **Key reuse with different args** | fingerprint comparison inside the claim | 8 threads, 2 payloads → **1 item, conflicts raised** |
| 8 | **Concurrent plan change** | row lock + `uq_one_open_item_per_product` | 8 threads → **1 change applied**, 1 open CRM line |
| 9 | **Entitlement recompute** | `pg_advisory_xact_lock(hashtext(org))` | 8 threads → 1 snapshot, correct contents, version advanced monotonically |
| 10 | **Reconcile vs seat assignment** | consistent lock ordering on `SeatGrant` | 8 mixed threads → **no deadlock**, capacity 10, ≤ 10 assigned |
| 11 | **Double-fired scheduler** | state machine + durable timestamps | 4 concurrent clock runs → **trial expired exactly once** |

Points 1–3 and 9 are the four the Phase 0 contention list
(10-performance-concurrency.md §12) assigns to this phase. The rest were
added because the code created them.

## 3. The mechanisms, and why each was chosen

### Locks where the row exists

`select_for_update` on `SeatGrant` and on `Subscription`. Short
transactions, low-traffic rows, negligible contention.

**Ordered by id** when locking a set. Two callers working on overlapping
pools with different orderings deadlock under load — a failure that only
appears in production.

### Constraints where the row does not exist yet

A row lock cannot protect a row that has not been inserted. Three races are
resolved by PostgreSQL rather than by application logic:

```sql
UNIQUE (organization_id) WHERE status <> 'expired'                       -- one subscription
UNIQUE (subscription_id, product_code) WHERE removed_at IS NULL AND ...  -- one line per product
UNIQUE (organization_id, scope, key)                                     -- one command
UNIQUE (seat_grant_id, membership_id) WHERE released_at IS NULL          -- one seat per member
```

Every catch site uses a **savepoint**. PostgreSQL aborts the whole
transaction on a constraint violation, so catching `IntegrityError` without
one leaves the connection unusable and the caller's work unrollbackable —
turning a routine duplicate into a failed request.

### An advisory lock where the contention is a computation

Entitlement recompute is not protected by a row: it rewrites a whole
organization's worth of derived state.
`pg_advisory_xact_lock(hashtext(org_id))` serialises per organization, and
last-writer-wins is safe because compute is deterministic.

Transaction-scoped, never session-scoped: session locks are broken by
transaction pooling.

### Optimistic tokens where the gap is between read and write

`Subscription.version` catches a caller acting on a stale read even when the
two commands never overlap in time — the case a lock cannot see.

## 4. What the transactional tests found

Both were invisible to the non-transactional suite, because `django_db`
wraps everything in one outer transaction in which an earlier
`set_config(..., true)` survives.

### P0 — cross-tenant reads returned silently empty

`unscoped_access()` relaxes the ORM manager. It does **not** relax RLS, and
a query with no tenant variable set matches **zero rows** rather than
failing.

So `reconcile()` from a Celery worker read no subscriptions, computed an
empty desired state, and `apply_entitlements` dutifully expired everything
the customer had. Every customer, on a schedule.

Fixed: every cross-tenant selector now enters `organization_db_scope` as well
as `unscoped_access`. Regressed by
`test_reconciliation_works_with_no_ambient_tenant_variable`, which asserts
the *outcome* rather than the mechanism so it keeps working if the mechanism
changes.

The same defect existed in Phase 1's `bump_permission_version`, which would
have failed for any caller outside a request. Fixed identically.

### P0 — two concurrent first purchases created two subscriptions

`_open_subscription` locked-and-read, found nothing, and inserted. Two
threads both found nothing.

An organization with two subscriptions has two renewal cycles, two dunning
cycles, and the one-line-per-product constraint — which is *per subscription*
— cannot stop the same product appearing twice.

Fixed with the partial unique constraint above plus an `IntegrityError`
re-read, because both callers wanted the same thing and one of them got it.

## 5. Not covered

| Not tested | Why | Trigger |
|---|---|---|
| Multi-process contention | Threads share a process; PostgreSQL does not care, but a GIL-serialised section could hide a race | Load testing before first customer |
| Contention at realistic scale | 8 threads, not 800. Proves the invariant, not the throughput | Load testing |
| Broker redelivery under load | The R-09 spike covered the broker; the relay is single-consumer per event by `SKIP LOCKED` | Production traffic |
| Deadlock under mixed workload at scale | Lock ordering is consistent by construction and tested pairwise | Load testing |

None of these can change a correctness answer established above; they can
change a performance one.
