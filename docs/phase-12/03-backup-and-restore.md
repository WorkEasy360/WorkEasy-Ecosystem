# Backup, restore, point-in-time recovery, RPO and RTO (Phase 12, Steps 18–20)

**A backup that has never been restored is not a backup.** Every claim here comes from a backup that was taken from the production-shaped staging stack and restored into an **isolated** environment. The size figures describe a small rehearsal database; they measure the mechanism, not production-scale timings.

## 1. What is backed up

| Data | Mechanism | Tool |
|---|---|---|
| PostgreSQL (all platform data) | Logical dump, custom format, compressed | `deploy/scripts/backup.sh` → `db.dump` |
| PostgreSQL, point in time | Continuous WAL archiving (`archive_mode=on`, `archive_timeout=300`) plus base backups, with retention | compose `archive_command`; `deploy/scripts/wal_retention.sh` (base backup, keep N, `pg_archivecleanup`); `deploy/scripts/pitr_verify.sh` |
| Roles (without passwords) | `pg_dumpall --globals-only --no-role-passwords` | `globals.sql` |
| Uploaded files (partner logos) | the object store: a volume archive on a single host; on AWS, S3 versioning + cross-region replication + a lifecycle rule | `files.tar.gz` |
| Critical configuration | version-controlled (`deploy/`), plus a per-backup manifest: release tag, **image digests**, **SHA-256 of every deploy config file**, env variable **names** (never values) | `manifest.json` |
| Secrets | the secret store's own versioning and backup (never in a backup file) | — |
| Integrity | `SHA256SUMS` over every artefact, checked before any restore | `SHA256SUMS` |

Redis (cache only, no persistence) and RabbitMQ (transient, since work is re-derived from PostgreSQL by the sweepers) are deliberately not backed up. Restoring the database is sufficient for them.

## 2. Verified results (staging, 2026-09-30)

### Logical backup and isolated restore (`backup.sh` + `restore_verify.sh`)

| Step | Result |
|---|---|
| Backup (database + files + manifest + checksums) | **6–10 s**; 722 KB dump; 56 stored objects |
| Checksums before restore | PASS |
| Restore target | a new PostgreSQL in its own network, **new** role passwords (`roles.sh`), never the live cluster |
| `pg_restore --single-transaction --exit-on-error` | PASS in **3 s** |
| Row counts vs backup-time counts | identical for all 22 authoritative tables: users, organizations, memberships, subscriptions, product trials, entitlements and snapshots, entitlement grants, seats, catalog products/plans/versions, orders, invoices, partners, product grants, domains, partner–customer links, commission accruals, payouts, audit, product integrations |
| The application image against the restored database | `check --deploy`: RLS enabled and FORCE on every tenant table, runtime role cannot bypass RLS and owns no table, timeouts present. Only the known W360.W010 product-host warnings. **0 unapplied migrations** |
| Uploaded files | restored into a new volume (56 files) |
| Total restore + verification | **18 s** |
| Secrets in the backup | none: the manifest lists variable *names*; `globals.sql` has no passwords (checked) |

The final repeat on the fuller dataset (after the browser and load runs added partners, commissions and hundreds of organizations) is recorded in phase-12-status items 21–22.

**The first version of `restore_verify.sh` could not fail its application check (G-52, final audit).** It grepped a truncated tail of `check --deploy`, and a failing `showmigrations` counted as zero unapplied. It now judges the application's startup gate, `check --deploy` and `migrate --check` by exit status, and reads the runtime role's timeout defaults from the catalog. The final run passed on that basis.

### Point-in-time recovery (`pitr_verify.sh`)

| Step | Result |
|---|---|
| Base backup (`pg_basebackup -Ft -z`) | 2–3 s |
| Marker written, target instant T recorded, second marker written after T, WAL switched | — |
| New isolated cluster: base backup + archive replayed to T | **3–4 s** |
| The pre-T marker is present and the post-T marker is absent | **PASS** |

