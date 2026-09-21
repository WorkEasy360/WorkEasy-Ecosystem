# ADR-008 — Transactional Outbox

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture

---

## Context

When a business change happens, a domain event must be published. The change lives in PostgreSQL; the event must reach a broker. These are two systems, and there is no distributed transaction between them.

Two naive orderings both fail:

- **Commit, then publish** — a crash between the two loses the event permanently. A deal is won but no invoice is ever created, and nothing indicates anything is wrong.
- **Publish, then commit** — a rollback after publishing emits an event for work that did not happen. An invoice is created for a deal that was never won.

Both failures are silent, and both corrupt business state in ways that are discovered weeks later.

## Decision

**Write events into an `OutboxEvent` table in the same database transaction as the business change, and relay them to the broker asynchronously.**

- `events.publish()` is an `INSERT`. No network call, no broker connection, no added request latency.
- A relay task claims batches with `SELECT ... FOR UPDATE SKIP LOCKED`, publishes, and marks published.
- Delivery is **at-least-once**; consumers are idempotent via a `ProcessedEvent` unique constraint inserted **in the same transaction as the handler's effect**.
- Ordering is guaranteed **per aggregate** (outbox `id` order, `aggregate_id` as partition key) and **not** globally.
- Published rows are archived and pruned after 30 days; a partial index keeps the claim query fast.
- Bounded retries with jittered backoff, then dead-letter with alerting and first-class replay tooling.

## Alternatives considered

### A. Publish directly in the request

- **Pro:** simplest; lowest latency to the broker.
- **Con:** both failure orderings above; adds broker latency and broker availability to every write; a broker outage becomes a write outage.
- **Rejected:** silent event loss is unacceptable for business facts.

### B. `transaction.on_commit(publish)`

- **Pro:** built into Django; fires only after a successful commit; looks correct.
- **Con:** the callback runs **in process memory with no durability**. A crash, deploy, OOM kill or worker restart between commit and callback loses the event with no trace. It also silently does nothing in `TestCase` unless `captureOnCommitCallbacks` is used, so tests pass while production loses events.
- **Rejected.** This is the most common wrong answer because it looks right; it converts a visible failure into an invisible one.

### C. Change Data Capture (Debezium on the WAL)

- **Pro:** no application changes; captures everything; genuinely exactly-once into the log.
- **Con:** events become row-change shapes rather than business facts, so consumers reverse-engineer intent from column diffs; a new infrastructure component (Kafka Connect, Debezium) with its own operations; schema changes ripple into the event stream; the domain language is lost.
- **Rejected for Phase 0:** heavy infrastructure, and it produces the wrong abstraction — we want `crm.deal.won`, not `UPDATE crm_deal SET stage=...`.

### D. Event sourcing

- **Pro:** events are the source of truth; no dual-write problem exists.
- **Con:** a fundamental change to how every module stores state; projections, rebuilds and versioning for every aggregate; a steep learning curve; only a few aggregates (ledger, stock, commissions) genuinely need temporal reconstruction — and those use append-only tables already.
- **Rejected:** solves the problem by reorganising the entire system.

### E. Transactional outbox (selected)

## Why selected

1. **Atomicity comes free from the database.** The event and the change commit or roll back together, with no coordination protocol.
2. **No added request latency.** Publishing is a local insert.
3. **Broker availability is decoupled from writes.** Broker down means events accumulate; business operations continue ([09](../09-failure-resilience.md)).
4. **Events stay domain-shaped**, in our language, versioned in `contracts/`.
5. **It is durable and inspectable.** Undelivered events are rows you can query, count, alert on and replay — not lost callbacks.
6. **No new infrastructure.** It uses PostgreSQL, which is already Tier 0.

## Consequences

**Positive**

- No lost events, no phantom events.
- Outbox lag is a directly observable pipeline-health metric.
- Replay is straightforward because envelopes are stored.
- `SKIP LOCKED` lets multiple relay workers scale without contention.

**Negative, accepted**

- Delivery latency of roughly one poll interval (target < 5 s). Acceptable for cross-product workflows; anything needing synchronous results should not be an event.
- The outbox table is write-heavy. Mitigated by a partial index, archiving and pruning; partitionable if volume demands.
- At-least-once requires idempotent consumers. Enforced by a helper and a mandatory duplicate-delivery test.
- Relay is a component that can stall. Monitored on **oldest pending age**, not depth, with paging thresholds.
- Bulk operations must aggregate events rather than emitting one per row.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Relay stalls unnoticed | **High** | `outbox_pending_age_seconds` alerts at 5 min / 30 min |
| Poison event blocks the batch | Medium | Per-event error isolation; bounded attempts; dead-letter |
| Consumer not idempotent | **High** | `ProcessedEvent` in the handler's transaction; required test |
| Outbox table grows unbounded | Medium | Archive + prune; monitored |
| Recovery stampede after an outage | Medium | Fixed batch size, bounded relay concurrency, dedicated queue and pool |
| Ordering assumed but not guaranteed | Medium | Documented; `aggregate_version` pattern available |

## Future extraction path

The outbox is the extraction enabler. Today the relay publishes to an in-process dispatcher and a broker; on extraction it publishes to a real topic that external services consume. **The publishing code does not change** — only the relay's destination. Consumers already handle duplicates and reordering, so their semantics are unchanged.

If volume demands it, the relay can be replaced with CDC on the outbox table specifically (not on domain tables), keeping domain-shaped events while removing the polling loop.

## Related

- [07-cross-product-integration.md](../07-cross-product-integration.md), [09-failure-resilience.md](../09-failure-resilience.md)
- [ADR-007](ADR-007-cross-product-communication.md), [ADR-012](ADR-012-failure-isolation-strategy.md)
