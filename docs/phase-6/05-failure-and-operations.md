# Phase 6 — Failure behaviour and operations

The integration platform is **Tier 3**: when it fails, sign-in, the app
launcher, entitlement checks and every product keep working. Its own work
is delayed, never lost. Nothing is ever duplicated beyond the documented
at-least-once edge ([03-webhooks.md](03-webhooks.md) §5).

## 1. Queues and tasks (Task 69)

| Task | Queue | Limits (soft/hard) | Triggered by |
|---|---|---|---|
| `integration.routing.route_event` | default | 30 s / 60 s | after-commit enqueue, due-work timer |
| `integration.automation.run_execution` | default | 60 s / 90 s | after-commit enqueue, due-work timer |
| `integration.automation.fire_schedule` | default | 30 s / 60 s | due-work timer |
| `integration.webhooks.deliver` | **heavy** | 60 s / 75 s | after-commit enqueue, due-work timer |
| `integration.scheduling.dispatch_due` | default | 60 s / 90 s | beat, every 5 s |
| `integration.routing.prune` | heavy | 600 s / 900 s | beat, daily |

No integration task runs on `critical`, the queue that carries the outbox
relay and security work (`test_integration_work_never_runs_on_the_critical_queue`).
Outbound HTTP runs only on `heavy`, so a slow customer endpoint can occupy
heavy workers but never the queues sign-in depends on. The pipeline tasks
set `max_retries=0`: retry is the pipeline's own durable state (a timer),
never a Celery retry.

## 2. The due-work timer (ADR-024)

Every piece of pending work is a durable database row plus a **`DueWork`
timer row written in the same transaction**. The after-commit enqueue is
only the fast path.

- `scheduling_due_work(kind, target_id, organization_id, due_at, claims)`,
  unique on `(kind, target_id)`. It holds only ids and times, never tenant
  data (`test_the_due_work_timer_holds_no_tenant_data`), which is why it is
  the one cross-tenant table without RLS (listed in
  `TENANT_SCOPE_EXEMPT`, owned by the migrator, DML-only for `w360_app`).
- `dispatch_due` claims up to 200 due rows with `FOR UPDATE SKIP LOCKED`
  (index `ix_due_work_due`). Each claim pushes the row's `due_at` forward by
  a lease that doubles with each re-claim (120 s × 2ⁿ, cap 3,600 s). A
  task that never runs is picked up again later; a poison row backs off
  instead of spinning.
- Completing work deletes its timer. Rescheduling (a retry, the next
  scheduled run) moves it.
- Dispatch runs each item under its own organization's context, with a
  per-item error boundary: one bad row cannot stop the batch
  (`test_one_failing_integration_leaves_the_others_unaffected`).

## 3. Failure matrix (Tasks 51, 70, 71)