**Two real defects found while proving this (both fixed):**
1. **G-44: WAL archiving was failing on every segment** (the archive volume was root-owned; PostgreSQL archives as `postgres`). Point-in-time recovery was impossible and `pg_wal` would have grown until the disk filled. Fixed in the topology, and now **alerted on**: `W360WalArchivingFailing` and `W360WalArchiveStale` from new `pg_stat_archiver` gauges.
2. A recovery target must run with at least the primary's `max_connections`, `max_locks_per_transaction` and `max_worker_processes`, or PostgreSQL refuses to replay. The script reads them from the primary, and the runbook states it.

## 3. RPO and RTO: recommendation

Honest numbers for what is built, not for an architecture that does not exist yet:

| Deployment | RPO (data loss) | RTO (time to restore service) | Conditions |
|---|---|---|---|
| **As built today: scripts, no schedule** | **unbounded**: nothing runs `backup.sh` or `wal_retention.sh` unless an operator does, and nothing leaves the host (P1, OPS-2) | not guaranteed | none: the scripts exist and were proven on staging |
| **Single host with the jobs scheduled, WAL archive on the same disk** | **up to 24 h** for loss of the host (only the nightly off-host logical backup survives); **≤ 5 min** for database corruption or operator error while the host survives (PITR from the local archive) | **≤ 2 h**: provision a host, deploy the images, `restore_verify.sh`, the startup gate | nightly `backup.sh` copied off-host; nightly `wal_retention.sh`; monthly restore and PITR tests ([04-deployment.md §7](04-deployment.md)) |
| **Single host with the WAL archive shipped off-host** (pgBackRest or WAL-G to S3) | **≤ 5 min** (`archive_timeout`) | **≤ 2 h** | archive shipping monitored (the `W360Wal*` alerts) |
| **AWS target** (RDS Multi-AZ with automated backups/PITR, S3 versioning + CRR, per `13-deployment-evolution.md`) | **≤ 5 min** | **≤ 5 min** for an AZ failure (Multi-AZ failover); **≤ 1 h** for a region (restore from a cross-region snapshot) | not built; recommended for general availability |

**Recommendation for the controlled go-live:** the third row, a single host with the jobs scheduled, WAL shipped off-host and a nightly logical backup. That gives RPO ≤ 5 min and RTO ≤ 2 h, which is proportionate for a controlled launch with a small number of customers. Move to the AWS target before general availability.

The platform does **not** claim zero data loss or high availability: every data-tier service is a single instance in `production.yml`.

**Operational rules:**
- `backup.sh` runs nightly and its artefacts are copied off-host (object storage in another account or region).
- Its exit status posts to a dead-man's switch (the backup-failure alert).
- `restore_verify.sh` runs **monthly** against the latest backup, and after every release that adds a migration.
- `pitr_verify.sh` runs monthly.
- Backups are retained for 35 days, plus monthly archives kept for 1 year (`13-deployment-evolution.md` §6).

## 4. WAL retention (final audit, G-59)

**The problem.** Without a job pruning it, the WAL archive only grows. On staging it reached 1.2 GB in one day (`archive_timeout=300` switches a segment every five minutes with any write in the window), and no base backup was ever taken on a schedule to replay it onto. On the single host the disk fills and PostgreSQL stops.

**The fix.** `deploy/scripts/wal_retention.sh` runs nightly (04-deployment.md §7) and:
1. takes a base backup into `/wal-archive/base/<timestamp>` (tar + gzip, WAL from the archive);
2. keeps the newest `KEEP` (default 7);
3. removes only WAL older than the oldest base backup kept (`pg_archivecleanup`), so recovery to any point since then stays possible.

**Verified on staging:**
- The archive went from 1.2 GB (82 segments) to 73 MB (7 segments and one base backup), in 5 s.
- `pitr_verify.sh` still passed afterwards.

**Still needed:** a copy off the host. Ship `/wal-archive` to object storage, or replace the script with pgBackRest or WAL-G. A copy on the same disk does not survive losing the host.
