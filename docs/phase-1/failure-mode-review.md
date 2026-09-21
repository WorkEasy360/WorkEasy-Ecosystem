# Phase 1 Failure-Mode Review

**Date:** 2026-09-21
**Scope:** The degradation matrix from
[09-failure-resilience.md](../architecture/09-failure-resilience.md) §5, for
the dependencies Phase 1 actually has.

The governing rule being checked: **a Tier-N component may never make a
Tier-(N−1) or lower operation fail.**

---

## 1. Degradation matrix — Phase 1 rows

| Component down | Tier | Login | Read | Write | Notifications | Events | Verified by |
|---|---|---|---|---|---|---|---|
| **Redis** | 1 | slower | slower | slower | ok | ok | `test_check_is_correct_when_cache_is_unavailable`, `test_authorization_never_fails_open_when_cache_is_down`, `test_readiness_does_not_fail_on_a_cache_outage` |
| **Broker / workers** | 2 | ok | ok | **ok** (queued) | delayed | queued in outbox | `test_broker_outage_leaves_delivery_recoverable`, `test_rollback_discards_the_event` |
| **Email provider** | 3 | ok | ok | ok | **in-app only** | ok | `test_a_provider_outage_does_not_fail_the_caller`, `test_in_app_channel_works_when_email_is_down` |
| **One relay worker crashes** | 2 | ok | ok | ok | ok | recovered | `test_worker_crash_leaves_rows_recoverable` |
| **PostgreSQL primary** | 0 | **fail** | **fail** | **fail** | fail | fail | Honest 503; no degradation claimed |

Only the last row is a real outage, which is the intended shape.

## 2. Per-dependency behaviour

### Redis unavailable (Tier 1)

| Use | Behaviour | Correctness |
|---|---|---|
| Entitlement cache | Reads `EntitlementSnapshot` from PostgreSQL | Correct, ~1.1 ms slower per request |
| Permission cache | Recomputes from the database per request | Correct, slower |
| Readiness | Cache is **probed and reported** but does **not** fail readiness | Instance stays in the load balancer |

Every cache read and write on a correctness path is wrapped at the call site.
This is not belt-and-braces: Django's built-in `RedisCache` has **no**
`ignore_exceptions` option — that belongs to the third-party `django-redis`
package, and passing it fails at connection construction. Phase 1 found that
the hard way and moved the tolerance to where it belongs.

The actual cascading-failure control is the timeout: **50 ms connect / 100 ms
operation**. A 5-second Redis timeout on every request exhausts the worker
pool in seconds; 100 ms does not. *The timeout, not the outage, is what causes
cascading failure.*

**Authorization never fails open.** Asserted directly: with `cache.get` and
`cache.set` both raising, a granted permission is still granted and a
non-granted one is still denied.

### Broker or workers unavailable (Tier 2)

The outbox makes this degrade by construction:

- **Business writes still commit.** Events accumulate as rows. Nothing lost.
- **User actions succeed.** The event is durable the moment the transaction
  commits, regardless of broker state.
- **Notification queuing tolerates the outage.** `_enqueue` catches the
  dispatch failure and logs it; the delivery row stays `pending` and the
  sweeper re-dispatches. Failing a user's action because a Tier-3
  notification could not be *queued* would invert the tiers entirely.

Outbox lag is exposed as `pending_age_seconds` on `/healthz/deep`. **Oldest
pending age, not depth** — a large draining backlog is healthy, a small stuck
one is not.

### Email provider unavailable (Tier 3)

- **Nothing is sent inline, ever.** `notify()` persists intent, queues
  delivery and returns. A provider outage cannot become request latency.
- Bounded retries: 5 attempts, exponential with full jitter, capped at 1 hour,
  then dead-lettered with the failure recorded.
- **Transient versus permanent is decided by exception type**, never by
  string-matching an error message. `SMTPRecipientsRefused` and a 5xx SMTP
  code are permanent and consume no retry budget; a timeout or 4xx is
  transient. Retrying "mailbox does not exist" five times is wasted work and
  alert noise.
- In-app delivery succeeds independently, so the customer is still informed.

### Worker crash mid-task (Tier 2)

A relay worker killed between claiming and publishing leaves rows in
`publishing` forever. `release_stale_claims` returns them to `pending` after
a timeout, so a crash is a delay rather than permanent event loss. Covered by
`test_worker_crash_leaves_rows_recoverable`.

