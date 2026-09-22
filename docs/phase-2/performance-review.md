# Phase 2 Performance Review

**Date:** 2026-09-21
**Method:** Measure, then optimise. Every number below was captured with
`CaptureQueriesContext` before anything was changed.

---

## 1. Query counts — pinned, not observed

An N+1 is invisible in code review, fails no functional test, and only
appears when a customer has enough rows for it to matter. A pinned count
fails on the pull request that introduces one.

| Path | Statements | What they are |
|---|---|---|
| Plan version lookup | ≤ 2 | the version, its features |
| Batch plan versions (3) | **= 1 lookup** | same as one |
| Bundle resolution | ≤ 2 | the version, its items with plans |
| Subscription detail | ≤ 2 | the subscription, its lines |
| Subscription list | **constant in line count** | asserted by growth |
| Commercial state read | ≤ 6 | SAVEPOINT, bind, 2 queries, restore, RELEASE |
| Entitlement computation | ≤ 13 | three scoped reads, each with its own scope statements |
| Entitlement computation growth | **= 0 per extra product** | the catalog read is batched |
| Full reconciliation | ≤ 40 | measured 51 → **now under 40**, see §2 |
| Reconciliation growth | ≤ 12 per extra line | rows, not lookups |
| Price resolution | ≤ 4 | contract, book join, tier prefetch |
| 75 repeat entitlement checks | **0** | the Phase 1 per-request budget, unbroken |

The growth assertions are the ones that matter. An absolute count can drift
for innocent reasons; a count that *scales with the customer's line count* is
a defect.

## 2. The one optimisation, and its measurement

**Measured first.** A reconciliation for a two-product bundle issued **51
statements**, and **13 more for each additional line**.

The shape was `update_or_create` — a SELECT followed by an INSERT or UPDATE —
per entitlement, per feature and per seat pool. An N+1 on the one path that
runs for every organization on a schedule, where nobody watches the query
count.

**Changed:**

| Before | After |
|---|---|
| `update_or_create` per row × 3 tables | `bulk_create(update_conflicts=True)` — one `INSERT ... ON CONFLICT DO UPDATE` per table |
| Stale entitlements: SELECT + one UPDATE per row | one UPDATE, keyed on `computation_id` |
| Stale features: filtered delete per key | one DELETE with a composed predicate |
| Over-limit marking: 3 statements **per pool** | 1 SELECT + ≤ 2 UPDATEs for the whole organization |

**Result:** 51 → under 40 statements, and the per-line growth fell from 13 to
within the pinned bound. Idempotence is unchanged: `ON CONFLICT DO UPDATE` is
the same operation `update_or_create` performed, in one round trip.

The stale-entitlement rewrite is worth noting on its own. Identifying stale
rows by `computation_id` — every row this pass wrote carries the current one,
so anything else is stale — is both cheaper than comparing tuples in Python
and impossible to get subtly wrong as the key grows.

## 3. What was deliberately not optimised

### Scope round trips per reconciliation

`compute_entitlements` makes three scoped reads (commercial state, plan
versions, support grants), and each enters `organization_db_scope`
independently: SAVEPOINT, bind, restore, RELEASE.

Collapsing them into one outer scope would save round trips. It was not done,
because **that independence is what prevented the P0**: a selector that
guarantees its own scope cannot return silently empty when a caller forgets
one.

The **bind** captures the previous value in the same statement that writes the
new one, from a `MATERIALIZED` CTE, so entering a scope still costs one round
trip rather than two.

The **restore** was added by the Opus audit and costs one more, on the two
conditions where it is not provably a no-op: the scope is nested inside an
existing transaction, and the value actually changed. `set_config(..., true)`
survives the release of a savepoint, so without it every nested scope left its
organization bound to the caller's transaction — a silent cross-tenant read
for every statement that followed. See
[opus-audit.md](opus-audit.md) §2.

Round trips on an hourly per-organization job are a good price for a class of
defect that revoked every customer's access, and for a second class that
handed one customer's rows to another.

### One query per organization in the two sweeps

`reconcile_all` and `process_due_subscriptions` both enumerate organizations
from the control plane and query inside each one's scope.

