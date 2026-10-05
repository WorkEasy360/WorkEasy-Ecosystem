# Deployment, rollback and the go-live checklist (Phase 12, Steps 46–50)

Everything below is version-controlled in `deploy/` and was rehearsed end to end on a production-shaped stack: `deploy/compose/production.yml` + `deploy/staging/compose.staging.yml`, production settings plus exactly one relaxation, the test payment provider (see [phase-12-status.md](phase-12-status.md)). No step depends on a manual, undocumented change to a server.

## 1. What is deployed

| Piece | Source | Notes |
|---|---|---|
| Backend image | `deploy/docker/backend.Dockerfile` | Python 3.12.14-slim, non-root uid 10001, code read-only, no tests or secrets in the image. One image runs api, workers, Beat and the migration task. |
| Frontend image | `deploy/docker/frontend.Dockerfile` | Node 24 LTS. `next build` runs inside the build, with the dynamic-rendering assertion. Non-root. |
| App server | `deploy/gunicorn.conf.py` | gthread, workers × threads from environment variables; the request log comes from Django, not gunicorn. |
| Topology | `deploy/compose/production.yml` | edge (Caddy TLS) → web (Next) and api (Django). Worker pools for `critical`, `default` and `heavy`, plus one Beat. PgBouncer (transaction pooling) → PostgreSQL 16 + pgvector, Redis (cache only), RabbitMQ (durable). |
| Edge | `deploy/edge/Caddyfile` | HTTPS only, HSTS, on-demand TLS for partner domains gated by the platform (ADR-039), internal paths refused. |
| Database roles | `deploy/postgres/roles.sh` | Owner vs runtime role, no BYPASSRLS, timeouts on the role, passwords from the environment. |
| Environment | `deploy/env/production.env.example` | Variable names only. The real file is rendered from the secret store ([05-secrets-and-key-rotation.md](05-secrets-and-key-rotation.md)). Compose is run with `--env-file <that file>`. Each container receives **only what it reads** (final audit): the edge and web hold no secret; PostgreSQL alone holds the superuser's password; only the migrate task holds the owner's. |
| Startup gate | `manage.py w360_startup_gate` | Runs before gunicorn and before every worker. It exits non-zero, so the container never serves, unless the runtime role cannot bypass RLS and owns no table, the timeouts are in force, migrations are applied and registries loaded. Proven on staging by granting `BYPASSRLS`. |
| Backups | `deploy/scripts/{backup,restore_verify,pitr_verify,wal_retention}.sh` | They must be **scheduled** (§7). Nothing in the compose topology schedules them. |

On AWS (the target in `13-deployment-evolution.md`) the managed services replace the data tier: RDS, ElastiCache, AmazonMQ and S3. The same images, commands and order apply.

## 2. The build (immutable, reproducible)

```bash
RELEASE=$(date -u +%Y.%m.%d)-$(git rev-parse --short HEAD)
docker build -f deploy/docker/backend.Dockerfile  --build-arg RELEASE=$RELEASE -t registry/workeasy360-backend:$RELEASE .
docker build -f deploy/docker/frontend.Dockerfile --build-arg RELEASE=$RELEASE \
  --build-arg BACKEND_ORIGIN=http://api:8000 -t registry/workeasy360-frontend:$RELEASE .
# Fails on any HIGH/CRITICAL that has a published fix (as CI does); the
# unfixable OS findings are triaged in 11-security-scans.md.
trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 registry/workeasy360-backend:$RELEASE
trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 registry/workeasy360-frontend:$RELEASE
```

- The build context is an allow-list (`.dockerignore`), so `.env`, keys, tests and `node_modules` cannot enter an image.
- Dependencies are exact pins (`requirements.txt`, `package-lock.json` via `npm ci`).
- Tags are never reused. Deploy by tag, and record the digest (`backup.sh` writes it into every backup manifest).

## 3. Deploy order

The exact order, each step gated on the previous one:

