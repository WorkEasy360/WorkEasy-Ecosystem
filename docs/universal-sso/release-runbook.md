# Release runbook: WorkEasy360 Accounts on accounts.workeasy360.com (ADR-067, ADR-069, ADR-070, ADR-071)

Written for: the operator of the single production host, and the owner who approves each step.

**Nothing in this runbook has been run in production.** Every step below needs the owner's explicit approval before it is run: merges, deploys, DNS, stage changes, the identity cutover and product releases. Each step is reversible on its own, and its rollback is given with it.

**Live today (19 November):** ADR-067 at **stage off** (release `2026.11.18-b10530e`). `auth.workeasy360.com` is the sign-in and the issuer.

- `accounts.workeasy360.com` resolves since 19 November: the owner added the proxied Cloudflare record, an A record to the same origin as `auth.`.
- It answers **Cloudflare 525** until stage a. The edge at stage off has no site for that name, so the TLS handshake to the origin fails.
- The fix is stage a itself (step 4), which gives the edge its `accounts.` site with a certificate.
- Do not weaken Cloudflare's SSL mode to hide it. Do not point `accounts.` anywhere else.

## Release R2 (prepared 19 November): Universal Login completion

This release carries #4, #5, #7, #9, #10, #11, #12 and `feat/universal-login-complete` (ADR-074), as merged into `main` in that order. The steps below are steps 1-5 of the table under "Order, and why", with this release's additions. **Nothing here has been run in production.**

**Gates before any production step (owner):**