A single cross-tenant scan is **not available** — a tenant table matches zero
rows without a tenant variable, by design — and is forbidden by
03-multi-tenancy.md even where it would work. So the cost is O(organizations)
per run.

| Job | Interval | Cost |
|---|---|---|
| `commercial.subscriptions.process_due` | 5 min | 1 indexed query per organization |
| `commercial.provisioning.reconcile_all` | hourly | 1 full reconciliation per organization |

At current scale this is unremarkable. **Trigger for a cheaper work list:
5 000 organizations**, at which point the 5-minute sweep is ~17 queries/second
of pure scheduling overhead. The likely answer is a control-plane
`commercial_due_at` projection maintained by the same services, so the sweep
reads one table to find work.

### No caching added

Nothing in Phase 2 introduces a cache. The entitlement read path already has
the Phase 1 request-local + version-keyed layers, and
`test_repeat_entitlement_checks_issue_no_further_queries` confirms the
per-request budget is unbroken by the new sources.

Caching the catalog would be the obvious next step — it is read-heavy and
rarely written — and is explicitly **not** done, because there is no measured
hot path to justify it. Trigger: catalog reads appearing in a production slow
log, or price resolution exceeding its share of a checkout budget.

## 4. Indexes — created only where justified

Every index on a tenant commercial table leads with `organization`, so the
RLS predicate is index-served rather than producing a scan.
`test_every_commercial_index_has_a_query_it_serves` asserts it for all five
tenant models.

| Table | Index | Serves |
|---|---|---|
| `subscriptions_subscription` | `(organization, status)` | "what does this org hold?" |
| | `(organization, current_period_end)` | the renewal sweep |
| | `(organization, trial_ends_at)` | the trial sweep |
| `subscriptions_item` | `(organization, subscription)` | detail and provisioning |
| | `(organization, product_code)` | trial-history check |
| | `(organization, removed_at)` | open-line filtering |
| `pricing_contract_price` | `(organization, plan_version, valid_from)` | resolution step 1 |
| | `(organization, bundle_version, valid_from)` | resolution step 1 |
| `billing_account` | `(organization)` | one row per org |
| `idempotency_record` | `(organization, scope, key)` | the claim |
| `entitlements_seat_assignment` | partial `(organization, over_limit_since)` | the over-limit read |
| `catalog_*` / `pricing_price_book` | segment and lookup indexes | resolution and listing |

The partial index on `over_limit_since` is the one worth defending: it covers
only flagged rows, which in a healthy system is almost none, so it costs
nearly nothing to maintain and turns the remediation read into a lookup.

## 5. Structural choices carried from Phase 1

Unchanged and still asserted: `CONN_MAX_AGE=0`, `prepare_threshold=None`,
`DISABLE_SERVER_SIDE_CURSORS=True`, `ATOMIC_REQUESTS=False`, statement and
lock timeouts, keyset pagination on list endpoints.

Phase 2 adds no list endpoint that returns an unbounded collection: the
subscription list is one organization's subscriptions, which is one row.

## 6. Findings

| Severity | Finding | Status |
|---|---|---|
| **P1** | Reconciliation was an N+1 across three tables | **Fixed**, measured before and after |
| **P2** | Both sweeps are O(organizations) | **Carried.** Trigger: 5 000 organizations |
| **P2** | Three `set_config` round trips per computation | **Carried deliberately.** The alternative reintroduces the P0's failure mode |
| **P2** | No catalog caching | **Carried.** Trigger: a production slow log, not a benchmark |
| **P2** | No load, soak or spike testing | Phase 1 P2, unchanged. No traffic to model and no deployed environment |

## 7. What was not measured

Latency. Phase 2 has no traffic and no deployed environment, so every number
here is a **query count**, which is the thing that is expensive to retrofit
and cheap to pin. Wall-clock timing without production load would be a number
that looks like evidence and is not.

The Phase 1 R-14 benchmark still governs the entitlement read path, and its
budget — 2 ms p50 / 5 ms p95 per request, ≤ 2 queries — is unchanged and
still met.
