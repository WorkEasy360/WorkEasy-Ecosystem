# partner.workeasy360.com: the WorkEasy360 partner portal (2026-10-23)

Brief: "Fix partner.workeasy360.com - central auth + type routing + guest login + migration + deployment". Decisions: ADR-047 addendum 2026-10-23 (builds on ADR-043, ADR-047, ADR-048). Browser check: `scripts/partner_portal_unification_browser_check/`.

## 1. Production before the change (audited 2026-10-23, read-only)

| Item | Found |
|---|---|
| Host | EC2 `i-048217cdd868c2867` (ap-southeast-1, t3.micro, 1 GiB + 2 GiB swap, 19 GB disk 63% used) |
| DNS | `partner`, `admin`, `app`, `auth`, `api` .workeasy360.com: Cloudflare **proxied** (orange cloud; answers are Cloudflare addresses) |
| TLS | Browser-facing certificate from Cloudflare (Google Trust Services WE1, CN workeasy360.com, valid to 2026-12-12); origin certificates from Caddy (ACME) |
| Edge | `w360-edge-1` (caddy:2) with `deploy/single-host/Caddyfile`: `{$W360_PARTNER_ORIGIN}` -> `ppanel-web:80` on network `ppanel_edge`; admin./app./auth./api. -> WorkEasy360 `web:3000` / `api:8000` |
| partner.workeasy360.com | **the separate Partner Panel** (compose project `ppanel`, `/opt/partner-panel`, images `partner-panel-{api,web}:2026.10.20-pp1`, own Postgres with 8 `team_members`, own bcrypt + JWT) |
| WorkEasy360 | release `2026.10.19-5f01823-web1` (`/opt/workeasy360/current` -> `releases/2026.10.19-5f01823-web1-pp`); api, worker-critical, web, edge, postgres, pgbouncer, redis, rabbitmq, seaweedfs all up/healthy |
| Auth/origin config | `W360_{ADMIN,PARTNER,APP,AUTH,API}_ORIGIN` = the five https origins; `W360_CSRF_TRUSTED_ORIGINS` = exactly admin, partner, app, auth; `W360_ALLOWED_HOSTS` = the five hosts; `W360_PARTNER_PANEL_URL` empty in `web` |
| Schema | every migration applied up to `partners.0006`; **0 partner accounts** (no partner rows of any kind) |
| Dependents of the Panel | only the edge (network `ppanel_edge`) and its own nightly `ppanel-backup` cron (20:40 UTC); nothing in WorkEasy360 links to it |

## 2. What changed

- **Edge.** The single-host overlay no longer replaces the Caddyfile or joins `ppanel_edge`: the edge runs the reviewed `deploy/edge/Caddyfile`, where `partner.` is served by WorkEasy360 like `admin.` and `app.` (`/api/*` -> api, the rest -> web). `deploy/single-host/Caddyfile` is deleted; `W360_PARTNER_PANEL_URL` is no longer passed to `web`.
- **Central sign-in.** Unchanged and now in use for partners: `partner.` -> `auth./login?mode=partner` -> email + password (`identity.User`) -> hand-off checks an active `PartnerMember` -> one-time code bound to `partner.` -> host-only session on `partner.` -> `/partner` -> the workspace the server resolves from `PartnerAccount.default_experience` and the live capability grants.
- **Guest Login.** Lands in the partner's own workspace (`guest.portal_path`), not the generic `/partner/<id>`; the hand-off to `partner.` now accepts a sign-in that holds a live guest view (a staff member has no partner membership, so in multi-portal mode Guest Login used to stop at "You don't have access to the Partner portal" - found here, invisible in single-host tests). Banner "Viewing Partner Portal as <name>" with **Exit Guest Session**, which ends the guest view and that partner-host session, then opens `admin./admin/partners/<id>`.
- **Guest-only partner sessions hardened** after the independent audit (P2): grant and factor re-checked at the hand-off, AAL 1 without step-up time, the session dies with the guest view (`SESSION_GUEST_UNTIL_KEY` enforced in `SessionStore.load`); sign-out ends the guest view (audited); a failed Exit stays on the page with an error instead of redirecting.
- **`is_staff`** reads moved out of `partner_views.py` into `control_plane_http.py` (`live_guest_view`, `end_guest_view`); `test_django_is_staff_is_never_an_authority` is green.
- **`partners.0008`**: backfills the primary workspace (rule in ADR-047 addendum).
- **Frontend image**: the runtime stage now copies `experiences.ts`, which `next.config.ts` imports - without it the new image would have crash-looped exactly as release 2026.10.19 did; `backend/tests/architecture/test_frontend_image_runtime.py` guards every such import.
- Banners that are sentences (guest view, an inactive account) wrap at phone widths (`.notice`); the browser check found 367 px of horizontal scroll at 390 px.

