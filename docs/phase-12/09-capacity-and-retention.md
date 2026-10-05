# Capacity, retention and tenant-data inventory (Phase 12, Steps 53, 56, 57)

## 1. Disk growth and retention (Step 57)

| Source | Growth driver | Retention (as built) | Control |
|---|---|---|---|
| **Container logs** | every request and task | bounded: 5 × 50 MB per container (Phase 12, `x-logging` in `production.yml`). The log pipeline holds history (recommend 30 days hot, 1 year archive for security events) | Before Phase 12, Docker's default was **unbounded**. |
| **PostgreSQL data** | tenants, commerce, audit | see the table rows below | `W360DiskFillingUp` |
| **WAL archive** | every write (1.2 GB in one day of staging) | keep the WAL needed back to the oldest retained base backup, then delete: `deploy/scripts/wal_retention.sh` nightly, `KEEP` base backups (final audit, G-59). **Not scheduled yet (P1, OPS-2).** | `W360WalArchiving*` (Phase 12); disk: `W360DiskFillingUp` needs node_exporter |
| **Backups** | nightly | 35 days, plus monthly for 1 year, off-host | the backup-failure alert |
| **Uploaded files** (logos) | partners | while referenced; removed on replace or scan failure | S3 lifecycle for noncurrent versions (30 days) |
| Outbox (published) | every domain event | 30 days (`OUTBOX_RETENTION_DAYS`) | daily prune |
| Integration deliveries and executions | webhooks, automation | succeeded 7 days, dead 30 days, executions and events 30 days | daily prune |
| AI executions / results | AI calls | metadata 400 days; results 15 min (sync) / 24 h (jobs) | daily prune |
| RAG | documents indexed by products | until deleted; tombstones 30 days | daily prune |
| Analytics | product metrics | facts 400 days; hourly 90 days; daily 1,830 days | daily prune |
| Authorization codes / **pending authorizations** | every product sign-in / anonymous `/authorize` | 1 hour after expiry. The pending-authorization prune was never scheduled until Phase 12 (G-33). | hourly prune |
| Account events / member sign-ins | sign-ins | 400 days | prune |
| **Audit events** | every privileged action | 7 years (policy), **no partitioning yet** | the largest long-term table; partition monthly before it passes about 50 M rows (`13-deployment-evolution.md` §3 step 7) |
| **Not pruned today** | | notifications and deliveries, payment webhook events, dead letters and dead-lettered outbox rows, processed-event markers, idempotency records, invitations, organization-creation requests | P3: retention defined here (notifications 180 days after final state; webhook events 400 days; idempotency records 7 days after expiry; the rest 400 days); prune jobs to be added. The volumes involved are small: one row per email, payment or request. |
| Redis | cache and limits | TTL-bound, `maxmemory 256mb`, `allkeys-lru`, no persistence | — |
| RabbitMQ | queues | transient; durable queues drain | `W360QueueLag` |
| Celery artefacts | none | no result backend (`CELERY_RESULT_BACKEND` empty), Beat schedule in `/tmp` | — |

## 2. Memory (Step 56)

Reviewed in code, then measured under load (below):
- **Fixed: an unbounded metric list** (G-34). Every HTTP request and every entitlement check appended to an in-process list forever. Now it is a 1,024-sample window with exact totals.
- **Bounded by design:**
  - Per-process rate-limit fallback: 10,000 keys, least-recently-hit evicted (G-13).
  - Cache breaker state: one entry per Redis location.
  - The S3 client: `lru_cache(maxsize=1)`.
  - Throttle windows.
  - Gunicorn recycles workers every 5,000 ± 500 requests; heavy workers recycle at 400 MB RSS (`--max-memory-per-child`).
- **No large materialization on a request path:** list endpoints are keyset-paginated. The sweeps enumerate organization *IDs* only (UUIDs), then work per organization.
- **Task payloads:** IDs only (JSON), never objects or documents. RAG and AI payloads are bounded by `AI_MAX_REQUEST_BYTES` and `RAG_MAX_DOCUMENT_CHARS`.

Measured container memory under load: [07-load-test.md](07-load-test.md) and phase-12-status item 24.

## 3. Tenant-data inventory for a future purge (Step 53)

No purge or export exists yet (D-036). Nothing is orphaned today, because no deletion path exists. What a purge must visit:

- **Discoverable by `organization_id`** (RLS-scoped or carrying the column):
  - memberships, roles and assignments
  - subscriptions and items, product trials, entitlements, snapshots, grants, seats
  - orders, invoices and lines, payment attempts and refunds, order summaries
  - billing accounts, notifications and deliveries, preferences
  - integration connections, subscriptions, deliveries, routed events, due work
  - automation rules and executions; AI executions and jobs; RAG sources, documents and chunks; analytics facts and aggregates
  - idempotency records, outbox and dead letters, files, audit events (retained as tombstones per `08-data-ownership.md` §6), partner–organization links, owner invites, referral conversions, commission accruals
- **Not directly discoverable by organization; a purge must go through the parent:**
  - `commissions_payout_line` (via its accrual)
  - `identity_account_token.organization_name` (free text on a verification token)
  - `payments_webhook_event` rows that arrived before an organization could be resolved
  - user soft references with no foreign key: `ProductTrial.started_by_user_id`, `Order.placed_by_user_id`, `PaymentAttempt.created_by_user_id`, `Refund.requested_by_user_id`, audit `actor_user_id`, settings `updated_by_id`
- **Product data** lives in each product's own database; the platform holds none (the architecture test pins that no product-domain code exists here). A tenant purge must emit an event the products consume. Not built: this belongs to D-036.
