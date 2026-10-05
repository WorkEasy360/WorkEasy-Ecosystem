# Incident runbooks (Phase 12, Steps 51–52)

Each runbook covers four steps: **detect → contain → recover → verify**. Behaviour marked *(proven)* was exercised by physically failing the dependency on the production-shaped staging stack (`scripts/phase12_staging/resilience.py`). Commands assume the single-host compose topology; on AWS the managed-service console equivalent applies.

`C` means `docker compose -p w360 -f deploy/compose/production.yml --env-file $W360_ENV_FILE`.

## API or database outage
<a id="api-or-database-outage"></a><a id="database-outage"></a>
- **Detect:**
  - `W360DatabaseUnavailable`, `W360ApiDown` or `W360High5xxRate`.
  - `/healthz/ready` 503 on every instance.
- **Contain:**
  - Nothing to do for data integrity *(proven)*. Requests fail fast (under 5 s with PostgreSQL itself down).
  - Readiness 503 takes instances out of the load balancer.
  - Commercial writes are refused, not half-applied: order count unchanged, no email sent during the outage.
  - Do **not** restart the api in a loop; it cannot help.
- **Recover:**
  - `C ps postgres pgbouncer`, then `C logs --tail 200 postgres`. Common causes: disk full (see Disk capacity below), connection exhaustion (`w360_database_connections`), a stuck migration lock.
  - Restart the failed component: `C start pgbouncer` / `C start postgres`. On RDS, fail over.
  - If data is lost, see Restore in [03-backup-and-restore.md](03-backup-and-restore.md).
- **Verify:**
  - `/healthz/startup` 200 (RLS role, ownership, timeouts) and `/healthz/ready` 200 on every instance.
  - Sign-in and My Products work *(proven: recovery in under 60 s after PgBouncer or PostgreSQL restart)*.
  - `w360_outbox_oldest_pending_seconds` and the due-work lag drain back to baseline.

## Redis outage
<a id="redis-outage"></a>
- **Detect:** `W360CacheUnavailable` (a ticket, not a page) and log event `cache.breaker.opened`.
- **Contain:**
  - None needed for correctness *(proven)*: sign-in, sessions, My Products, catalog, trials and entitlement reads all work from PostgreSQL.
  - The circuit breaker makes cache calls fail fast. Each API process pays one slow request to discover the outage, then serves at normal latency (catalog 38 ms, My Products 78 ms with Redis down).
  - Rate limits degrade to per-process counters. The database account lockout still stops password guessing *(proven)*.
- **Recover:** `C start redis` (ElastiCache: wait for failover). Nothing needs repopulating: caches refill on demand and versioned keys stay correct.
- **Verify:** `w360_cache_up == 1` and the breaker closes (no new `cache.breaker.opened`); latency at baseline.

## RabbitMQ outage
<a id="rabbitmq-outage"></a>
- **Detect:** `W360BrokerUnavailable`; `W360WorkerPoolDown` for every queue.
- **Contain:**
  - None needed for correctness *(proven)*: trials, orders, payment starts and password-reset requests all commit.
  - Deferred work waits in PostgreSQL (DueWork, notification rows, outbox), and no email leaves during the outage.
- **Recover:**
  - `C start rabbitmq` (AmazonMQ: wait or fail over). Workers reconnect by themselves.
  - Beat's sweepers re-dispatch everything that was due: notification sweep every 5 min, due-work every 5 s.
- **Verify:**
  - `w360_queue_consumers ≥ 1` for each queue and the queues drain.
  - *(Proven)* The queued password-reset email arrived after recovery exactly once, and the deferred checkout was created, paid and fulfilled.
  - Check `w360_outbox_dead_letters` did not grow.

## Queue backlog
<a id="queue-backlog"></a>
- **Detect:** `W360CriticalQueueLag`, `W360QueueLag`, `W360OutboxStuck`, `W360DueWorkLag`, `W360ProductHealthStale`.
- **Contain:**
  - Find which queue has the backlog. `critical` holds sign-in email and the outbox relay.
  - Scale that pool (`W360_WORKERS_<QUEUE>` or more replicas). Pools are separate, so a heavy backlog cannot starve `critical`.
