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

1. **DNS (Cloudflare, DNS only / grey cloud):** A records `admin`, `partner`, `app`, `accounts` (ADR-067; `auth` too while products move - see "Accounts" below), `api` -> the instance's Elastic IP. Then `w360 restart edge`; Caddy obtains Let's Encrypt certificates and renews them itself. If the records are ever proxied (orange cloud), set `W360_TRUSTED_PROXY_COUNT=2` and list Cloudflare's ranges in `W360_EDGE_TRUSTED_PROXIES` (deploy/edge/Caddyfile), and use SSL mode Full (strict).
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

## Accounts: moving the sign-in to accounts.workeasy360.com (ADR-067)

The central sign-in (product sign-in, access chooser, product sign-out, Switch Account) is **WorkEasy360 Accounts** on `accounts.workeasy360.com`. Its previous name `auth.workeasy360.com` stays reachable while products move: there, discovery, the key set and every server-to-server endpoint answer as before, and every browser request is sent (302) to the same address on `accounts.`. Same Django, same database, same users, same signing keys. Full record: `docs/accounts-portal/status.md`.

Each stage is a complete state: `make_env.py accounts --stage <x>` writes all seven values at once (origin, previous name, its edge switch, issuer, transition issuer, allowed hosts, CSRF origins). **Going back = running the earlier stage.**

| Stage | Sign-in pages + session | `auth.` | Canonical issuer | Also issued (own host only) |
|---|---|---|---|---|
| off | auth. | - | auth. | - |
| a | accounts. | forwards browsers, serves products | auth. | - |
| b | accounts. | same | auth. | accounts. |
| c | accounts. | same | accounts. | auth. |
| d | accounts. | same | accounts. | - |
| e | accounts. | retired | accounts. | - |

Before stage a: the owner adds the Cloudflare record `accounts` (proxied, like the others - Caddy then gets its own certificate through the proxy, as `store.` did) and checks it resolves: `nslookup accounts.workeasy360.com`.

Every stage change, on the host:

```sh
E=/opt/workeasy360/env/production.env
sudo cp -p $E $E.before-accounts-$(date -u +%Y%m%dT%H%M%SZ)
sudo python3 /opt/workeasy360/current/deploy/single-host/make_env.py accounts --stage a --env $E
sudo /opt/workeasy360/bin/w360 run --rm --no-deps -T api python manage.py check </dev/null   # settings accept the stage
sudo /opt/workeasy360/bin/w360 run --rm --no-deps -T edge caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null
sudo /opt/workeasy360/bin/w360 up -d --no-deps edge            # first: certificate for a new name
sudo /opt/workeasy360/bin/w360 up -d api worker-critical web
```

Then check from outside: `python scripts/verify_portal_dns_tls.py` (both names, all PASS), `curl -s https://auth.workeasy360.com/.well-known/openid-configuration` (issuer as the table says), `curl -sI "https://auth.workeasy360.com/login?resume=x"` (302 to `https://accounts.workeasy360.com/login?resume=x`), and a real product sign-in.

- **a** when DNS for `accounts.` works. Nothing a product sees changes: `iss` stays `https://auth.workeasy360.com`. People sign in once more on `accounts.` (sessions are per host; the old `auth.` session is never read again).
- **b** right after a (or together): a product may now be configured with `WORKEASY360_ISSUER=https://accounts.workeasy360.com` - one at a time, each on its own release, following the product checklist in `docs/accounts-portal/status.md` (its identity rows must move to the new issuer in the same release).
- **c** once the products that matter use `accounts.`.
- **d** only when no `ecosystem.token.issued` with `"issuer": "https://auth.workeasy360.com"` has been logged for 30 days: `w360 logs api | grep ecosystem.token.issued | grep auth.workeasy360.com`. The api container keeps only 5 x 50 MB of logs, so append that grep to a file weekly rather than trusting one read. A product still on `auth.` then fails closed (refuses discovery) - it is never given a wrong issuer.
- **e** when `accounts_legacy_host_requests_total` stays flat (nothing reaches `auth.`) and every product is on `accounts.`. The owner deletes the `auth` DNS record at the same time: once retired, the name is no platform host, and a record left pointing at the edge would land on the partner-domain site (which serves nothing but a branded landing).
- **Rollback** from a or b: `--stage off` and the same restart (products on `auth.` never noticed). Never go back past b while a product is configured with `accounts.` - it would fail closed until it is configured back.

## Known limits of this host size

- 1 GiB RAM: ~560 MiB of idle pages live in swap; one gunicorn worker (4 threads), one Celery worker (2 slots, all queues, Beat embedded), one password hash at a time.
- Point-in-time recovery is off (no WAL archive); recovery point = last nightly backup, and backups stay on the same EBS volume.
- No monitoring or alerting (04-deployment.md §7).
- T3 CPU credits: keep the instance in *standard* credit mode so a busy hour throttles instead of billing.
