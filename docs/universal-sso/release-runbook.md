# Release runbook: back-channel logout (ADR-069), domain ownership (ADR-070), and the move to WorkEasy360 Accounts (ADR-067)

Written for: the operator of the single production host, and the owner who approves each step.

**Nothing in this runbook has been run in production.** Every step needs the owner's explicit approval: merges, deploys, DNS, stage changes and product releases. Each step is reversible on its own, and the rollback is given with it.

What is already live (16 November): ADR-067 at **stage off** (release `2026.11.16-fe495b7`), so `auth.workeasy360.com` is the sign-in and the issuer. `accounts.workeasy360.com` does not resolve yet.

## Order, and why

| # | Step | Depends on | Who |
|---|---|---|---|
| 0 | Merge PR #3 into `main` | review | owner |
| 1 | Merge PR #4 (ADR-069), then PR #5 (ADR-070) into `main` | review; CI green | owner |
| 2 | Platform release from `main`: migrations `ecosystem.0007`/`0008`, api, workers, beat | 1 | operator |
| 3 | DNS record `accounts` | - (can be done any time) | owner |
| 4 | Accounts **stage a**, then **stage b** | 2 (for back-channel to name the right issuer), 3 | operator |
| 5 | Easy CRM release: connector + `WORKEASY360_ISSUER=https://accounts.workeasy360.com` + identity move | 4 (stage b) | owner / CRM pipeline |
| 6 | Register Easy CRM's back-channel URL in Super Admin | 2, 5 | Super Admin |
| 7 | **Stage c**, later **d**, later **e** | every product moved; 30 days of no `auth.` tokens | operator |

Step 2 comes before step 4 because, from stage b, two issuers are honoured. A release without ADR-069's addendum would sign every logout token as the canonical issuer, and a product on the other name would refuse it. Steps 2 and 4 may share one maintenance window.

## Step 2 - platform release (PR #4 + PR #5)

Before you start:

- `main` contains #3, #4 and #5, and its CI is green.
- Run `pip-audit` and Trivy; both are clean in CI.
- The images are built and loaded under a new tag `<tag>` (`deploy/single-host/README.md`, "Release and rollback").

```sh
sudo /opt/workeasy360/bin/w360-backup                                    # database backup first
E=/opt/workeasy360/env/production.env
sudo cp -p $E $E.before-adr069-$(date -u +%Y%m%dT%H%M%SZ)
# unpack the release's deploy/ (git -c core.autocrlf=false archive) into /opt/workeasy360/releases/<tag>
sudo python3 /opt/workeasy360/releases/<tag>/deploy/single-host/make_env.py set W360_RELEASE --env $E   # value: <tag>
sudo ln -sfn /opt/workeasy360/releases/<tag> /opt/workeasy360/current
sudo /opt/workeasy360/bin/w360 up -d          # the migrate job runs first (depends_on), then api, workers, beat, web, edge
```

Verify:

```sh
sudo /opt/workeasy360/bin/w360 exec api python manage.py showmigrations ecosystem </dev/null | tail -3   # [X] 0007, [X] 0008
curl -s https://auth.workeasy360.com/.well-known/openid-configuration | python3 -m json.tool | grep -E '"issuer"|backchannel'
#   "issuer": "https://auth.workeasy360.com", "backchannel_logout_supported": true
sudo /opt/workeasy360/bin/w360 logs --tail 50 beat | grep -E "backchannel-logout-deliver|products-recheck-domains"
```

Then, in Super Admin, open Products, pick a product, and check its Integration tab:

- **SSO readiness** says what is missing, if anything.
- **Domains** lists every hostname. Existing ones are carried over as `pre_existing`, verified.
- **Back-channel logout** shows "not registered".

**Rollback.** Put `W360_RELEASE` and `current` back to the previous release and run `w360 up -d`. The new tables and columns are unused by the old code, so they stay. Undo the schema only if you must (`docs/universal-sso/migrations-ecosystem-0007-0008.md`, "Rolling back"): stop the workers and beat, then `migrate ecosystem 0006_sign_in_ask_once` with the new release, then redeploy the old one. No user, organization, credential or product data changes in either direction.

## Steps 3 and 4 - DNS, then stages a and b

The full procedure is `deploy/single-host/README.md`, section "Accounts". In short:

```sh
nslookup accounts.workeasy360.com                        # must resolve first (owner: Cloudflare record `accounts`)
E=/opt/workeasy360/env/production.env
sudo cp -p $E $E.before-accounts-$(date -u +%Y%m%dT%H%M%SZ)
sudo python3 /opt/workeasy360/current/deploy/single-host/make_env.py accounts --stage b --env $E
sudo /opt/workeasy360/bin/w360 run --rm --no-deps -T api python manage.py check </dev/null
sudo /opt/workeasy360/bin/w360 run --rm --no-deps -T edge caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null
sudo /opt/workeasy360/bin/w360 up -d --no-deps edge
sudo /opt/workeasy360/bin/w360 up -d api worker-critical web
python scripts/verify_portal_dns_tls.py                   # every check PASS, both names
curl -s https://accounts.workeasy360.com/.well-known/openid-configuration | grep '"issuer"'   # accounts. at stage b
curl -s https://auth.workeasy360.com/.well-known/openid-configuration | grep '"issuer"'       # still auth.
curl -sI "https://auth.workeasy360.com/login?resume=x" | grep -i '^location'                  # https://accounts.workeasy360.com/login?resume=x
```

What people see: they sign in once more on `accounts.`, because sessions are per host. Products on `auth.` notice nothing.

**Rollback:** `--stage off` and the same three commands. Do this only while no product is configured with `accounts.`; otherwise go back to b at most.

## Step 5 - Easy CRM

Branch `feat/workeasy360-sso-connector` (Easy CRM repository), merged to `main`. Its own procedure is `docs/workeasy360-sso.md`, "Moving to WorkEasy360 Accounts" and "Rolling out the connector release". In order:

1. Read SSM `/keel/production/WORKEASY360_ISSUER`. On 16 November CRM answered `sso_error=unavailable`, consistent with `app.` being stored there.
2. Set it to `https://accounts.workeasy360.com`. The platform must be at stage b.
3. Deploy CRM through its pipeline. On the host, after `migrate` and before traffic:
   ```sh
   python manage.py move_workeasy360_issuer --from <the old value>            # dry run: expect identities=0 if no sign-in ever completed
   python manage.py move_workeasy360_issuer --from <the old value> --apply    # only if it reports identities to move
   python manage.py w360sso_check                                            # READY
   ```
4. Do one real sign-in and one sign-out.

**Rollback:** put the old SSM value back and run `move_workeasy360_issuer --from https://accounts.workeasy360.com --apply`. Then redeploy the previous image (`f5c9254`). No migration needs reversing.

## Step 6 - Easy CRM's back-channel URL

Go to Super Admin, then Products, then Easy CRM, then Integration, then Authentication. Set **Back-channel logout URL** to `https://crm.workeasy360.com/api/auth/workeasy360/backchannel-logout`; its hostname is already proven (carried over). Then sign out of WorkEasy360 on one device: that device's CRM session ends, and Integration, then Back-channel logout, shows one **Delivered**.

**Rollback:** clear the URL. Nothing more is sent.

## Step 7 - stages c, d, e

| Stage | When |
|---|---|
| c | When the products that matter are on `accounts.` |
| d | When no `ecosystem.token.issued` naming `https://auth.workeasy360.com` has been logged for 30 days (`w360 logs api \| grep ecosystem.token.issued \| grep auth.workeasy360.com`, appended weekly to a file, because the container keeps only 5 x 50 MB of logs). A product still on `auth.` then fails closed. |
| e | When `accounts_legacy_host_requests_total` stays flat. Delete the `auth` DNS record at the same time. |

Each is the stage 4 command with `--stage c|d|e`.

## Smoke tests after each step

- A product sign-in from a clean browser lands on `accounts.workeasy360.com` (from stage a), and returns to the product signed in.
- "Use another account" shows the chooser. Continue keeps the person; Switch signs in someone else, and the first person's product session ends (back-channel products).
- Account & Security, then **Sign out everywhere**, reports each back-channel product as signed out.
- Super Admin, then Products, then Integration: readiness is ready, domains are verified, deliveries are delivered.

The same journeys run in Chrome, against a local copy of this release behind the production edge at stage b, in `Accounts/e2e/run_browser.py` (outside this repository). Its last report is recorded in `Accounts/README.md`.