- **Recover:**
  - A crash-looping task: `C logs worker-<queue>`. A task killed every time (memory) is redelivered forever; fix it or purge that one message after reviewing it.
  - Dead letters: review, then replay or discard.
- **Verify:** queue depth at baseline, and oldest pending under 30 s for the outbox.

## External product outage
<a id="external-product-outage"></a>
- **Detect:** `W360ProductUnreachable{product_code}` (ticket for the product team), and the dashboard shows "temporarily unavailable".
- **Contain:**
  - Nothing on the platform *(proven for all four products × 5xx, timeout, DNS failure, bad TLS)*. Readiness, sign-in, My Products, catalog and other products are unaffected.
  - The platform makes no request to a product on any customer's path.
- **Recover:** the product team restores their service. Optionally suspend the integration in the Super Admin products console so the launcher stops offering it.
- **Verify:** the next health sweep (5 min) or "Check now" shows healthy.

## OIDC failure
<a id="oidc-failure"></a>
- **Detect:** the log-based OIDC alert (token 5xx, spikes of `invalid_grant` or `invalid_client`); product teams report sign-in failures.
- **Contain:**
  - `/healthz/deep` → signing keys: is an **active** key present, and is its `W360_OIDC_SIGNING_KEY_<KID>` variable set? A missing variable gives a 503 on `/token`.
  - A spike of `invalid_grant` with reason `access_policy_denied` points to a customer's access policy. This is expected and not an incident; G-10 was the bug that turned it into *every* sign-in.
- **Recover:**
  - Missing key material: restore the variable from the secret store and redeploy the api.
  - Suspected key compromise: see Suspected credential leak (below).
- **Verify:** reference-client flow in staging; `/token` 200 rate back to baseline.

## Payment outage
<a id="payment-outage"></a>
- **Detect:** the checkout-failure log alert; the provider's status page; payment webhooks not arriving (`payments_webhook_event` stops growing).
- **Contain:**
  - Checkout attempts expire safely (`PAYMENT_ATTEMPT_TTL_MINUTES`) and grant nothing. Access is granted only on a confirmed payment (pay-first orders).
  - Do not "mark paid" manually except through the audited staff action (AAL2) against a confirmed provider record.
- **Recover:** once the provider is back, webhooks are retried by the provider and are idempotent (duplicate deliveries are no-ops). The payments clock reconciles open attempts.
- **Verify:** orders placed during the outage are fulfilled or expired, never left half-way; invoices match the provider's dashboard.

## Email outage
<a id="email-outage"></a>
- **Detect:** growing `notifications_delivery` rows in `failed`; `notification.failed` log events; the provider's status page.
- **Contain:**
  - Deliveries retry with backoff and dead-letter after `NOTIFICATION_MAX_ATTEMPTS`. Sealed links keep their own expiry.
  - Tell support that verification, reset and invitation emails are delayed.
- **Recover:** restore the SMTP credentials or provider. Re-queue dead-lettered deliveries whose links are still valid; users can resend verification and reset themselves.
- **Verify:** a test password reset to an ops mailbox arrives, and the `failed` count stops growing.

## Backup failure
<a id="backup-failure"></a>
- **Detect:**
  - `W360WalArchivingFailing` or `W360WalArchiveStale` (from `pg_stat_archiver`).
  - The nightly `backup.sh` dead-man's switch missing.
  - `restore_verify.sh` failing on the latest backup.
- **Contain:**
  - While WAL does not archive, point-in-time recovery is broken *and* `pg_wal` grows until the disk fills. Treat it as urgent.
  - `C logs postgres | grep "archive command failed"` shows the reason, for example "Permission denied" on the archive directory. That was the exact failure the Phase 12 PITR rehearsal found and fixed (G-44).
- **Recover:**
  - Fix the cause (archive target writable and reachable, disk space).
  - PostgreSQL retries the backlog by itself.
  - Then take a fresh base backup: every backup taken during the failure is unusable for PITR. `deploy/scripts/wal_retention.sh` takes one, and prunes the archive back to it.
  - The archive filling the disk is the other failure: the nightly `wal_retention.sh` did not run. Run it by hand; it keeps the newest `KEEP` base backups and removes only WAL older than the oldest one kept.