Deviation, stated: the workspace addresses stay `partner.workeasy360.com/partner/<type>/dashboard` (ADR-047: under `/partner` so the host routing and return-URL allowlists stay as they are). `partner.workeasy360.com/reseller/dashboard`, `/affiliate/...` and `/white-label/...` redirect there (307).

## 3. Migrations

| Migration | What | Safe under overlap |
|---|---|---|
| `partners.0007_partner_default_experience` | `PartnerAccount.default_experience` varchar(16), `db_default ''`, CHECK in (`''`, reseller, white_label, affiliate) | yes - declared `overlap_safety`; old code never names the column |
| `partners.0008_backfill_default_experience` | data only: empty -> white_label / reseller / affiliate from the live grants; explicit values kept; capabilities untouched | yes - declared `overlap_safety` |

No other model change: `makemigrations --check` "No changes detected".

## 4. Gates (final tree `95eafc4`)

| Gate | Result |
|---|---|
| Backend full suite (bare `pytest`, 4,732 tests) | **4,731 passed + 1 error**. The error is `partner_panel/test_products_profile.py::test_logged_in_without_partner_membership_is_denied`, with `database removed`: a second pytest that I started by mistake killed the run's connections at that moment. The file re-run alone: **23/23 passed**. |
| ruff, ruff format --check | clean (772 files) |
| mypy | no issues |
| lint-imports | 18 contracts kept, 0 broken |
| bandit (`-ll`) | 0 medium / 0 high |
| pip-audit --strict | no known vulnerabilities |
| `manage.py check` (migration settings, WARNING) | no issues |
| `makemigrations --check` | no changes |
| `check --deploy` | development settings: 0 errors (the expected transport warnings). **Production settings, in the deployed api container, `--fail-level WARNING`: no issues.** |
| Frontend: clean `npm ci` in a scratch copy, typecheck, eslint | clean |
| vitest | **819/819** (46 files) |
| `next build` | passes, no warnings |
| npm audit | 0 vulnerabilities |
| detect-secrets 1.5.0 (Linux, baseline) | 0 new findings |
| gitleaks v8.28.0 (working tree + 27 commits) | no leaks |
| Real browser, multi-portal, behind a local copy of the production edge (`scripts/partner_portal_unification_browser_check`) | **13/13 journeys**. 10 audited pages: axe 0, CSP 0, console errors 0, focus 0, one h1 each, reflow 35/35. |
| Independent security/architecture audit (Opus, read-only) | 0 P0. Its P1 (two architecture tests the change would have broken) and its P2 (a guest-only partner session outliving the view, AAL 2) are fixed and tested. See section 7 for the P3s. |

## 5. Deployment (2026-10-23, 18:54-19:15 UTC)

