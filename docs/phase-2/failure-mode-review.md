# Phase 2 Failure-Mode Review

**Date:** 2026-09-21
**Method:** Inject the failure, assert the state. Fourteen tests in
`tests/integration/test_commercial_failure_modes.py`, all transactional.

---

## 1. The governing rule

> **Commercial state must never be half-applied.**

Either the subscription changed, the event was written and the audit entry
exists, or none of them did. There is no intermediate state in which an event
describes work that never happened, or work happened with nobody told.

## 2. Transaction boundaries

Every command is one transaction containing:

```
idempotency claim  →  state change  →  outbox publish  →  audit entry
```

and **nothing else**. Provisioning runs after it, outside.

| Rule | How it is held | Test |
|---|---|---|
| No external I/O inside a transaction | No HTTP client is imported anywhere in `commercial/` | `test_no_external_call_happens_inside_a_commercial_transaction` (AST) |
| Events published in the business transaction | `events.publish` refuses to run outside one (Phase 1) | `test_events_are_written_in_the_business_transaction` |
| Never published before commit | It is an INSERT; it commits with the change or not at all | same |
| Never published after commit | No `on_commit` anywhere in the publish path | code + AST scan |
| Transactions are short | No loops over external calls, no queue waits | review |

## 3. Failure injection results

| Failure injected | Expected | Observed |
|---|---|---|
| Price resolution raises mid-command | No item, no subscription, no event | ✅ all zero |
| Same, then retry | The retry genuinely retries | ✅ key not burned, second attempt succeeds |
| Audit write fails | **The operation fails** | ✅ `AuditWriteFailed`, no state |
| Bundle expansion fails on the second component | Nothing at all | ✅ 0 items, 0 subscriptions, 0 events |
| Provisioning listener raises | Commercial state stands, access is stale | ✅ item exists, access denied until reconcile |
| Explicit rollback around a command | Everything discarded | ✅ items, events and idempotency records all zero |
| Relay replayed twice | Inert | ✅ no state change |
| Reconcile run four times | Converges | ✅ identical snapshot |
| Reconcile with no tenant variable | Correct state | ✅ **after the P0 fix** |
| Scheduled pass with no tenant variable | Finds work | ✅ **after the P0 fix** |
| Commercial clock with no tenant variable | Finds work | ✅ **after the P0 fix** |
| One organization corrupt during `reconcile_all` | Others still processed | ✅ 1 failed, rest visited |
| Entitlement tables deleted entirely | One pass rebuilds them | ✅ full access restored |
| Scheduler double-fires | Applies once | ✅ trial expired exactly once |

## 4. The two asymmetries, stated explicitly

### An audit failure fails the operation

The single exception to "telemetry never breaks the request", inherited from
Phase 1. An unaudited change to what a customer is paying for is worse than a
failed one.

### A provisioning failure does **not** fail the operation

The opposite call, and the reasoning is the mirror image. The customer has
paid; the commercial record is the durable fact. Entitlements are **derived
and rebuildable**, so the right behaviour is to keep the commercial fact and
let reconciliation repair access.

Rolling back the purchase because a *derived* write failed would lose the
money and the record. The failure is counted
(`commercial_provisioning_failures_total`) and logged, and the hourly pass
repairs it.

`test_a_provisioning_failure_does_not_roll_back_the_purchase` asserts exactly
this sequence: purchase succeeds, access is denied, reconcile restores it,
commercial state never moved.

## 5. Degradation

| Dependency down | Commercial behaviour | Why |
|---|---|---|
| **Redis** | Unaffected. Slower entitlement reads, which fall through to PostgreSQL | Phase 1 call-site tolerance |
| **Broker** | Commands succeed. Events accumulate in the outbox | The outbox is a table; the relay is what needs the broker |
| **Celery workers** | Commands succeed. Entitlements are provisioned by the immediate hook; time-based transitions are **delayed** | The hook is in-process |
| **Celery Beat** | Commands succeed and provision immediately. Trials do not expire, periods do not roll, drift is not repaired | See §7 |
| **PostgreSQL** | Everything fails | Nothing works without the database; no special case needed |