| Failure | Behaviour | Evidence |
|---|---|---|
| Broker down at enqueue | The transaction has committed the work and its timer. A real unreachable broker fails each enqueue after its 1 s connect timeout (measured), so the request that accepted an event is at most ~1 s slower, and still `202`. **The first broker error ends any enqueue loop**: the after-commit fan-out and the dispatcher batch hand the remaining items to their timers instead of spending one second each, which would have held a worker for 200 s per dispatch batch or 500 s per large fan-out. Work resumes within one lease of the broker's return | `test_a_broker_outage_loses_no_work`, `test_an_after_commit_fanout_enqueue_stops_at_the_first_broker_error` |
| Worker killed mid-delivery / mid-execution | Its lease lapses. The next claim records the lost attempt as `abandoned` (it may have been sent) and continues | `test_a_worker_that_died_after_claiming_is_recovered` |
| Database error while recording an attempt | The attempt is recovered as `abandoned` on the next claim; the delivery is not lost | `test_a_database_failure_while_recording_is_recovered_as_an_abandoned_attempt` |
| Late worker finishing after a newer attempt | Fenced on the attempt number: the late result is counted and discarded | `test_a_late_acknowledgement_cannot_overwrite_a_newer_attempt` |
| Redis down | Rate limits degrade per the Phase 1 rule (fail open for limits, never for authorization); correctness unaffected | `test_a_cache_outage_degrades_limits_not_correctness` |
| Receiver down / slow / 5xx / 429 | Bounded retry with equal jitter and clamped `Retry-After`, then dead after 10 attempts or 3 days; at most 4 in flight per connection | `test_a_down_receiver_retries_with_growing_backoff_then_dies`, `test_a_slow_endpoint_costs_at_most_the_read_timeout`, `test_retry_after_is_honoured_but_clamped` |
| Receiver persistently failing | Circuit opens after 5 consecutive failures (30 s, doubling, cap 1 h), holding deliveries **without spending attempts**; half-open allows one probe. 50 consecutive failures auto-suspend the connection (audited) | `test_the_circuit_opens_and_holds_deliveries_without_charging_attempts`, `test_sustained_failure_auto_suspends_the_connection` |
| Backlog on one connection | More than 1,000 open deliveries: new ones are recorded dead `backlog_limit` rather than growing without bound | `test_a_backlogged_connection_does_not_grow_without_bound` |
| Organization suspended or read-only | No new events accepted (403). Pending deliveries and executions are **held** (re-timed, not attempted, not charged) and resume on reactivation | `test_a_suspended_organization_accepts_no_new_events`, `test_suspension_while_a_delivery_is_pending_holds_it`, `test_suspension_racing_deliveries_sends_nothing_after_it_commits` |
| Product integration suspended | Its events are refused (`integration_suspended`) | `test_a_suspended_product_cannot_publish` |
| Destination starts resolving to a private address | Refused at send time before a byte is sent; delivery dead `destination_blocked` | `test_rebinding_at_send_time_kills_the_delivery_without_sending` |
| Integration pipeline backed up | `/health/deep` reports it. Readiness never does: a backlog is not a reason to pull a healthy web instance | `test_integration_health_never_gates_readiness` |

## 4. Observability (Tasks 66–68)

Structured logs carry `organization_id`, `correlation_id` (propagated from
the event into every delivery and execution, and sent to receivers as
`X-W360-Correlation-Id`), ids and categories. They **never** carry payloads,
URLs beyond their origin, secrets or response bodies
(`test_no_secret_reaches_a_log_line`). The stored response excerpt is at
most 200 printable characters, is visible only to `connection.view`, and is
pruned with its attempt.

| Metric | Labels | Meaning |
|---|---|---|
| `integration_due_work_lag_seconds` (gauge) | — | age of the oldest overdue timer: **the** pipeline SLI |
| `integration_due_work_dispatched_total` / `_stuck_total` | kind | timers dispatched; timers claimed ≥ 10 times |
| `integration_events_rejected_total` | reason | ingestion refusals |
| `integration_routing_fanout` (histogram) | — | deliveries + executions per routed event |
| `webhook_deliveries_total` | result | terminal outcomes (`succeeded`, `dead`) |
| `webhook_delivery_attempts_total` | outcome | per-attempt outcomes by category |
| `webhook_retries_total`, `webhook_dead_letter_total` | reason | retry pressure; dead letters by reason |
| `webhook_deliveries_held_total` | reason | `organization`, `connection`, `circuit`, `concurrency` |
| `webhook_backlog_rejected_total`, `webhook_replays_total` | | |
| `webhook_late_completions_total`, `automation_late_completions_total` | | fenced-out late workers |
| `automation_executions_total` | result | `succeeded/skipped/failed/retry/dead/chain_depth_exceeded/rate_limited` |
| `automation_schedule_fired_total`, `automation_schedule_missed_total` | | firings; coalesced misses |
| `egress_requests_total` | outcome | `2xx`…`5xx`, `dns_failure`, `dns_timeout`, `connect_failed`, `connect_timeout`, `read_timeout`, `deadline_exceeded`, `tls_error`, `connection_lost`, `protocol_error`, `request_too_large`, `request_invalid` |
| `egress_rejected_total` | reason | SSRF refusals by reason |
| `egress_request_duration_seconds` (histogram) | | |

**Suggested alerts:**
- `integration_due_work_lag_seconds > 300` for 10 minutes (the pipeline is
  stuck);
- `integration_due_work_stuck_total` increasing (a poison timer);
- `egress_rejected_total{reason!~"scheme|port"}` increasing (someone is
  probing internal addresses);