1. **Captured** the release before the change: `2026.10.19-5f01823-web1-pp` (`W360_RELEASE=2026.10.19-5f01823-web1`) and the container list. Stored in `/opt/workeasy360/backups/predeploy-20261023T185455Z/state.txt`.
2. **Database backup** `w360-backup`, `20261023T185456Z`:
   - checksums verified;
   - `db.dump` is 727,150 bytes, sha256 `b6046f6b4742e4b4915ef89e1f4c4238aa9da4bd778582417f3687b87ad0817e`;
   - a copy is kept as `predeploy-20261023T185455Z/db-backup` (root 0700), outside the nightly 7-day rotation.
3. **Configuration backup** `predeploy-20261023T185455Z/config.tgz` (root 0600, sha256 `8cdbe58f…e38a4`). It holds:
   - `env/`, `bin/` and the release's `single-host/`;
   - the Panel's compose file, env and bin;
   - both cron files.

   It contains secrets and is not in Git.
4. **Images** were built from tree `15658526…`, which is the commit's tree. Both were smoke-tested: the frontend starts and runs `next.config.ts`; the backend imports. They were loaded on the host with identical layer digests and tagged `2026.10.23-95eafc4`. `deploy/` was unpacked from the commit with no CRLF and executable scripts.
5. **Migration rehearsal on the host.** The backup was restored into an isolated Postgres (new network, volume and role passwords), then the new image migrated it as the migrator role:
   - applied `partners.0007` and `0008`;
   - all 123 data tables kept their row counts; only `django_migrations` grew by 2. The script's strict check flagged that row, which is expected;
   - `migrate --check` and the startup gate passed.
   - No data left the host.
6. **Application release.** Set `W360_RELEASE=2026.10.23-95eafc4`, pointed `current` at the new release, ran `migrate`, then recreated `api`, `worker-critical` and `web` (not the edge):
   - **production migrations applied: `partners.0007`, `partners.0008`**;
   - api healthy at 18:57:39.
7. **Internal verification before cutover**, with the partner Host header from inside the stack:
   - `/` goes to `/partner`, which goes to `auth./login?mode=partner`;
   - workspace addresses carry `next`;
   - `/reseller/dashboard` returns 307 to `/partner/reseller/dashboard`;
   - `/admin/...` goes to `admin.`, `/dashboard` goes to `app.`;
   - the partner API returns 401 signed out; the admin API returns 404 on `partner.`.
8. **Cutover** (18:58:47):
   - took a final Panel dump, `panel-20261023T185845Z.dump` (168K);
   - recreated **only the edge** on `deploy/edge/Caddyfile`. It now sits on the W360 networks only, with no `ppanel_edge`;
   - stopped the Panel (`ppanel stop`; containers exited, `ppanel_pgdata` volume and images kept);
   - disabled its backup cron by moving `/etc/cron.d/ppanel-backup` to `/opt/partner-panel/ppanel-backup.cron.disabled`.
   - **DNS was not changed**: Cloudflare proxied records already point at this host.
9. **TLS**:
   - every host returns its expected status with certificate verification on;
   - Chrome reported **TLS 1.3**, subject `workeasy360.com`, issuer Google Trust Services WE1 (Cloudflare edge), valid until 2026-12-12;
   - no certificate errors and no mixed content (CSP violations 0).
10. **Production journeys**, with three demo partners created through the real admin endpoints (in-process in the api container, acting as the Super Admin with the audit reason "Post-deploy check 2026-10-23 (operator: Claude Code)"). The demo administrators' emails are `demo-{reseller,whitelabel,affiliate}.20261023@example.com`; their random passwords exist only in the operator's scratch file:
   - **draft 12/12**: each type signs in at https://partner.workeasy360.com with the admin-typed password, through `auth.` (`mode=partner`), and lands in its own workspace with "isn't active yet";
   - **active 22/22**: Reseller 13 / White-label 15 / Affiliate 12 menu entries; the other type's workspace refused; affiliate customer API 403; cross-partner 404; admin API 404 on `partner.`; the short address works; logout ends the `partner.` session; the session cookie is host-only, Secure, HttpOnly, Lax;
   - **switch 5/5**: Affiliate added to the reseller gives one account and two workspaces, Reseller stays primary, and the switcher opens Affiliate;
   - **suspended 4/4**: the signed-in affiliate sees "This partner account is suspended."; it was then reactivated;
   - **Guest Login 16/16, server-side** (in-process clients, one cookie jar per host, production settings, cache and DB): hand-off refused before a guest view; each type opens its workspace path; hand-off to `partner.` works; viewer entry; write 403 `guest_read_only`; Exit ends the view and the `partner.` session while `admin.` stays. A **browser** click-through of Guest Login needs the Super Admin's authenticator and was not done by Claude (owner step).
   - 0 console or CSP problems on any page.