1. **Backup.** Run `deploy/scripts/backup.sh <dir>` and confirm `SHA256SUMS`. A release with a migration also takes a WAL-consistent snapshot (RDS: manual snapshot).
2. **Migration.** The pre-deploy task `migrate` runs as the owner role, directly against PostgreSQL (never through PgBouncer and never from an app container at boot). In compose it is a service that must exit 0 before anything else starts (`service_completed_successfully`). CI's guard (`tests/architecture/test_migration_overlap_safety.py`) has already refused any migration that is unsafe while the old release runs.
3. **Backend.** Roll the api containers one at a time. Each runs the startup gate first (RLS role, table ownership, DB timeouts, migrations applied, registries): a failure exits the container before it serves. Then it must pass `/healthz/ready` before the next is replaced.
4. **Workers.** Roll `worker-critical`, `worker-default`, `worker-heavy` and `beat` one pool at a time. Celery's warm shutdown finishes in-flight tasks. `acks_late` plus reject-on-worker-lost means an interrupted task is redelivered, and every task is idempotent ([06-async-safety.md](06-async-safety.md)).
5. **Frontend.** Roll `web`.
6. **Verification.** Work through §5 "Post-deploy".

## 4. Zero/low downtime: what may overlap

**What the shipped topology gives.** `production.yml` runs **one** `api` and one `web` container. `docker compose up -d` replaces a container in place (stop, then start), so a compose deploy has a short gap. It was measured on staging (phase-12-status.md, item 33). Zero-downtime needs **at least two api and web instances** behind the edge, rolled one at a time by the orchestrator (for example ECS with minimum healthy 100 %), each gated on `/healthz/startup` and `/healthz/ready` as in §3.

