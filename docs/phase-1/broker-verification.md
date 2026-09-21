# Broker Verification — Spike R-09

**Status:** Complete · **Verdict: PASS**
**Date:** 2026-09-21
**Risk:** [R-09](../architecture/15-risk-register.md#r-09--broker-capability-mismatch-celery--sqs) (P1)

---

## 1. The question

> Celery's SQS transport has real limitations: no broker-side priority, no
> remote control or worker events, visibility-timeout semantics that interact
> awkwardly with long tasks, and FIFO throughput caps if ordering is needed.
> Our design assumes per-aggregate ordering via partition key, which standard
> SQS does not provide.

## 2. Decision

**AmazonMQ (RabbitMQ) is the chosen broker. `amqp://` in every environment.**

This is an **operability** decision, not a correctness one. The design
deliberately does not depend on strict queue ordering — consumers are
idempotent and order-tolerant by construction (ADR-008) — so either broker
would be *correct*. RabbitMQ is chosen because it provides four things SQS
does not, each of which we would otherwise have to build or do without.

## 3. What was measured

`backend/tests/spikes/spike_r09_broker.py`, against RabbitMQ 3.13 — the
engine behind AmazonMQ.

SQS cannot be measured here: it is a hosted AWS service. Its capabilities are
read from the kombu transport's own declared feature set where the package is
installed, and taken from documentation otherwise. **Every such row is
labelled**, so the distinction between measured and documented is never lost.

```
SPIKE R-09 RESULT: PASS
```

| Requirement | Result | Evidence |
|---|---|---|
| Broker reachable | **PASS** | `amqp at 127.0.0.1:5682` |
| Three queue groups configured | **PASS** | `('critical', 'default', 'heavy')` |
| Every task declares a queue | **PASS** | 6 tasks routed, 0 unrouted |
| Task distribution | — | `critical: 1, default: 4, heavy: 1` |
| Priority-enabled queues accepted | **PASS** | `x-max-priority` declared on all three |
| **Broker honours priority** | **PASS** | Published `low, high, mid` → consumed **`high, mid, low`** |
| FIFO within one queue | *observed* | 50/50 in order — **recorded, not relied upon** |
| Unacked message is redelivered | **PASS** | Redelivered with the `redelivered` flag set |
| Acked message is not redelivered | **PASS** | Queue empty after ack |
| Rejected message reaches the DLQ | **PASS** | Routed via `x-dead-letter-exchange` |
| Duplicate delivery absorbed | **PASS** | 5 deliveries → 1 applied, 4 rejected by the unique constraint |
| Retries bounded | **PASS** | `TASK_MAX_RETRIES=5`, cap 3600 s |
| Backoff uses full jitter | **PASS** | 200 samples at attempt 3 → **200 distinct** values in [0.42 s, 15.90 s], ceiling 16 s |
| Backoff grows exponentially | **PASS** | Observed ceilings by attempt: 3.9, 6.8, 15.3, 30.3, 63.9 s |
| Visibility timeout exceeds the longest task | **PASS** | 900 s vs 600 s (`prune_published`) |

## 4. RabbitMQ versus SQS, requirement by requirement

| Requirement | RabbitMQ / AmazonMQ | SQS | Source |
|---|---|---|---|
| Task routing to named queues | Yes — measured | Yes (one SQS queue per Celery queue) | measured / documented |
| **Broker-side priority** | **Yes** — measured, `x-max-priority` | **No.** SQS has no priority concept; the workaround is separate queues per priority and client-side polling weights | measured / kombu transport |
| Dead-letter routing | Yes — measured, `x-dead-letter-exchange` | Yes, via a redrive policy | measured / documented |
| Visibility timeout / redelivery | Yes — measured; unacked messages return to the queue | Yes, but the timeout is per-queue and a task exceeding it is **redelivered while still running** | measured / documented |
| Per-aggregate ordering | Best-effort within a queue | Only with FIFO queues, which cap at 300 msg/s (3 000 with batching) per queue | measured / documented |
| Worker events / remote control | Yes | **No.** `celery inspect`, `celery control` and the events stream do not work on SQS | documented (kombu transport declares no `events` support) |
| Publisher confirms | Yes | n/a (HTTP API returns a message id) | kombu transport |
| Long polling / cost model | Persistent connection | Per-API-call billing; idle workers poll continuously | documented |

### Why priority and observability decided it

Two rows above are the ones that matter.

**Observability.** Losing `celery inspect` and the worker event stream means
losing the ability to answer "what is this worker doing right now?" during an
incident. [11-observability.md](../architecture/11-observability.md) §12 makes
diagnosability a release requirement; a broker that removes the standard
tooling works against it directly.

**Priority.** The bulkhead design uses `critical` for the outbox relay. With
no broker-side priority, keeping the relay ahead of bulk work means more
queues and client-side polling weights — which is R-12's operational load
arriving through the back door, immediately after deciding to start with
three queues.

Neither is a correctness argument. Both are operational cost, which is exactly
what R-09 framed the decision as.

## 5. Ordering — stated plainly

FIFO order was observed (50/50 messages in order with one consumer) and is
**recorded as an observation, not a guarantee.**

[07-cross-product-integration.md](../architecture/07-cross-product-integration.md)
§3.6 states that global ordering and cross-product ordering are not provided,
and that consumers must tolerate out-of-order and duplicate delivery. Nothing
in Phase 1 depends on order:

- `ProcessedEvent` has a unique constraint on `(consumer, event_id)`, so a
  duplicate is a no-op regardless of when it arrives.
- The entitlement writer is **diff-from-truth**, not incremental, so an
  out-of-order recompute converges to the same state.
- Handlers are "set to this state", never "increment by this delta".

This measurement exists so that nobody later mistakes "it looked ordered in
testing" for a contract.

## 6. Configuration adopted

```python
CELERY_QUEUE_GROUPS = ("critical", "default", "heavy")   # R-12: three, not seven

CELERY_TASK_ACKS_LATE = True             # paired with idempotency
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1    # long tasks must not hoard messages
CELERY_TASK_SERIALIZER = "json"          # pickle is banned (12-security.md s7)
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 900}

TASK_MAX_RETRIES = 5
TASK_RETRY_BACKOFF_BASE_S = 2.0
TASK_RETRY_BACKOFF_CAP_S = 3600.0
```

**Routing is declared per task, from day one.** That is the property that
makes starting with three queues safe: splitting `heavy` into `imports`, `ai`
and `reports` later is a deployment change (start more workers, add routes),
not a change to any business code. System check `W360.E007` fails the build
for a task with no queue or no time limits.

Current routing:

| Queue | Tasks | Rationale |
|---|---|---|
| `critical` | `integration.events.relay_outbox` | A stalled relay stops cross-product automation for every tenant; it must not queue behind a bulk import |
| `default` | notification delivery, sweeper, stale-claim release | Ordinary asynchronous work |
| `heavy` | `integration.events.prune_published` | Long-running maintenance; isolated so it cannot starve the rest |

### Visibility timeout

900 s against a longest `time_limit` of 600 s. The check is asserted, not
assumed: if the visibility timeout were shorter than a task's time limit, a
slow task would be redelivered **while still running** and the same work would
execute twice concurrently. Survivable because tasks are idempotent, but
wasteful and productive of confusing duplicate side effects — and it is the
specific SQS semantic R-09 flags.

## 7. Limitations of this verification

Stated rather than glossed:

1. **SQS was not executed.** Its rows are documentation-derived and labelled
   as such. Should AmazonMQ operational cost later argue for SQS, that
   comparison needs its own spike.
2. **Single-node RabbitMQ.** AmazonMQ in production is a cluster; failover and
   partition behaviour are not covered here and belong in the failure-test
   suite against staging.
3. **The relay dispatches in-process in Phase 1.** The broker round trip is
   exercised by this spike rather than sitting on the relay's critical path
   before a broker decision existed. ADR-008 anticipates this: the relay's
   *destination* changes on extraction, the publishing code does not.
4. **No sustained-throughput measurement.** Capacity planning needs a load
   test against realistic volumes, which Phase 1 has no traffic to model.

## 8. Conclusion

| | |
|---|---|
| **R-09 status** | **Verified. Broker decided: AmazonMQ (RabbitMQ).** |
| **Correctness dependency** | None. Consumers are idempotent and order-tolerant; either broker would be correct. |
| **Deciding factors** | Broker-side priority and the Celery observability toolchain |
| **Queue groups** | Three (`critical`, `default`, `heavy`), with per-task routing so splitting is a deployment change |
| **Revisit trigger** | AmazonMQ operational cost becomes material, or a managed-service constraint forces SQS. Consumers need no change if it does. |

Raw results: `backend/tests/spikes/r09_results.json`.