**The direction of every degradation is safe.** A customer keeps what they
had. Nothing in the list causes a revocation, because revocation requires a
positive durable decision — a date that has arrived, read by a computation
that only runs when the database is available.

## 6. Retry and redelivery

| Aspect | Value |
|---|---|
| Command idempotency | `IdempotencyRecord`, unique `(organization, scope, key)` |
| Replay returns | The original result, not "already done" |
| Key reuse with a different payload | Refused (`IdempotencyConflict`) |
| Claim rolls back with the work | Yes — a failed command does not burn its key |
| Event redelivery | `ProcessedEvent` (Phase 1) |
| Reconciliation redelivery | Harmless by construction — it recomputes |
| Task retries | Phase 1 policy: 5 attempts, exponential with full jitter, capped, dead-lettered |

Both new tasks are idempotent by construction rather than by a dedupe marker:
`reconcile_all` recomputes, and `process_due_subscriptions` is guarded by the
state machine and by the timestamps that caused each transition. Running
either twice in the same window applies nothing.

## 7. Celery Beat — the trigger is now reached

Phase 1 carried "Celery Beat not configured" as a P2, on the grounds that the
relay and sweeper tasks existed but nothing depended on them running.

**Phase 2 changes that.** Two things now have no other trigger:

- The **commercial clock**. Trials ending, cancellations taking effect and
  periods rolling happen because time passed. There is no request and no
  actor. Without a scheduler they never happen, and a cancelled customer
  keeps access indefinitely.
- **Drift repair**. The reconciliation pass is the recovery mechanism the
  entire provisioning design rests on — including the disaster-recovery
  property that entitlements can be rebuilt from subscriptions.

So `CELERY_BEAT_SCHEDULE` is now configured:

| Task | Interval | Queue |
|---|---|---|
| `integration.events.relay_outbox` | 5 s | critical |
| `integration.events.release_stale_claims` | 5 min | default |
| `commercial.subscriptions.process_due` | 5 min | default |
| `commercial.provisioning.reconcile_all` | hourly | heavy |
| `integration.events.prune_published` | daily | heavy |

Intervals are deliberately unambitious. Every task is idempotent, so running
one late is a delay and running one twice is a no-op; the cost of a tight
schedule is constant database load for no correctness gain. Each entry
carries an `expires` below its interval, so a backed-up queue discards stale
scheduled work rather than running it all at once.

## 8. Findings

| Severity | Finding | Status |
|---|---|---|
| **P0** | Cross-tenant reads returned empty, revoking all entitlements | **Fixed**, regression test asserts the outcome |
| **P0** | Concurrent first purchases created two subscriptions | **Fixed**, DB constraint + re-read |
| **P1** | Celery Beat unconfigured, so no time-based transition would ever fire | **Fixed**, schedule added |
| **P2** | No chaos or dependency-outage testing in staging | Phase 1 P2, unchanged. No staging environment exists; the matrix above is verified by unit-level fault injection, which proves the code paths but not the operational behaviour |
| **P2** | The over-limit deadline is not swept | Deliberate — see licensing.md §6 |
| **P2** | No dead-letter consumer for commercial events | Nothing consumes them yet; the DLQ depth alarm from Phase 1 applies unchanged |

## 9. What Phase 2 does not protect against

| Not protected | Why acceptable now | Trigger |
|---|---|---|
| A payment provider outage | There is no payment provider | Phase 3 |
| A poisoned commercial event blocking consumers | No consumer exists | The first consumer |
| Clock skew between app servers | **Not** what this row originally claimed. `django.utils.timezone.now()` is the *application* server's clock, not PostgreSQL's, so skew between web and worker hosts is real. It is tolerable because every clock-driven transition is guarded by a durable timestamp and a state machine, so a skewed host applies a transition early or late rather than wrongly, and a later pass converges. NTP on every host is the control | Multi-region, or any transition whose correctness depends on ordering within seconds |
| Sustained reconciliation failure for one organization | Counted and logged; no alert threshold is configured because there is no alerting backend | Prometheus integration |