Containers drain rather than being killed: `stop_grace_period` is 35 s for `api` (above gunicorn's 30 s `graceful_timeout`), 40 s for `worker-critical` (above its 30 s hard limit) and 60 s for the other pools. A longer task that is cut off is redelivered (`acks_late`) and is idempotent.

During steps 3–5 the old and the new release both run against the new schema.

- **Allowed:** new tables, nullable columns, columns with a database default (`db_default`), indexes built `CONCURRENTLY`.
- **Not allowed** in the same release as the code that stops using them: column or table drops, renames, NOT NULL without a DB default on an existing table, non-concurrent indexes on large tables.
- These use **expand/contract** instead: add → deploy code writing both → backfill → deploy code reading new → drop, one release later.
- The guard fails CI for any new migration with an unsafe operation unless the migration declares a reviewed `overlap_safety = "..."`.
- Phase 12's only migration (`ecosystem.0004`, a nullable JSON column on a 60-second table) is overlap-safe. A code minted by the previous release has no facts and is judged least-trusted.
- The migrations that already exist were written without this rule (for example `identity.0004`: NOT NULL without a DB default). The first production deployment starts from an empty schema, so none of them runs under overlap. They are listed in `migration_baseline.txt` and never grow.

## 5. Checklist

### Pre-deploy
- [ ] CI green on the release commit: backend suite, ruff, mypy, lint-imports, bandit, pip-audit, gitleaks, frontend gates, image build, and trivy with no HIGH/CRITICAL.
- [ ] `manage.py check --deploy --fail-level WARNING` passes in staging. W360.W010 (product on a wildcard-DNS host) is clean or has a signed-off exception.
- [ ] The release's migrations are reviewed: each is overlap-safe or declares `overlap_safety`; destructive changes have a forward-fix plan.
- [ ] The latest backup exists, has been restored within the last 30 days by `restore_verify.sh`, and its manifest names the running release.
- [ ] The secret store holds every variable in `production.env.example`, and no secret expires within the deploy window.
- [ ] Rollback target recorded: previous image tags and digests.

### Deploy
- [ ] `backup.sh` → checksums verified.
- [ ] `migrate` task exit 0 (keep its log).
- [ ] api rolled; `/healthz/startup` 200 per instance.
- [ ] Workers and Beat rolled; `w360_queue_consumers` ≥ 1 for each queue.
- [ ] web rolled.

### Post-deploy (within 15 minutes)
- [ ] `/healthz/ready` 200 on every api instance. Prometheus target `w360-api` is up and there are no firing page alerts. `production.yml` does not run Prometheus or Alertmanager: they, and the pager route, are deployed separately (§7). The rules are `deploy/observability/alerts.yml`.
- [ ] Sign-in, My Products, public catalog and a product launch (JSON mode) work through the edge.
- [ ] One transactional email is delivered (a password reset to an ops mailbox).
- [ ] Edge headers present: HSTS, CSP (nonce), X-Frame-Options, nosniff. `/healthz/*` and `/api/v1/public/edge/*` answer 404 from outside.
- [ ] 5xx rate, p95 latency and queue depth are at their baselines for 15 minutes.

### Rollback / forward-fix
- [ ] **Code-only release:** redeploy the previous image tags in the same order (web, workers, api). No database action.
- [ ] **Release with an overlap-safe migration:** roll the code back; leave the schema. The old code ignores the new column or table, so no database rollback is needed.
- [ ] **Migration that must be reversed:** only if it is reversible *with the data it now holds* (§6). `manage.py migrate <app> <previous>` with migration settings, **after** the code is rolled back.
- [ ] **Irreversible or data-destroying change: forward-fix.** A new release that repairs the problem. Restore from backup only if data is lost; that is the disaster-recovery path with its RPO ([03-backup-and-restore.md](03-backup-and-restore.md)).

## 6. What can actually be rolled back

"Code rollback" never implies "database rollback". Measured in the Phase 12 rehearsal (`scripts/rehearse_phase12_upgrade.py`):

- **Every migration reverses structurally on an empty schema** (the round trip from forward to zero and forward again). See the status document for the result.
- **Reversible in structure only, and lossy with real data:**
  - `payments.0002` makes `invoice.subscription` nullable. Once a pay-first order invoice exists (subscription NULL), reversing fails. **Forward-fix only after the first order.**
  - `pricing.0002`'s data step has a no-op reverse (the partner price books it retired stay retired).
  - Several data migrations reverse as no-ops by design (listed in `01-audit-gap-list.md` §C, G-37).
- **Phase 12's `ecosystem.0004` reverses over populated data with no loss** (proved on a copy of the staging database).

## 7. What must be scheduled or deployed beside the compose topology

The compose file runs the application. These are **not** in it and must exist before go-live (status §2, OPS-2 and OPS-3):

| Job | Command | When | Why |
|---|---|---|---|
| Logical backup + off-host copy | `W360_COMPOSE=... W360_FILES_VOLUME=... deploy/scripts/backup.sh /srv/backups/w360`, then copy the new directory off the host (object storage with versioning) | daily, e.g. 02:15 UTC | The portable backup: the RPO for losing the host is its age. |
| Base backup + WAL retention | `W360_COMPOSE=... KEEP=7 deploy/scripts/wal_retention.sh`, then ship `/wal-archive` off-host (or use pgBackRest/WAL-G to object storage instead) | daily, e.g. 02:45 UTC | Point-in-time recovery needs a base backup, and without pruning the archive fills the disk (1.2 GB after one day of staging). |
| Restore test | `deploy/scripts/restore_verify.sh <latest backup>` | monthly, and after any backup-script change | A backup that was never restored is not a backup. |
| PITR test | `deploy/scripts/pitr_verify.sh <work dir>` | monthly | Proves the archive replays. |
| Monitoring | Prometheus with `deploy/observability/{prometheus,alerts}.yml`, node_exporter on the host (`W360DiskFillingUp` needs it), Alertmanager with a pager or chat route | always on | Without them the alerts are never evaluated and nobody is paged. |
| Dead-man's switch | a heartbeat from each scheduled job (for example to the pager's heartbeat API) | per job | A job that silently stops running is the backup failure nobody sees. |

Example crontab on the single host, with the heartbeats left to the operator's pager:

```
15 2 * * *  cd /srv/workeasy360 && W360_COMPOSE="docker compose -p w360 -f deploy/compose/production.yml --env-file /etc/w360/production.env" W360_FILES_VOLUME=w360_seaweed deploy/scripts/backup.sh /srv/backups/w360
45 2 * * *  cd /srv/workeasy360 && W360_COMPOSE="docker compose -p w360 -f deploy/compose/production.yml --env-file /etc/w360/production.env" KEEP=7 deploy/scripts/wal_retention.sh
```