- **Verify:**
  - `archived_count` rises and `failed_count` stops rising.
  - `deploy/scripts/pitr_verify.sh` passes.
  - `restore_verify.sh` passes on a new logical backup.

## Staff member locked out of the control plane
<a id="staff-mfa-lockout"></a>

Since D-037 the Super Admin console needs a verified second factor in every session. A staff member who has lost both the authenticator *and* the recovery codes cannot get in, and nothing inside the application can remove a factor without a verified session.

- **Verify who is asking.** Out of band, by a channel the platform did not supply: a known phone number, in person. This is exactly the request an attacker holding a stolen password would make.
- **Reset (operator, on the server, audited):**
  `python manage.py w360_staff_mfa_reset --email <address> --reason "<ticket and why>"` shows what it would do; add `--confirm` to make the change.
  It removes the account's factors and recovery codes, signs every session out, and **removes the staff designation**. Grants are kept, and inert until the designation returns.
- **Restore access (the ordinary path):**
  1. The person signs in (a normal account now) and enrols a new authenticator under Account > Security.
  2. The operator confirms with them that the enrolment time the next command prints is theirs.
  3. `python manage.py w360_staff_identity --email <address> --enable --reason "<ticket>" --confirm`. It refuses an account without a confirmed factor.
- **The bootstrap Super Admin** (`bootstrap_superadmin`) is created with a password only. The operator signs in and enrols the authenticator immediately at deploy time: until then that account's password is the only thing protecting the control plane.
- **Watch:** `identity.mfa.staff_factor_enrolled` log events (a staff enrolment nobody expected is an incident) and the `staff.mfa_reset` audit entries.

## Suspected credential leak
<a id="suspected-credential-leak"></a>
Follow [05-secrets-and-key-rotation.md §3](05-secrets-and-key-rotation.md): detect, contain (rotate at the issuer first), recover (re-issue dependents, review audit), verify (old credential refused).

## Tenant isolation incident
<a id="tenant-isolation-incident"></a>
- **Detect:**
  - Log alarms `tenant.context.missing`, `tenancy.db_scope.missing` or an `rls_violation`.
  - A customer report of seeing another organization's data.
  - `W360.E002` on `check --deploy`.
- **Contain (immediately):**
  - Treat it as a security incident and preserve logs.
  - If one code path is implicated, disable it: feature flag, or suspend the affected integration or partner.
  - If RLS itself is in doubt, stop serving: take api instances out of the load balancer. A platform that can leak is worse than one that is down.
- **Recover:**
  - Find the path (correlation ID → request → code).
  - Fix and add a regression test to `tests/isolation/`.
  - Run `check --deploy` (RLS role, ownership, FORCE on every tenant table) against production.
- **Verify:**
  - The cross-tenant matrix (`tests/security`, `tests/isolation`) is green.
  - Audit shows the full extent of what was read or written.
  - Affected organizations are notified per the data-protection obligations.

## Elevated 5xx
<a id="elevated-5xx"></a>
Correlate the `http.request` lines with status ≥ 500 by `http_path` and their `correlation_id`. A single path usually means a code defect: roll back per [04-deployment.md §5](04-deployment.md). A broad spike usually means a dependency: see the runbooks above.

## Disk capacity
<a id="disk-capacity"></a>
Growth sources and retention: [09-capacity-and-retention.md](09-capacity-and-retention.md). Free space by pruning WAL archives older than the backup retention, rotating container logs (the Docker `json-file` driver with `max-size`), and moving old base backups to cold storage. Never delete from `pgdata` by hand.

## Certificate expiry
<a id="certificate-expiry"></a>
Caddy renews 30 days before expiry. If `W360CertificateExpiring` fires:
- Check `C logs edge` for ACME errors: rate limits, DNS no longer pointing at the edge, or port 80/443 blocked.
- For a partner domain, check it is still approved (`/api/v1/public/edge/tls-allowed` from inside the edge). A domain disabled for lapsed control *should* lose its certificate.

## Custom-domain failure
<a id="custom-domain-failure"></a>
`W360CustomDomainFailing`: the ownership record is missing on re-check. The platform disables the domain after repeated misses, and the brand goes off the air on that host only. Tell the partner to restore the TXT record; nothing on the canonical host is affected (ADR-039).