`acks_late=True` with `reject_on_worker_lost` means a crashed task re-runs
rather than vanishing — safe only because consumers are idempotent, which the
`ProcessedEvent` constraint enforces at the database.

## 3. Transaction rules (09 §7)

| Rule | Status | Enforcement |
|---|---|---|
| No external I/O inside a database transaction | **Held** | `publish()` is an INSERT. The relay publishes **outside** the claim transaction. No HTTP client exists in any transactional path. |
| Transactions are short | **Held** | Request-scoped, with `idle_in_transaction_session_timeout=15s` as a backstop |
| `on_commit` not used for anything that must not be lost | **Held, with one reviewed use** | See below |
| Never hold a transaction across a user interaction or queue wait | **Held** | No such path exists |

**The one `on_commit`.** `notify()` uses it to dispatch delivery tasks after
commit. Deliberate and safe *specifically because the `Notification` and
`NotificationDelivery` rows are already durable*: a lost callback delays a
send, and the sweeper recovers it. Contrast the outbox, where `on_commit`
would be wrong — there would be no durable record to recover from. Both the
use and the contrast are commented at the call site.

## 4. Retry policy

| Aspect | Value | Verified |
|---|---|---|
| Max attempts | 5 (outbox, notifications, tasks) | `test_retries_are_bounded_then_dead_lettered` |
| Backoff | Exponential with **full jitter**, `random(0, min(cap, base·2ⁿ))` | 200 samples at attempt 3 → **200 distinct values** in [0.42 s, 15.90 s] |
| Cap | 3 600 s | Asserted |
| Nesting | **None** | `deliver_notification` sets `max_retries=0`; the service owns backoff. Two retry policies would multiply attempts. |
| Infinite retries | **None anywhere** | Every path dead-letters |

Full jitter rather than plain exponential: without it, every client that
failed together retries together, producing a synchronised herd that re-kills
the recovering dependency.

## 5. Health checks

| Endpoint | Checks | Verified |
|---|---|---|
| `/healthz/live` | **Nothing.** Process responsive. | `test_liveness_survives_a_database_outage`, `test_liveness_issues_no_queries` |
| `/healthz/ready` | PostgreSQL + migrations (blocking); cache reported but **non-blocking** | `test_readiness_does_not_fail_on_a_cache_outage` |
| `/healthz/startup` | The above plus registries and the **RLS role assertion** | `test_startup_verifies_the_rls_role` |
| `/healthz/deep` | Per-dependency detail, staff-only, **never influences orchestration** | `test_deep_health_requires_staff` |

**Liveness checking a dependency is the classic self-inflicted outage**: a
brief database blip restarts every container simultaneously, turning a
30-second degradation into a full outage with cold caches. It is asserted
directly, with `connection.cursor` patched to raise.

Readiness is cached for 5 s so a health-check storm cannot itself become
database load.

**External providers never appear in readiness.** Email, AI and payment are
Tier 3; an outage there must not remove an instance from the load balancer,
because the core platform is perfectly able to serve requests.

## 6. Findings

| Severity | Finding | Status |
|---|---|---|
| **P0** | `ignore_exceptions` is not a valid option for Django's `RedisCache`, so the documented Redis degradation was not actually configured | **Fixed.** Removed, with the reason recorded; tolerance is at the call sites and is now tested. |
| **P1** | No circuit breakers | **Carried.** 09 §4.4 applies them to external dependencies, and Phase 1 has one (SMTP) with no production traffic. Required before connectors or payment. **Owner:** Platform · **Due:** with the first external provider. |
| **P1** | No backpressure or load shedding | **Carried.** Needs queue-age metrics and an edge tier. **Due:** before first production customer. |
| **P2** | No chaos or dependency-outage testing in staging | No staging environment exists. The matrix is verified by unit-level fault injection, which proves the code paths but not the operational behaviour. |
| **P2** | Celery Beat not configured | The sweeper and relay tasks exist and are routed; scheduling is a deployment concern with no scheduler running yet. |
| **P2** | `prune_published` has no partition strategy | Chunked deletion is adequate to the trigger (500 M rows) documented in 13. |

## 7. Conclusion

Every Phase 1 row of the degradation matrix behaves as documented, and each is
asserted by a test that injects the failure rather than assuming it.

The one P0 is worth restating because it is the shape of failure this review
exists to catch: the *documentation* said Redis failures degraded gracefully,
the *settings* contained an option that made that true in a different library,
and nothing would have noticed until Redis actually went down. It was found by
writing the test.
