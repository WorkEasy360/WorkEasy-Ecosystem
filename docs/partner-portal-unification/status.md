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

## 4. Gates (final tree)

<!-- filled in below as each gate ran -->

## 5. Deployment

<!-- filled in after the deployment -->

## 6. Rollback

- **Cutover only** (the portal misbehaves, the data is fine): `sudo ln -sfn /opt/workeasy360/releases/2026.10.19-5f01823-web1-pp /opt/workeasy360/current`, set `W360_RELEASE=2026.10.19-5f01823-web1` (`make_env.py set`), `sudo /opt/partner-panel/bin/ppanel start`, `sudo /opt/workeasy360/bin/w360 up -d` - the previous overlay routes `partner.` to the Panel again and joins its network. The two new migrations stay: 0007 adds a column the previous release never reads and 0008 only fills it, so the previous code runs on the new schema (expand-only).
- **Application** (the new release fails health): the same release switch, without starting the Panel if `partner.` should stay on WorkEasy360's previous partner portal.
- **Database**: not restored automatically. The backup taken before migrating is the recovery point only for actual data corruption; once the new release has served, new writes would be lost by a restore - prefer a forward fix.
- **Panel data**: its volume, images, nightly dumps and a final dump taken at cutover stay on the host; `ppanel start` brings it back.