- a `webhook_dead_letter_total` rate well above baseline.

## 5. Retention (Task 58)

`integration.routing.prune` (daily, heavy) removes per organization, in
bounded batches:

| Data | Kept | Setting |
|---|---|---|
| succeeded deliveries (+ attempts) | 7 days | `W360_RETENTION_SUCCEEDED_DAYS` |
| dead deliveries (+ attempts) | 30 days, the replay window | `W360_RETENTION_DEAD_DAYS` |
| terminal executions | 30 days | `W360_RETENTION_EXECUTION_DAYS` |
| routed events (payloads) | 30 days, and never while a delivery or execution still references one | `W360_RETENTION_EVENT_DAYS` |

## 6. Runbook

**Deliveries are late (`integration_due_work_lag_seconds` rising).**
1. Is beat running? `dispatch_due` should log every 5 s.
2. Are heavy workers alive and not saturated? Check `egress_request_duration_seconds`.
   One slow customer endpoint occupies at most 4 workers, one organization
   at most `WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION` (8); scale heavy workers
   if many organizations are slow at once. A delivery refused a slot is
   *parked* for `WEBHOOK_THROTTLE_HOLD_S`-`2x` (`webhook_deliveries_held_total{reason="concurrency"}`)
   and normally pulled forward sooner by the next finishing attempt.
3. Is the broker reachable? Work is not lost while it is down: the timer
   re-dispatches it on recovery.
4. `integration_due_work_stuck_total` rising → find the timer by
   `kind`/`target_id` in the logs; the row backs off to hourly on its own.

**A customer reports missing webhooks.**
1. In Settings → Webhook deliveries (or the API), filter by status.
2. Still `pending`/`retry` with a later next attempt: the work is being
   *held*, without spending attempts. The connection is suspended, its
   circuit is open (shown on the connection), or the organization is
   suspended; `webhook_deliveries_held_total{reason}` says which.
3. `dead`: the reason and the last category are shown (`http_4xx`,
   `redirect_refused`, `destination_blocked`, `retries_exhausted`, …). After fixing the
   receiver, **replay** (requires `platform.webhook.replay`); replays keep
   the original `event.id`, so the receiver deduplicates.
4. A connection auto-suspended after sustained failure is reactivated by
   an administrator once the receiver is fixed; its circuit resets.

**A signing secret leaked.** Rotate it (Settings → Integrations → Rotate).
Then **end the overlap now** (`DELETE …/secret`) instead of waiting 24 h,
so the leaked value stops verifying at once.

**The integration master key must rotate** (`W360_INTEGRATION_SECRETS_KEYS`).
1. Prepend the new key; `MultiFernet` then seals with the new key and opens
   with every configured key.
2. Rotate each connection's secret, or leave old secrets to open under the
   old key.
3. Remove the old key only once no sealed secret needs it.

Losing every key makes existing signing secrets unrecoverable. Deliveries
are then dead `unsendable` (logged `webhook.delivery.secret_unavailable`),
and each connection needs a rotation.

**Emergency stop for one customer's integrations.** Suspend the connection
(audited). Its deliveries are held, not dropped. To stop everything for an
organization, suspending the organization holds all its integration work.

## 7. Upgrade and rollback

Rehearsed on a copy of the Phase 5 database; the results are in
[phase-6-status.md](phase-6-status.md) §4.

- **Upgrade:** `manage.py migrate` (as the migrator, before new code
  serves). It is additive:
  - 9 new tenant tables with RLS;
  - 1 cross-tenant timer table;
  - 5 permissions, granted to `owner` and `admin`.

  No Phase 5 row changes. Cached permission sets from before the deploy are
  not read, because the cache key carries the role-catalog fingerprint.
- **Rollback of code only:** safe. Phase 5 code ignores the new tables, and
  the extra role grants name permissions Phase 5 does not declare, which
  the registry carries through harmlessly.
- **Rollback of schema:**
  ```
  migrate tenancy 0004
  migrate automation zero
  migrate webhooks zero
  migrate connectors zero
  migrate scheduling zero
  migrate events 0001
  migrate authz 0003
  ```
  This **discards all Phase 6 data**: connections, secrets, deliveries,
  rules. Audit rows are kept (append-only).
