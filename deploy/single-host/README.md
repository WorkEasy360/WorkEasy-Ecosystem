# Single small host (AWS EC2 t3.micro) - operator runbook

First deployed 2026-10-13: release `2026.10.13-c664656` (commit `c664656`) on
`i-048217cdd868c2867` (ap-southeast-1), 20 GB gp3, 2 GB swap.

## Layout on the host

| Path | What |
|---|---|
| `/opt/workeasy360/releases/<release>/deploy/` | `deploy/` of the release commit plus this directory |
| `/opt/workeasy360/current` | symlink to the running release |
| `/opt/workeasy360/env/production.env` | every secret, root 0600, rendered on the host by `make_env.py` (never regenerate: new secrets cannot open the existing volumes) |
| `/opt/workeasy360/env/seaweedfs-s3.json` | object-store access key, uid 1000 (the SeaweedFS user) 0400 |
| `/opt/workeasy360/env/superadmin-initial-password` | the bootstrap Super Admin's first password, root 0600 - delete after the first sign-in and password change |
| `/opt/workeasy360/bin/w360` | `docker compose` with project `w360`, both compose files and the env file fixed |
| `/opt/workeasy360/backups/` | nightly backups, 7 days (cron 20:15 UTC, `w360-backup`) |
| `/opt/workeasy360/baseline/` | host state before and after the first deployment |

## Everyday commands

```sh
sudo /opt/workeasy360/bin/w360 ps
sudo /opt/workeasy360/bin/w360 logs --tail 100 api        # api, worker-critical, web, edge, ...
sudo /opt/workeasy360/bin/w360 restart edge               # e.g. retry certificates right after DNS changes
sudo /opt/workeasy360/bin/w360 exec api python manage.py <command> </dev/null
```

Always give `exec` `</dev/null` inside scripts: it reads stdin and will swallow a heredoc.

## Owner steps still open

1. **DNS (Cloudflare, DNS only / grey cloud):** A records `admin`, `partner`, `app`, `auth`, `api` -> the instance's Elastic IP. Then `w360 restart edge`; Caddy obtains Let's Encrypt certificates and renews them itself. If the records are ever proxied (orange cloud), set `W360_TRUSTED_PROXY_COUNT=2` and list Cloudflare's ranges in `W360_EDGE_TRUSTED_PROXIES` (deploy/edge/Caddyfile), and use SSL mode Full (strict).
2. **SMTP (Hostinger):** create `no-reply@workeasy360.com`, then
   `sudo python3 /opt/workeasy360/current/deploy/single-host/make_env.py set W360_EMAIL_HOST_PASSWORD --env /opt/workeasy360/env/production.env`
   (input hidden) and `sudo /opt/workeasy360/bin/w360 up -d api worker-critical`.
3. **Super Admin:** sign in at https://admin.workeasy360.com as `admin@workeasy360.com` with the password in `superadmin-initial-password`, change the password, delete the file. (No second factor is asked for anywhere - ADR-050; the Super Admin console is protected by the password alone, so use a long unique one.)
4. **Products:** registered as draft integrations with 7-day trials stored switched off; publish plans and activate integrations in the console.

## Release and rollback

New release: build and load both images under a new tag, unpack the commit's `deploy/` (with `git -c core.autocrlf=false archive` on Windows - CRLF breaks `roles.sh` and every shell script) into `releases/<tag>`, run `w360-backup`, set `W360_RELEASE=<tag>` with `make_env.py set`, re-point `current`, `w360 up -d`.

Rollback (code): set `W360_RELEASE` back, re-point `current` to the previous release, `w360 up -d`. The schema stays (04-deployment.md §5-6).

Take WorkEasy360 off the host, data kept: `sudo /opt/workeasy360/bin/w360 down` - frees ports 80/443; volumes stay. Never `down -v` unless the data is meant to go.

Restore test: `cd /opt/workeasy360/current && W360_BACKEND_IMAGE=workeasy360-backend:<tag> W360_ENV_FILE=/opt/workeasy360/env/production.env deploy/scripts/restore_verify.sh /opt/workeasy360/backups/<stamp> </dev/null`

## partner.workeasy360.com

Since 2026-10-23 the edge uses the reviewed `deploy/edge/Caddyfile` unchanged, so `partner.workeasy360.com` is the WorkEasy360 partner portal (central sign-in on `auth.`, the Reseller / White-label / Affiliate workspaces). From 2026-10-20 to 2026-10-23 this overlay routed it to the separate Partner Panel (`/opt/partner-panel`, compose project `ppanel`). That panel is stopped, not removed: its database volume, its nightly dump in `/opt/partner-panel/backups/` and its images remain. To bring it back temporarily, the previous release (`2026.10.19-5f01823-web1-pp`) still has the overlay and Caddyfile that route to it - see `docs/partner-portal-unification/status.md` (rollback).

## Known limits of this host size

- 1 GiB RAM: ~560 MiB of idle pages live in swap; one gunicorn worker (4 threads), one Celery worker (2 slots, all queues, Beat embedded), one password hash at a time.
- Point-in-time recovery is off (no WAL archive); recovery point = last nightly backup, and backups stay on the same EBS volume.
- No monitoring or alerting (04-deployment.md §7).
- T3 CPU credits: keep the instance in *standard* credit mode so a busy hour throttles instead of billing.