1. An independent review of each PR (none has one; the agent wrote them under the owner's account).
2. CI green on the merge commit.
3. A fresh database backup (`w360-backup`) with a restore drill (`w360-restore-drill`) that passed this week.
4. The owner's written approval for each numbered step.

**Migrations (six, all additive):**

- `ecosystem.0007_backchannel_logout`
- `ecosystem.0008_product_domains`: existing hostnames are carried over as `pre_existing`, verified.
- `ecosystem.0009_product_last_sign_in`
- `ecosystem.0010_product_connection`
- `ecosystem.0011_product_sign_in_setup`: a new table only.
- `identity.0010_account_event_product_sign_in`

Rehearsed against the production image: see "Evidence" in the report of 19 November.

**Environment checklist (production env file; names only, no values here).** Check each with `make_env.py get <NAME>` before the release:

| Name | Value at this release | Why |
|---|---|---|
| `W360_ACCOUNTS_IDENTITY` | `off` for step 2; `on` only at step 5 | ADR-071 cutover is its own step |
| `W360_AUTH_ORIGIN` / `W360_AUTH_LEGACY_ORIGIN` | set by `make_env.py accounts --stage a/b` | ADR-067 stages; never edited by hand |
| `W360_OIDC_ISSUER`, `W360_OIDC_TRANSITION_ISSUERS` | set by the stage command | strict issuer validation |
| `W360_BEAT_SIGN_IN_SETUP_S` | absent (default 300 s) | ADR-074 sweep |
| `W360_RL_SIGN_IN_SETUP_USER` / `_IP` | absent (defaults 10 and 30 per minute) | *Check now* rate limits |
| `INTEGRATION_EGRESS_ALLOW_HTTP` / `_ALLOW_LOOPBACK` | **absent or false** | the setup fetch is https-only and never reaches private addresses in production |
| `W360_PAYMENTS_FAKE_PROVIDER` | **absent** | test provider is development-only |

No new secret is introduced. Automatic setup reads only public documents and public keys.

**Commands (operator, after approval).** These are the commands of step 2, plus this release's checks:

```sh
sudo /opt/workeasy360/bin/w360-backup
E=/opt/workeasy360/env/production.env
sudo cp -p $E $E.before-r2-$(date -u +%Y%m%dT%H%M%SZ)
# unpack the release's deploy/ with: git -c core.autocrlf=false archive <sha> deploy | tar -x -C /opt/workeasy360/releases/<tag>
sudo python3 /opt/workeasy360/releases/<tag>/deploy/single-host/make_env.py set W360_RELEASE --env $E   # value: <tag>
sudo ln -sfn /opt/workeasy360/releases/<tag> /opt/workeasy360/current
sudo /opt/workeasy360/bin/w360 up -d
sudo /opt/workeasy360/bin/w360 exec api python manage.py showmigrations ecosystem identity </dev/null | grep -E "0007|0008|0009|0010|0011"
sudo /opt/workeasy360/bin/w360 logs --tail 80 beat | grep -E "products-sign-in-setup|products-recheck-domains|backchannel-logout-deliver"
```

**Smoke tests after step 2 (read-only, then one disposable sign-in):**

```sh
curl -s https://auth.workeasy360.com/.well-known/openid-configuration | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["issuer"], d.get("backchannel_logout_supported"))'
curl -sI https://app.workeasy360.com/ | head -1                      # 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://app.workeasy360.com/api/v1/orgs/x/apps/y/sign-in   # 401/403/404, never 200
```

Then, in a browser, with a test organization that buys nothing:

1. Explore Products lists the catalog; a configured product offers **Sign in to <product>**.
2. Choosing it shows **Continue as <name>** / **Use another account**.
3. *Continue as* ends on the product's "no access" page.
4. Super Admin, a product's Integration tab: **Automatic sign-in setup** shows "Set up by hand" for every existing product (nothing changed them).

**After step 4 (stage a):** `curl -sI https://accounts.workeasy360.com/login | head -1` answers 200. This is the end of the 525.

**Rollback:**

1. **Code:** put `W360_RELEASE` and `current` back to `2026.11.18-b10530e`, then `w360 up -d`. The new tables and columns stay, unused by the old code. The chooser order and Explore's *Sign in* revert with the code.
2. **Schema, only if you must:**
   1. Stop the workers and beat.
   2. With the new release, run `migrate ecosystem 0006_sign_in_ask_once` and `migrate identity 0009_email_change`.
   3. Redeploy the old release.
3. **A product activated by automatic setup that must stop:** *Suspend* it on its Integration tab. Automatic setup never resumes a suspended integration.

**Post-deployment recovery:**

- **A product's sign-in fails after the release:** its Integration tab shows SSO readiness and the setup checks with their reasons. Its credentials and addresses are audited (`ecosystem.integration.*`).
- **The setup sweep misbehaves:** set `W360_BEAT_SIGN_IN_SETUP_S` to a very large value and restart beat. Nothing else depends on it.
- **Explore's *Sign in* must be withdrawn quickly:** roll the code back (step 1 above). No data depends on it.

## The target

| Host | Role |
|---|---|
| `https://accounts.workeasy360.com` | **WorkEasy360 Accounts.** Sign-in, sign-up, email verification, password reset, the account page (security, sessions and devices, sign out everywhere, product sign-in preferences), the product sign-in and chooser (OIDC), Switch Account, organization choice, product sign-out. The **OIDC issuer**. |
| `https://app.workeasy360.com` | **User Admin.** Organizations, members, product access, subscriptions, business settings. Signs in through Accounts. |
| `https://auth.workeasy360.com` | **Previous name**, while products move: discovery, keys and the server-to-server APIs. Browsers are forwarded to Accounts. Retired at stage e. |
| `admin.`, `partner.`, `store.` | Unchanged; they keep their own sign-in. |

There is one identity provider, one database and one user UUID (`sub`) per person.

## Order, and why

| # | Step | Depends on | Approves |
|---|---|---|---|
| 0 | Merge #6 (next 16.4.6) into `feat/partner-admin-panel`, then #3 into `main` | review, CI | owner |
| 1 | Merge #4 (ADR-069), #5 (ADR-070) and #7 (ADR-071) into `main`, in that order | review, CI | owner |
| 2 | **Platform release** from `main`, with `W360_ACCOUNTS_IDENTITY=off`: migrations `ecosystem.0007` and `0008`, then api, workers, beat, web | 1 | owner, then operator |
| 3 | **Cloudflare:** add `accounts` | none (any time) | owner |
| 4 | **Stage a, then stage b** | 2, 3 | operator |
| 5 | **Identity cutover:** `W360_ACCOUNTS_IDENTITY=on` | 4, and the checks in step 5 on staging or a canary | owner, then operator |
| 6 | **Easy CRM** release with `WORKEASY360_ISSUER=https://accounts.workeasy360.com` and its identity move | 4 (stage b) | owner |
| 7 | Register Easy CRM's back-channel URL in Super Admin | 2, 6 | Super Admin |
| 8 | **Stage c**, later **d**, later **e** | every product moved; 30 days of no `auth.` tokens | owner, then operator |

Why this order:

- **Step 2 before step 4.** From stage b two issuers are honoured. Only a release with ADR-069's addendum signs each logout token as the issuer its product trusts.
- **Step 5 after step 4.** User Admin's sign-in needs `accounts.` to serve.
- **Steps 2 and 4** may share a maintenance window.

## Step 2: platform release (#3 + #6, #4, #5, #7), identity switch off

Prerequisites:

- `main` contains #3 (with #6), #4, #5 and #7, and its CI is green.
- The images are built and loaded under a new tag `<tag>` (`deploy/single-host/README.md`, "Release and rollback").
- `W360_ACCOUNTS_IDENTITY` is absent or `off` in the env file. Off is ADR-059 exactly.

```sh
sudo /opt/workeasy360/bin/w360-backup                                    # database backup first
E=/opt/workeasy360/env/production.env
sudo cp -p $E $E.before-release-$(date -u +%Y%m%dT%H%M%SZ)
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
- **Domains** lists every hostname: existing ones are `pre_existing` and verified.
- **Back-channel logout** says "not registered".

What people see: nothing changes. The sign-in is still on `auth.` and `app.`.

**Rollback:**

1. Put `W360_RELEASE` and `current` back to the previous release, then `w360 up -d`. The new tables and columns stay, unused by the old code.
2. Undo the schema only if you must: stop the workers and beat, then `migrate ecosystem 0006_sign_in_ask_once` with the new release, then redeploy the old one (`docs/universal-sso/migrations-ecosystem-0007-0008.md`).

Rehearsed 17 November on a populated scratch database: down to 0006, up, up again. Users, organizations, integrations, credentials and signing keys were unchanged throughout (7/7).

## Step 3: Cloudflare DNS (owner)

In the `workeasy360.com` zone, add **`accounts`**: the same type and content as the existing `auth` record, **Proxied** (orange cloud), TTL Auto. The other portal records are proxied today.

- Keep `auth` exactly as it is until stage e.
- Leave the zone's SSL mode as it is (Full (strict)).
- Check: `nslookup accounts.workeasy360.com 1.1.1.1` returns Cloudflare addresses (`2606:4700:…` / `104.21.…` / `172.67.…`).

Nothing serves `accounts.` until stage a: before then, the edge does not know the name.

## Step 4: stages a and b, exactly

```sh
E=/opt/workeasy360/env/production.env
sudo cp -p $E $E.before-accounts-$(date -u +%Y%m%dT%H%M%SZ)
sudo python3 /opt/workeasy360/current/deploy/single-host/make_env.py accounts --stage a --env $E     # later: --stage b
sudo /opt/workeasy360/bin/w360 run --rm --no-deps -T api python manage.py check </dev/null
sudo /opt/workeasy360/bin/w360 run --rm --no-deps -T edge caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null
sudo /opt/workeasy360/bin/w360 up -d --no-deps edge          # first: Caddy obtains the certificate for accounts.
sudo /opt/workeasy360/bin/w360 up -d api worker-critical web
python scripts/verify_portal_dns_tls.py                       # every check PASS for both names
```

### Stage a: `accounts.` serves; every token stays `iss = https://auth.workeasy360.com`

| Area | Behaviour |
|---|---|
| **Existing clients** (products configured with `WORKEASY360_ISSUER=https://auth.workeasy360.com`) | Nothing to change. Discovery on `auth.` still names `auth.` as the issuer and lists `auth.` endpoints, with the same keys. Their token, userinfo, entitlement and server-to-server calls keep going to `auth.` and are answered there. |
| **The browser** | The authorization request to `auth./…/oauth/authorize` gets a **302** to the same path and query on `accounts.`, which completes it and returns to the product's callback with the code. One extra hop; no new password if Accounts is signed in. |
| **Issuer validation** | Unchanged: the ID token's `iss` is `auth.`, as the products expect. Discovery read on `accounts.` is refused for a product: `accounts.` is not yet an issuer, so a misconfigured product fails closed, never half-works. |
| **Sessions** | The central sign-in session now lives on `accounts.` (host-only `__Host-w360_session`). An old `auth.` session is never read again, so people **sign in once more** the first time a product sends them there. Product sessions are untouched. User Admin keeps its own sign-in (switch off). |
| **Logout** | Product sign-out (RP-initiated) goes `auth./…/oauth/logout` → 302 → `accounts.`, which confirms. Back-channel logout tokens name `auth.` (the issuer the product redeemed under). "Sign out everywhere" on Account & Security works as before. |
| **Rollback** | `--stage off` and the same commands. No product notices. |

### Stage b: both names are issuers, each on its own host

| Area | Behaviour |
|---|---|
| **Existing clients on `auth.`** | Exactly as stage a. Tokens say `iss = auth.`, which they verify. |
| **Clients moved to `accounts.`** (one at a time, each in its own release with its identity move) | Discovery on `accounts.` names `accounts.`, with `accounts.` endpoints and the **same keys**. Tokens issued there say `iss = accounts.`, the same `sub`. |
| **Issuer validation** | Strict on both sides: each product verifies exactly its own configured issuer, and a token from one name is refused by a product configured with the other. The platform accepts client assertions only for its own two token endpoints. Verified in a real browser: one product on each name, both signed in. |
| **Sessions** | One central session on `accounts.` serves products on either name. Codes issued before the switch remain redeemable, and the `iss` is chosen where the code is redeemed. |
| **Logout** | Each logout token is signed as the issuer that product **last redeemed a code under** (ADR-069 addendum), so a product on `auth.` and a product on `accounts.` are both signed out by "sign out everywhere" and by Switch Account. Verified in a real browser: delivered, `http_200`, each under its own issuer. |
| **Rollback** | **Per product, never globally, once any product is on `accounts.`** (next section). The platform goes back to stage a only while no product is configured with `accounts.`; `w360 exec api python manage.py w360_issuer_readiness --retire https://accounts.workeasy360.com` names any product that last redeemed a code there. |

### Rolling back with products on both names

A global rollback to stage a would stop `accounts.` being an issuer, and every product already moved there would fail closed. So once one product has moved:

1. **Roll back the product, not the platform.** The platform stays at stage b, which serves both names. In the product's rollback release:
   - set `WORKEASY360_ISSUER` back to `https://auth.workeasy360.com`;
   - move its identity links back with the same command, pointed the other way: `w360sso_move_issuer --from https://accounts.workeasy360.com` (dry run, then `--apply`), or Easy CRM's `move_workeasy360_issuer --from https://accounts.workeasy360.com --apply`.

   People keep the same local accounts both ways. This is tested in the connector: `test_a_product_rolled_back_to_the_previous_name_keeps_its_people`, 18 November.
2. Logout keeps working through the rollback. Logout tokens follow the issuer the product last redeemed a code under (ADR-069 addendum). The first sign-in after the rollback moves it back to `auth.`.
3. Roll back the platform's code only to a release that still serves stage b, and only while some product is on `accounts.`. Every release since 2026.11.16 does. The database needs no rollback for this; `ecosystem` 0007 and 0008 stay, unused by older code. Rehearsed on 18 November with `Accounts/e2e/platform/rehearse_backup_restore.sh` (19/19). The run covered: the production backup script; `restore_verify.sh` with the production image (28/28); the upgrade, a re-run, rollback to 0006 with the production image starting again, and a migration blocked by a lock, which rolled back whole and then completed.

## Step 5: identity cutover (ADR-071): `W360_ACCOUNTS_IDENTITY=on`

**Before:** stage a or b is live, and the following checks pass in the real-browser run (`Accounts/e2e/run_browser.py`, stage b) or on a staging copy:

- sign-up and email verification on Accounts;
- password recovery on Accounts;
- User Admin signing in through Accounts (the hand-off), and its automatic retry;
- the account page;
- sign-out on Accounts ending User Admin's session;
- `app.`'s old identity addresses moving to Accounts.

```sh
E=/opt/workeasy360/env/production.env
sudo cp -p $E $E.before-identity-$(date -u +%Y%m%dT%H%M%SZ)
sudo python3 /opt/workeasy360/current/deploy/single-host/make_env.py set W360_ACCOUNTS_IDENTITY --env $E   # type: on
sudo /opt/workeasy360/bin/w360 up -d api worker-critical web          # api and web read the same switch
```

Then from outside:

```sh
curl -sI https://app.workeasy360.com/dashboard | grep -i '^location'      # https://accounts.workeasy360.com/login?mode=user&...&state=...
curl -sI https://app.workeasy360.com/signup | grep -i '^location'         # https://accounts.workeasy360.com/signup
curl -sI https://accounts.workeasy360.com/ | grep -i '^location'          # https://accounts.workeasy360.com/account
```

Finally, sign in once from a clean browser: User Admin, then Accounts, then the password, then back in User Admin.

What changes for people:

- User Admin's sign-in, sign-up and password reset are on Accounts. A User Admin already signed in stays signed in.
- New verification and reset emails link to Accounts. Links sent earlier still work: `app.`'s old addresses move to Accounts with the token fragment kept.

**Rollback:** set it to `off` and run the same `up -d`. That is ADR-059 exactly. Links emailed while it was on still work, because Accounts' identity pages then move them back to `app.` with the fragment kept.

## Step 6: Easy CRM (WorkEasy360/AI-CRM#19)

1. Read SSM `/keel/production/WORKEASY360_ISSUER`. On 16 November CRM answered `sso_error=unavailable`, consistent with `app.` being stored there.
2. At stage b, set it to `https://accounts.workeasy360.com`.
3. Deploy through CRM's pipeline. After `migrate`, before traffic:
   ```sh
   python manage.py move_workeasy360_issuer --from <the old value>            # dry run: expect identities=0 if no sign-in ever completed
   python manage.py move_workeasy360_issuer --from <the old value> --apply    # only if it reports identities to move
   python manage.py w360sso_check                                            # READY
   ```
4. Do one sign-in and one sign-out.

**Rollback:** put the old SSM value back, run `move_workeasy360_issuer --from https://accounts.workeasy360.com --apply`, then redeploy `f5c9254`.

## Step 7: Easy CRM's back-channel URL

Go to Super Admin, then Products, then Easy CRM, then Integration, then Authentication. Set the back-channel logout URL to `https://crm.workeasy360.com/api/auth/workeasy360/backchannel-logout`; its hostname is carried over as proven.

Then sign out of WorkEasy360 on one device. The CRM session ends, and Integration, then Back-channel logout shows **Delivered 1**.

**Rollback:** clear the URL.

## Step 8: stages c, d, e

| Stage | When | What it does |
|---|---|---|
| c | The products that matter are on `accounts.` | Canonical issuer `accounts.`. `auth.` is still honoured on its own host. |
| d | `w360_issuer_readiness` answers **READY** for `https://auth.workeasy360.com` (below) | `auth.` is no longer an issuer. A product still on it fails closed at discovery. Browsers are still forwarded. |
| e | `w360_issuer_readiness` answers **READY** again after stage d, and the owner accepts the forwarded-browser count it reports | `auth.` stops being served. Delete the `auth` DNS record at the same time. |

Each uses the stage command above with `--stage c|d|e`.

**Retirement is decided by evidence, never by elapsed time.** The API logs are the evidence the database cannot hold. Keep them for the whole window: the container keeps only 5 x 50 MB, so append them to a file every week.

```sh
sudo /opt/workeasy360/bin/w360 logs --no-log-prefix --since 168h api >> /opt/workeasy360/evidence/api-logs.jsonl
```

Then:

```sh
sudo /opt/workeasy360/bin/w360 run --rm --no-deps -T -v /opt/workeasy360/evidence:/evidence:ro api \
  python manage.py w360_issuer_readiness --retire https://auth.workeasy360.com --log /evidence/api-logs.jsonl --days 30
```

The exit status is the answer: **0** READY, **1** NOT READY, **2** NOT ENOUGH EVIDENCE. Nothing is changed. The command weighs four things:

- **Configuration.** The name must no longer be the canonical issuer.
- **The database.** It looks at every active product's last issuer.
  - A product still on `auth.` blocks.
  - So does a product with no recorded issuer, because nothing shows which name it trusts. Have it sign someone in once, or suspend it.
- **The logs, over the whole window.**
  - Blocking: any token issued as `auth.`, and any discovery, key-set or API request served or refused by the old host (`accounts.legacy_host.request`).
  - Reported but not blocking: browsers forwarded from old links.
  - Logs that do not cover the window mean NOT ENOUGH EVIDENCE.
- **Refresh tokens.** None are issued; the token endpoint grants `authorization_code` only.

**If the answer is not READY, keep `auth.`.** A working compatibility name costs nothing. Retiring it while something still depends on it breaks a live integration.

## Global logout: what is and is not covered

| Product | Signed out by Accounts sign-out, Switch Account, "sign out everywhere" | Evidence |
|---|---|---|
| WorkEasy360 portals (User Admin, Accounts) | Yes. Sessions end directly; User Admin's handed-over session shares the Accounts sign-in. | real browser, 17 Nov |
| Template products, the `workeasy360-sso` connector | Yes, by back-channel, under either issuer | real browser and live E2E, 17 Nov |
| Easy CRM (connector, AI-CRM#19) | Yes, by back-channel, **once deployed and its URL registered** | live E2E (CRM backend), 17 Nov |
| EasyBook (connector, EasyBook#2) | Yes, by back-channel: access tokens are refused at once and refresh fails | live: `Accounts/e2e/run_global_logout.py`, 18 Nov |
| Easy Project (Spring, Project-Module#1) | Yes, on **every instance**: the logout ends a shared sign-in record checked on each request. Delivered to instance B, it ended the session held by instance A | live, two instances, 18 Nov |
| Easy Help Desk (NestJS, helpdesk-workeasy#1) | Yes: the per-request session check refuses the old access token at once, refresh fails, and open sockets are dropped | product e2e suite (two API instances); live, 18 Nov |

That evidence is from branches, run live against the platform locally. In production, global logout covers only what is deployed and registered. Each of these products still needs its PR reviewed, merged, deployed and its back-channel URL registered (Step 7 is CRM's). **Until then, global logout is not complete in production.**
