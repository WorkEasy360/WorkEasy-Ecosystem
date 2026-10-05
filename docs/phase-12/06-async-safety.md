# Queues, Celery safety and scheduled-job singleton safety (Phase 12, Steps 28–30)

## 1. Queue isolation (Step 28): actual routing

There is no `task_routes`: every task declares its queue in its decorator (W360.E007 refuses a task without one), and Beat entries set the queue explicitly. Each queue has its own worker pool (`deploy/compose/production.yml`), so no queue's backlog can occupy another's workers.

| Queue | Tasks | Why here |
|---|---|---|
| **critical** | `integration.events.relay_outbox`, `platform.notifications.deliver` (Phase 12), `commercial.provisioning.reconcile_organization` | Sign-in path emails: password reset, verification, invitations, owner activation. Each is bounded by the 10 s SMTP timeout and a 20/30 s soft/hard limit. |
| **default** | `commercial.subscriptions.process_due`, `commercial.payments.{process_due, create_checkout, send_refund}`, `platform.notifications.sweep_pending`, `integration.events.release_stale_claims`, `integration.scheduling.dispatch_due`, `integration.automation.*`, `integration.routing.route_event` | Customer-facing commercial work and the due-work timer. |
| **heavy** | `integration.webhooks.deliver`, `intelligence.*` (AI gateway, RAG indexing/purge, analytics projection, prune), `commercial.provisioning.reconcile_all`, `commercial.commissions.{sweep, prune_referrals}`, `commercial.partners.recheck_domains` (Phase 12: decorator aligned with Beat), `integration.health.check_all` (Phase 12: moved from default), `platform.ecosystem.prune_authorization_codes`, `integration.{events.prune_published, routing.prune}`, `platform.administration.prune_activity` | AI, integration, reports, sweeps and anything that talks to a third party for long. |

**Starvation, proved.**
- `tests/architecture/test_phase6_architecture.py` pins that no integration task other than the relay is on `critical`.
- Phase 12 moved sign-in email onto `critical` and the two long third-party calls (product health, domain DNS) onto `heavy`.
- In the staging rehearsal a verification email was delivered through the `critical` worker within seconds while product-health probes against 20-second endpoints ran on `heavy` (`resilience.py`, "products" scenario).

## 2. Celery safety (Step 29)

| Property | Setting | Effect |
|---|---|---|
| Acknowledge after completion | `CELERY_TASK_ACKS_LATE = True` | A worker that dies mid-task does not lose it. |
| Redeliver on worker loss | `CELERY_TASK_REJECT_ON_WORKER_LOST = True` | The broker requeues it. |
| No hoarding | `CELERY_WORKER_PREFETCH_MULTIPLIER = 1`, `-O fair` | A long task holds one message, not a batch. |
| Bounded time | per-task `soft_time_limit`/`time_limit` (W360.E007), 60/120 s defaults | A stuck task is killed. Phase 12: sweeps now **re-raise** `SoftTimeLimitExceeded` instead of swallowing it and running into the hard kill (G-32). |
| Visibility | `visibility_timeout = 3600 s` (Phase 12; was 900 s, below the 1,200 s longest limit) | Honoured by SQS/Redis transports. RabbitMQ's own `consumer_timeout` (30 min) is also above 1,200 s. |
| Retries | durable due-work timers, not Celery retries (`max_retries=0` on pipelines) | Retry state lives in PostgreSQL. A lost enqueue delays work, never loses it. |
| Idempotency | per task: conditional UPDATE, `SELECT … FOR UPDATE SKIP LOCKED` leases, unique keys, advisory locks (§3) | A redelivered task is a no-op or a resumed lease. |
| Serialization | JSON only | No pickle. |

**Worker crash, proved.** In the staging rehearsal RabbitMQ was stopped with a checkout already started. The payment attempt sat in PostgreSQL as due work. After the broker returned, the sweeper re-dispatched it, the checkout was created, paid and fulfilled, and there was exactly one order.

**Known limit (documented).** `reject_on_worker_lost` plus a task that deterministically kills its worker (for example out-of-memory) redelivers forever. The heavy pool sets `--max-memory-per-child` to recycle leaking workers, and the `W360WorkerPoolDown` and queue-lag alerts catch a crash loop.

## 3. Scheduled jobs are safe to run twice (Step 30)

One Beat is deployed (`deploy.replicas: 1`). A second Beat during a deploy overlap, or a redelivered message, must still not double-apply anything.

| Job | Interval / queue | Duplicate-safety mechanism |
|---|---|---|
| outbox relay | 5 s / critical | `FOR UPDATE SKIP LOCKED` claim per row. |
| commercial clock (renewals, trial expiry, cancellations, **product-trial settlement**) | 300 s / default | Row locks plus state guards (each transition checks the current state); product trials locked (`system.py`). A second run finds nothing due. |
| payments clock (invoices, expiry, dunning, order expiry) | 300 s / default | `uq_invoice_subscription_period` unique constraint; attempts claimed with SKIP LOCKED; orders locked. Dunning stage recorded on the invoice, so each stage applies once. |
| **commissions sweep** | 1 h / heavy | Ledger `idempotency_key` unique **and** per-organization advisory lock. |
| entitlement reconcile-all | 1 h / heavy | Per-organization advisory transaction lock; the computation is idempotent. |
| **notification sweep (email sends)** | 300 s / default | **Phase 12 fix (G-30, reworked by G-50 after the final audit).** Three steps. (1) A short transaction claims the row `FOR UPDATE SKIP LOCKED`, marks it SENDING, counts the attempt and sets a 120 s lease, then commits. (2) The provider call runs **outside any transaction**. (3) A second short transaction records the outcome. A concurrent copy sees the live claim and sends nothing, and the sweeper re-dispatches a claim whose lease ran out. The first fix held the lock and an open transaction across the SMTP call: a send slower than `idle_in_transaction_session_timeout` rolled back its own outcome and attempt count, and was then re-sent without end. Delivery is at-least-once only across a worker dying between the send and step 3, bounded by the attempt count. Pinned by `test_12_notification_claim.py` and `test_12_notification_lease.py`. |
| integration due-work dispatch (**webhook retries**, automation, AI, RAG, payment checkouts) | 5 s / default | SKIP LOCKED claim plus a doubling lease; each consumer holds its own lease. |
| product health | 300 s / heavy | Overwrites state (last write wins). |
| domain re-check | 6 h / heavy | `select_for_update` per domain. |
| prunes (outbox, auth codes + **pending authorizations** (Phase 12), integration, intelligence, referrals, activity) | daily/hourly / heavy | Idempotent deletes. |

**Residual (documented):**
- `release_stale_claims` keys on `created_at`, not claim time (G-40). A slow relay's rows may be re-released. The relay is idempotent and the outbox currently has no subscriber, so there is no effect today; it becomes relevant with the first real consumer.
- Dunning's audit row may be written twice by overlapping runs (the state change itself is single).
