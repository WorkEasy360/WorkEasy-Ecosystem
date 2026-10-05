# Phase 6 — Performance

**These are local measurements, not production throughput.** They were
taken on:

- one Windows 11 developer workstation (24 logical CPUs);
- PostgreSQL 16 in Docker, connected directly (test settings);
- eager Celery;
- no other test process running (an earlier run taken alongside the full
  suite was 2–3× slower on every timing and is not reported).

They establish **shape** (what grows with what) and **plans** (which index
serves which query). They do not establish capacity. The capacity question
needs the load environment described in `10-performance-concurrency.md`,
which does not exist yet.

Source: `backend/tests/performance/test_phase6_queries.py`, run with `-s`.
Query counts are **asserted**, not just printed.

## 1. Query counts do not grow with fan-out (Task 73)

Routing one event to N matching subscriptions:

| Subscriptions | Queries | Time |
|---|---|---|
| 0 | 5 | 6.9 ms |
| 10 | 10 | 19.9 ms |
| 100 | 10 | 37.0 ms |
| 500 | 10 | 99.0 ms |

Routing one event to N matching active rules (plus one subscription):

| Rules | Queries | Time |
|---|---|---|
| 0 | 10 | 16.7 ms |
| 10 | 14 | 24.6 ms |
| 100 | 14 | 41.1 ms |
| 500 | 14 | 95.5 ms |

The step from 0 to "some" is the bulk inserts and timers appearing. After
that, the count is flat: one indexed `IN` match, one bulk insert per table,
and one bulk timer upsert. The time that still grows with N is row
construction and the bulk `INSERT` itself, which is linear by nature.
Bounds keep N small in practice: at most 50 connections with 50
subscriptions each, and 100 rules per organization.

## 2. Ingestion (Task 74)

`ingest_product_event`, after client authentication, 20 sequential events:

| | |
|---|---|
| Queries | 14 per event (asserted ≤ 16) |
| p50 | 20.5 ms |
| p95 | 24.9 ms |

The count includes the two rate-limit buckets, the live entitlement
decision for writes, the idempotent insert in a savepoint, and the routing
timer. No per-subscription or per-rule work happens on the request path;
routing is asynchronous.

## 3. The Settings lists do not grow with the page (Tasks 73, 85)

Each list holds 60 rows, and pages of 5 and of 50 are requested through
the real HTTP stack (session, tenancy, permission):

| List | Queries (limit 5 / 50) | Time (limit 5 / 50) |
|---|---|---|
| Integrations (`connections`) | 17 / 17 | 27.0 / 29.7 ms |
| Webhook deliveries | 19 / 19 | 26.0 / 26.1 ms |
| Automations | 17 / 17 | 23.6 / 26.0 ms |

Everything past the middleware is one keyset page plus one batch query per
related label (connection names, event summaries). Nothing is fetched per
row.

## 4. Plans on a padded table (Task 75)

Three organizations, with the one under test holding 50 subscriptions and
the other two 3,000 each; 20,000 due-work rows spread over 48 hours;
statistics fresh (`ANALYZE` as the owner).

**Subscription match** (the routing query), scanned through an index, not
sequentially:

```
Bitmap Heap Scan on webhooks_subscription  (rows=50)
  Recheck Cond: (organization_id = …)
  Filter: (event_filter = ANY ('{product.example.record.created.v1,product.example.*}'))
  ->  Bitmap Index Scan on webhooks_subscription_organization_id_…
```

With every subscription on the same filter (the worst case for the
composite index), the planner picks the organization index. With many
distinct filters per organization, `ix_subscription_match (organization,
event_filter)` is the selective one. Either way, one tenant's scan is
bounded by its own ≤ 2,500 rows.

**Due-work claim** (the dispatcher's `FOR UPDATE SKIP LOCKED` CTE):

```
Update on scheduling_due_work w
  CTE due
    ->  Limit -> LockRows
          ->  Index Scan using ix_due_work_due on scheduling_due_work
                Index Cond: (due_at <= now)
  ->  Nested Loop -> Index Scan using scheduling_due_work_pkey
```

The assertions are that the subscription plan contains no
`Seq Scan on webhooks_subscription` and that the claim plan uses
`ix_due_work_due`.

On an unpadded table of about 600 rows, the planner correctly chose a
sequential scan. That first version of the test was measuring the fixture,
not the query, so the padding was raised to a realistic tenant share.

## 5. Outbound HTTP budget (Task 19)

Per attempt, at most:

| Step | Limit |
|---|---|
| DNS | 2 s |
| Connect | 3 s |
| Read | 10 s |
| Whole attempt (deadline) | 15 s |

One connection per attempt, no pool. A slow endpoint costs at most the read
timeout (`test_a_slow_endpoint_costs_at_most_the_read_timeout`), and at most
4 concurrent attempts per connection. A refused connection on this machine
takes about 2 s before the OS reports it; that is the OS's connect-refused
behaviour, not a platform timeout.

## 6. Frontend

Bundle delta in [07-api-and-frontend.md](07-api-and-frontend.md) §3:
+24 B gzip on the shared shell, and +12.4 KB gzip loaded only on the three
new pages.