11. **Monitoring** from deploy to 19:15:
   - api: 139 × 200, 2 × 401, 2 × 403, 2 × 404 (the probes);
   - no 5xx, no exception, no CSRF failure;
   - no worker, web, edge or migration error;
   - all services up and healthy; 307 MiB RAM available, 6.2 GB disk free.

## 6. Rollback

- **Cutover only** (the portal misbehaves, the data is fine):
  1. `sudo ln -sfn /opt/workeasy360/releases/2026.10.19-5f01823-web1-pp /opt/workeasy360/current`
  2. Set `W360_RELEASE=2026.10.19-5f01823-web1`: `printf %s <value> | sudo python3 .../make_env.py set W360_RELEASE --env /opt/workeasy360/env/production.env`
  3. `sudo /opt/partner-panel/bin/ppanel start`
  4. `sudo /opt/workeasy360/bin/w360 up -d`
  5. `sudo mv /opt/partner-panel/ppanel-backup.cron.disabled /etc/cron.d/ppanel-backup`

  The previous overlay routes `partner.` to the Panel again and joins its network.
- **Application only** (keep `partner.` on WorkEasy360, run the previous code): the same switch to `releases/2026.10.19-5f01823-web1`, whose overlay predates the Panel. Do not start the Panel.
- **Schema.** The two new migrations stay in either case. 0007 adds a column the previous release never reads and 0008 only fills it, so the previous code runs on the new schema (expand-only).
- **Database.** Not restored automatically. `predeploy-20261023T185455Z/db-backup` is the recovery point only for actual data corruption; once the new release has served, a restore would lose new writes. Prefer a forward fix.
- **Panel data.** Its volume, images, nightly dumps and the final dump stay on the host; `ppanel start` brings it back.

## 7. Open items

- **P2 (pre-existing): client addresses.** The DNS records are Cloudflare-proxied, but `W360_TRUSTED_PROXY_COUNT=1` and the edge trusts only loopback. Every visitor therefore reaches Django with a Cloudflare address, so rate-limit buckets and any IP allow-list see Cloudflare, not the visitor. The fix is in the runbook: list Cloudflare's ranges in `W360_EDGE_TRUSTED_PROXIES` and set `W360_TRUSTED_PROXY_COUNT=2`. Owner decision; not changed here.
- **P3: Guest Login by browser in production** needs the Super Admin's authenticator. It is verified server-side in production and by browser behind the local edge.
- **P3: demo partners** (three, active; the reseller also holds Affiliate) remain in production for the owner's own checks. Suspend or terminate them from All Partners when no longer wanted.
- **P3 (pre-existing): `/opt/workeasy360/env/superadmin-initial-password`** is still on the host. Delete it once the Super Admin password has been changed.
- **P3 (audit): the console cannot end a guest view.** It can be ended only from `partner.` (Exit) or by signing out; it also expires after 30 minutes.
- **P3 (audit): the backfill records today's answer.** See the ADR-047 addendum.
- **P3: workspace addresses** are `/partner/<type>/dashboard`; `/reseller/dashboard` and the like redirect there.
- **P3: the Partner Panel** is stopped, not deleted. Remove it (`ppanel down`; its volume only on purpose) once nothing more is wanted from it.
- **Not pushed.** Commit `95eafc4` and the docs commit are local on `fix/ci-run-1`.
