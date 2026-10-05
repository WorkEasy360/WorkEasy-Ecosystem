# Phase 8 — Unified analytics

Decision record: [ADR-032](../architecture/adrs/ADR-032-analytics-semantic-and-event-model.md).

## 1. One ingestion path (Tasks 60, 61, 67, 68)

Analytics has **no ingestion endpoint of its own**. A product publishes its
events exactly as in Phase 6 (`POST /api/v1/integrations/events`,
`product.<code>.<aggregate>.<verb>`, versioned, declared in its manifest). By
the time analytics sees an event the platform has authenticated the product,
bound the organization through the live entitlement decision, checked the
namespace and the declaration, and recorded the event idempotently.

When routing fans the event out, a consumer registered with
`integration/routing/consumers.py` writes **one fact per active metric** the
owning product defined over that event type and version — in the routing
transaction and the organization's scope, so:

- **exactly once** — a duplicate event is never routed, routing is idempotent,
  and `analytics_fact` is unique on (organization, source, event, metric,
  version) besides (`test_one_event_published_eight_ways_is_one_fact`);
- **the platform never queries a product's database** — the only input is
  what the product chose to publish;
- **cheap on the routing path** — one indexed read of definitions, one insert,
  one timer. Aggregation happens later; a projection problem can never hold
  up a webhook or an automation.

## 2. The metric registry (Tasks 62-65, 77)

`POST /api/v1/integrations/analytics/metrics` with a `metric` JSON field:

```json
{"key": "product.example.order_value", "version": 1,
 "description": "Value of orders placed", "unit": "currency",
 "aggregation": "sum", "value_type": "money",
 "event": {"type": "product.example.order.placed", "version": 1},
 "measure": "amount_minor", "currency_field": "currency",
 "dimensions": [{"name": "channel", "field": "channel",
                 "values": ["web", "store", "phone"]}],
 "shared_with": ["books"]}
```

| Field | Rule |
|---|---|
| `key` | `product.<authenticated code>.<name>` |
| `event` | an event type in the **same** product's namespace, declared in its current manifest |
| `aggregation` | `sum` or `count` (a count has no measure) |
| `value_type` | `integer` or `money` (integer minor units + `currency_field`) |
| `unit` | `count`, `items`, `currency`, `seconds`, `milliseconds`, `bytes`, `basis_points` |
| `dimensions` | ≤ 3, each an **allowlist** of ≤ 50 enumerated values; names describing a person or an identifier (`email`, `user_id`, `name`, `ip`…) are refused |
| `shared_with` | ≤ 20 product codes the owner lets query it |

**No arbitrary schema and no SQL.** A definition names one integer field, an
optional currency field and at most three enumerated dimensions. Versions are
immutable (identical re-registration is idempotent, a different one is `409`,
a trigger refuses `UPDATE`); changing a metric is registering its next
version. At most 200 metric versions per product. The platform defines no
product's business metric.

## 3. Dimension control (Task 65)

A value outside a dimension's allowlist is stored as `other`; a missing one
as `unknown`. So a user's email, a UUID or free text can never become an
aggregate key, however a product fills its events — the keyspace of a metric
is bounded by its definition
(`test_unbounded_dimension_values_collapse_into_other_and_unknown`).

## 4. Facts (Tasks 66, 67, 73)

`analytics_fact` is tenant-scoped (RLS), and a fact carries only what the
definition names: the measure (a count is 1), the currency, the mapped
dimensions, `occurred_at` (the event's own time) and the event id. Nothing
else in the payload is stored (`test_a_fact_carries_only_the_named_fields`).
Measures must be integers within ±10¹⁵ — floats are refused (money is never a
float) — and negative values are allowed, because **a correction is a new
corrective event**: facts are immutable (a trigger refuses any change but the
projection mark). A refused measurement is counted by reason
(`analytics_facts_rejected_total{reason}`) and skipped; it never blocks the
event's routing.

## 5. Aggregation (Tasks 69, 70, 72)

A per-organization due-work timer (`analytics.project`, heavy) projects new
facts, one organization per transaction, under a per-organization advisory
lock:

1. take up to 500 unprojected facts;
2. sum them per (metric, version, bucket, dimensions, currency) for the UTC
   **hour** and **day** of `occurred_at`;
3. add the sums with one `INSERT … ON CONFLICT DO UPDATE`;
4. mark the facts projected — in the same transaction as step 3.

A fact is added to its buckets **exactly once**: a crash rolls back both, a
duplicate dispatch finds nothing to do, and two workers serialize on the lock
(`test_concurrent_projections_add_every_fact_exactly_once`). **Late events**
are bucketed by when they occurred, so a late event corrects the historical
hour and day it belongs to (`test_hourly_buckets_follow_occurrence_time_not_receipt`).
Queries read aggregates only; nothing rescans facts. Month answers are summed
from day rows at query time.

## 6. Rebuild and reconcile (Task 71)

Aggregates are a projection; facts are the record.

```
manage.py w360_analytics_aggregates <organization-uuid> [--metric KEY]          # report drift, read-only
manage.py w360_analytics_aggregates <organization-uuid> [--metric KEY] --rebuild
```

`reconcile` recomputes from facts without writing and lists every bucket whose
stored sum or count differs. `rebuild` deletes the organization's aggregates
within the retained fact window and recomputes them in one statement per
granularity, under the projection's lock — safe against a live platform
(`test_rebuild_after_a_lost_projection_restores_every_bucket`). Buckets older
than the fact window (400 days) have nothing left to rebuild from and are
final.

## 7. The query API (Tasks 74-77, 79, 80, 108)

Product: `POST /api/v1/integrations/analytics/query`. Organization:
`POST /api/v1/orgs/<slug>/analytics/query` (`platform.analytics.view`).

```json
{"metric": "product.example.order_value", "version": 1,
 "start": "2026-09-01T00:00:00Z", "end": "2026-09-25T00:00:00Z",
 "granularity": "day", "group_by": ["channel"]}
```

| Bound | |
|---|---|
| granularity | `hour` (≤ 7 days), `day` (≤ 400 days), `month` (≤ ~3 years); bounds aligned to it |
| `group_by` | ≤ 2 of the metric's **own** dimensions |
| rows | worst case (buckets × each grouped dimension's values × currencies) ≤ 5,000, checked before the database is touched |

The statement is fixed; the caller's choices are bound parameters — no
expression, field name or filter language exists to turn into SQL. Every
query is served by the `(organization, metric, granularity, bucket)` index.

**Who may query what.** A product: its own metrics and metrics whose owner
listed it in `shared_with`; anything else is `404 metric_not_found`, identical
to an unregistered key. An organization member with `platform.analytics.view`:
metrics of products the organization holds. Every row is the caller's
organization's — from the authenticated context, in the statement, and by
RLS.

**Money.** A money metric's rows carry integer minor units and their
currency, and the currency is always part of the grouping: amounts in
different currencies are never summed into one number
(`test_amounts_in_different_currencies_are_never_added_together`).

**Freshness.** Every answer carries `freshness`: `pending_facts`,
`oldest_pending_since`, `last_projected_at`, `up_to_date` — a caller never
mistakes a lagging aggregate for real time
(`test_freshness_says_when_aggregates_lag`).

## 8. What the platform does not do

No product dashboard, no business interpretation of a metric, no backfill of
a new metric from past events (a metric counts events routed after it is
registered — a product that needs history republishes it as events), no
organization-local time buckets (UTC only), no warehouse, ClickHouse or
Kafka. See [12-known-debt.md](12-known-debt.md).
