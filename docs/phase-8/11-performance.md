# Phase 8 — Performance (Task 114)

Measured on a developer laptop (Windows 11): PostgreSQL 16 with pgvector in
Docker Desktop, one Django process driven through the real HTTP stack
(client authentication, rate limits, tenancy, rendering), and the loopback
provider answering instantly — so every millisecond is the platform's own.
One database round trip costs about 2 ms here (Docker on Windows), which
dominates every figure below. **These are local shapes, not capacity
claims**; no load test was run.

The test is `backend/tests/phase8/test_phase8_performance.py`
(`pytest -s -m slow …` reprints the tables). It asserts shape, not speed:
the query count of a retrieval and of an analytics query is identical at
10, 100, 500 and 5,000 rows, and each has an index-served plan (`EXPLAIN`
with sequential scans disabled needs no `Seq Scan` of `rag_chunk` or
`analytics_aggregate`).

## 1. AI gateway

| Path | p50 | max | Queries |
|---|---|---|---|
| authenticated no-op product call (list own skills) | 8.7 ms | — | — |
| sync invoke, no retrieval | 113.7 ms | 178.6 ms | 46 |
| gateway overhead (invoke − no-op) | ≈ 105 ms | | |
| reserve + settle alone (row locks, lease timer) | 43.7 ms | 47.9 ms | 23 |

The 46 statements of an invoke: client authentication (1), the skill (1),
the access decision — organization status, the entitlement decision for
writes, the plan's AI feature, the AI policy — (3 reads, each in its own
tenant scope), model selection with prices (2), the reservation transaction
(budget row locks, the in-flight count, two increments, the execution, the
lease timer), marking it running, the provider call, and the settlement
transaction (lock, two budget updates, the execution, the usage line, the
result, the timer). At ~2 ms per round trip that is the whole cost; the
provider's own latency adds to it in production.

Two reductions were made while measuring (53 → 46): the budget rows are
locked first and inserted only when missing (once a month per scope)
instead of an `INSERT … ON CONFLICT` on every reserve and settle, and the
result is stored with one upsert instead of `update_or_create`
(a `SELECT` and two savepoints).

## 2. RAG

Documents of up to 250 chunks submitted through the product endpoint and
indexed by the real pipeline (16-dimension loopback embeddings), one
organization per size; retrieval `top_k = 5` over one source:

| Chunks | Indexed in | Retrieve p50 | Retrieve max | Queries |
|---|---|---|---|---|
| 10 | 0.2 s | 86.2 ms | 147.7 ms | 56 |
| 100 | 0.3 s (297 chunks/s) | 99.9 ms | 140.0 ms | 56 |
| 500 | 0.7 s (722 chunks/s) | 101.3 ms | 173.1 ms | 56 |
| 5,000 | 8.3 s (601 chunks/s) | 200.0 ms | 1,302 ms | 56 |
| 10, with 5,600 other tenants' chunks in the table | | 94.4 ms (was 86.2) | | |

- **The candidate set, not the table, sets the cost**: the 10-chunk
  organization's retrieval is unchanged with 5,600 other chunks beside it.
- A retrieval is metered like any AI call (access decision, reservation,
  query embedding, settlement), which is most of its 56 statements.
- The one cost that grows is the exact scan of the authorized candidate set
  (every candidate's distance is computed): about 100 ms more at 5,000
  candidates here, and more with real 1,536-dimension vectors, which are
  stored out of line. That is the documented trigger for an approximate
  index ([05-rag.md](05-rag.md) §9); a single source is expected to hold
  thousands, not hundreds of thousands, of chunks.

## 3. Analytics

| | p50 | max | Queries |
|---|---|---|---|
| event ingestion through `/integrations/events` (authenticated, routed, fact written) | 49.8 ms | 57.0 ms | |

| Facts | Projected in | Query p50 (30 days × 3 statuses, daily) | max | Queries |
|---|---|---|---|---|
| 10 | 17 ms | 20.6 ms | 44.8 ms | 13 |
| 100 | 30 ms (3,382/s) | 18.7 ms | 40.5 ms | 13 |
| 500 | 71 ms (7,018/s) | 18.9 ms | 29.6 ms | 13 |
| 5,000 | 988 ms (5,059/s) | 21.6 ms | 24.4 ms | 13 |

Queries read pre-aggregated rows through the
`(organization, metric, granularity, bucket)` index, so their cost does not
depend on how many facts exist. Projection is linear in new facts only and
runs off the request path, 500 facts per transaction.

## 4. Two latency defects found by measuring

1. **Nagle's algorithm in the egress client.** `http.client` writes a
   request's headers and body in separate sends; with Nagle on, the body
   waits for the ACK of the headers, which a receiver waiting for the body
   delays (40 ms on Linux, 200 ms on Windows). The egress client now sets
   `TCP_NODELAY` on every connection — the same default urllib3 uses. One
   loopback embedding call: 10.2 ms → 2.5 ms p50. This also shortens every
   Phase 6 webhook delivery.
2. **The test harness.** The fake provider wrote its reply the same way
   (≈ 200 ms per call, now fixed), and the reference product client
   re-parses its RSA key to sign a `private_key_jwt` on every call
   (≈ 290 ms, client-side). The measurements use a `client_secret`
   credential so they report the platform's cost, not the harness's.
