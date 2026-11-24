# WorkEasy360 Accounts on accounts.workeasy360.com - status

Brief: "WorkEasy360 - Rename Existing Auth Service to Accounts Portal" (owner, 16 Nov 2026). Decision record: ADR-067. Operator runbook: `deploy/single-host/README.md`, section "Accounts".

**State on 2026-11-16:**

- Committed `71e9afa` (+ `61d4130`, a test-only change), pushed to `feat/partner-admin-panel` (PR #3).
- The code is live in production since 16 November: release `2026.11.16-a3f1485` (backup `20261116T144947Z`), then `2026.11.16-fe495b7`.
- It is live at **stage off**, so nothing changed for anyone. Verified from outside: the discovery issuer is still `https://auth.workeasy360.com`, the key is unchanged, and the auth-host sign-in is titled "Sign in - WorkEasy360 Accounts".
- **No DNS was changed.** `accounts.workeasy360.com` does not resolve yet (NXDOMAIN): the owner adds its Cloudflare record, then stage a (runbook: `deploy/single-host/README.md`, "Accounts").

Owner's scope decision (16 Nov): **rename + rebrand**. Everything the auth host does moves to `accounts.`. Customer login, sign-up, password reset and the account pages stay on `app.` (ADR-059 unchanged; no cross-host hand-off).

---

## 1. The existing authentication system (audit, before any change)

Verified in the code and against the live hosts, nothing assumed.

**One deployment, not a separate service.**

- One Django API (`backend/`) and one Next.js app (`frontend/`) serve six hosts. The host chooses the UI and narrows the API (ADR-043). `auth.workeasy360.com` is the portal role `auth`, set by `W360_AUTH_ORIGIN`.
- There is no separate auth project, auth database or user store to rename.
- Edge: Caddy (`deploy/edge/Caddyfile`) behind Cloudflare (proxied; Cloudflare's edge certificates are issued by Google Trust Services). It runs on the single EC2 host (`deploy/single-host/`).

**What `auth.` served on 2026-11-16** (ADR-059 moved every portal's own sign-in to its own host):

| Kind | Pages / endpoints |
|---|---|
| Pages | `/login?resume=` (product sign-in), `/product-access` (access chooser, ADR-044/066), `/sign-out` (product sign-out confirmation), `/switch-account` (ADR-066), `/organizations/select` (org choice for a product sign-in). `/` and a `/login` with no `resume` went to `app./login`. |
| API on that host only | `integrations/oauth/authorize`, `.../resume`, `.../pending`, `.../logout*`, `auth/handoff` (kept for a hand-off in flight), `auth/switch-account/finish` |
| Issuer root | `/.well-known/openid-configuration`, `/.well-known/jwks.json` |

**Identity.**

- `identity.User` (`AUTH_USER_MODEL`): a UUID primary key, which is also the OIDC `sub`. Email is globally unique; passwords are Argon2id.
- Organizations and memberships: `platform.organizations`. Staff: `StaffGrant`. Partners: `PartnerMember`. All are checked on every request, and none reads the host.

**Account endpoints** (`/api/v1/...`, the same on every web host since ADR-059):

- `auth/login`, `auth/logout`, `auth/session`
- `auth/password` (change), `auth/email` (change sign-in email, ADR-066)
- `auth/sessions` (list and revoke), `auth/activity`, `auth/sign-in-preferences`
- `public/signup` (+ `verify`, `resend`), `public/password/forgot`, `public/password/reset`, `public/email/confirm`
- `public/invitations/*`
- Emailed links name the portal they were requested on (`identity.portals.account_url` / `request_portal`), never `auth.`.

**OIDC provider** (`platform/ecosystem`, its own code, no library):

- Authorization code + PKCE (S256 only), `state` echoed, `nonce` in the ID token, `prompt` (`none`, `login`, `select_account`, `consent`).
- Client authentication: `private_key_jwt`, `client_secret_basic` and `client_secret_post`. No refresh tokens.
- RS256 ID tokens (300 s) and access tokens (900 s; userinfo only), `kid` in the header, keys in `SigningKey` with the private half from the environment (`W360_OIDC_SIGNING_KEY_<REF>`).
- RP-initiated logout (`end_session`), and an entitlement verification endpoint.
- The issuer came from `W360_OIDC_ISSUER`, never from the request, and production required it to **equal** `W360_AUTH_ORIGIN`.
- Live discovery on 2026-11-16: `issuer` `https://auth.workeasy360.com`, every endpoint on that host, one key (`Yr2-3xEeOeH2d1F8a-AGSg`, RS256).

**Registered clients** (`infra/ecosystem/products.json`; staff-created, no dynamic registration):

| Product | Code | Redirect URI | Post-logout URI | Mode |
|---|---|---|---|---|
| Easy CRM | `easy_crm` | `https://crm.workeasy360.com/api/auth/callback/workeasy360` | `https://crm.workeasy360.com/login` | sso_only |
| EasyBook | `easybook` | `https://book.workeasy360.com/api/auth/callback/workeasy360` | `https://book.workeasy360.com/login` | sso_only |
| Easy Project | `easy_project` | `https://ops.workeasy360.com/api/auth/callback/workeasy360` | `https://ops.workeasy360.com/` | sso_only |
| Easy Help Desk | `easy_helpdesk` | `https://help.workeasy360.com/api/auth/callback/workeasy360` | `https://help.workeasy360.com/login` | sso_only |

All redirect and post-logout URIs are on the products' own hosts. **None names the auth host, so none needs re-registering.**

**The products, as relying parties** (read-only inventory of the four repositories, 2026-11-16):

- Each configures **one** issuer (`WORKEASY360_ISSUER`) and fetches `<issuer>/.well-known/openid-configuration`. Each refuses a document whose `issuer` differs, verifies `iss` by exact equality, and takes every endpoint from discovery.
- Each keys stored identities on **(issuer, sub)**.
- None hard-codes `auth.workeasy360.com` in code or deployment config (only EasyBook's tests do), and none puts the provider host in a CSP.
- Live, read-only:
  - Easy CRM's SSO start answers `sso_error=unavailable`.
  - EasyBook's SSO route does not exist in production (404).
  - `ops.` and `help.` are unreachable (Cloudflare 521 and 526).
  - Only Easy CRM has its SSO code on its default branch. **No product completed a sign-in against production on 2026-11-16.**

**Sessions and CSRF.**

- Sessions are database-backed (`platform.identity.sessions`, `UserSession` rows): 8 h idle, 30 d absolute, "Remember me" 7 d on Admin and Partner.
- Cookies: `__Host-w360_session` (HttpOnly, Secure, SameSite=Lax, host-only) and `__Host-w360_csrf` (double-submit, so readable by script).
- `CSRF_TRUSTED_ORIGINS` is exactly the web origins, and the surface middleware also refuses a cross-portal write.
- No `.workeasy360.com` cookie exists anywhere.

**Single sign-on and sign-out.**

- Product SSO is ADR-044: a per-product preference, the access chooser, and ADR-066 "Ask once".
- Product sign-out goes through `end_session`, with a confirmation page that ends the central session only if the person asks.
- Since ADR-059 a portal's sign-out ends that portal's session only (Switch Account also ends the central one). There is no back-channel logout to products: it exists only on Easy CRM's unmerged connector branch.

**Tests before the change:**

- Full backend suite on `769476a`: see section 6. The last full run before that was 5,606 of 5,609 on `b57ad44`, with its 3 fixed.
- Frontend: 1,706 of 1,706 (vitest, 16 Nov).
- The OIDC flow is covered by `platform/ecosystem/tests`, `tests/integration` (an independent reference product client), `tests/portals`, `tests/universal_sso` and `tests/unified_login`.

**Operational dependencies:** Cloudflare DNS (owner), Caddy ACME certificates, the nightly `w360-backup`, `make_env.py` for the environment, and the `w360` compose wrapper. There is no monitoring stack on the host (OPS-3).

---

## 2. Exact changes

| Area | File | Change |
|---|---|---|
| Settings | `backend/config/settings/base.py` | `W360_AUTH_LEGACY_ORIGIN` (only in multi-portal mode); `OIDC_TRANSITION_ISSUERS` (`W360_OIDC_TRANSITION_ISSUERS`): each must be the auth origin or the previous one, once, and not the canonical issuer; `IssuerMiddleware` added after `PortalSurfaceMiddleware` |
| Settings | `backend/config/settings/production.py` | The previous name must be a bare https origin on a host of its own. `ALLOWED_HOSTS` = portal hosts + store + previous name. The previous name is never in CSRF. The issuer must be `W360_AUTH_ORIGIN` **or** `W360_AUTH_LEGACY_ORIGIN`. Existing messages keep their wording, so the existing tests are unchanged. |
| Previous host | `backend/workeasy360/interfaces/api/portal_surface.py` | `legacy_auth_answer`: serve the discovery documents and `/api/v1/integrations/*` (except what a portal owns), 302 every `GET`/`HEAD` navigation to the same path and query on `W360_AUTH_ORIGIN`, 404 everything else. `accounts_legacy_host_requests_total{kind}` counts these. `IssuerMiddleware` chooses the request's issuer from the host. |
| Issuer | `backend/workeasy360/platform/ecosystem/tokens.py` | `canonical_issuer()`, `issuers()`, `issuer_for_host()`, `answering_as()`. `issuer()` returns the request's chosen issuer, otherwise the canonical one. `verify()` accepts the configured issuers. |
| Client auth | `backend/workeasy360/platform/ecosystem/clientauth.py` | The assertion audience is the token endpoint under each configured issuer. |
| Token log | `backend/workeasy360/interfaces/api/v1/ecosystem_views.py` | `ecosystem.token.issued` with `product_code` and `issuer`. |
| Links | `backend/workeasy360/platform/identity/portals.py` | `legacy_auth_origin()`, `is_legacy_auth_host()`; `platform_hosts()` includes the previous name, so the edge certificate check still says yes and no partner can claim it. |
| Product hook-up | `.../controlplane/management/commands/connect_product_sso.py` | Prints `accounts.` as the issuer to configure whenever it is already a configured issuer, so a newly connected product never has to move. |
| Edge | `deploy/edge/Caddyfile` | `auth_legacy_on` / `auth_legacy_off` site, chosen by `W360_AUTH_LEGACY_EDGE`: the previous name goes entirely to the API. Header comments updated. |
| Compose | `deploy/compose/production.yml` | The edge gets `W360_AUTH_LEGACY_EDGE` and `W360_AUTH_LEGACY_ORIGIN`. The backend already reads the whole env file. |
| Env tool | `deploy/single-host/make_env.py` | `accounts --stage off\|a\|b\|c\|d\|e` writes all seven values together. `render` now names the sign-in `accounts.<domain>` for a new deployment. |
| Env example, CI | `deploy/env/production.env.example`, `.github/workflows/ci.yml` | New variables documented; Caddyfile validated with the previous name switched on as well. |
| DNS/TLS check | `scripts/verify_portal_dns_tls.py` | Checks `accounts.` and, while kept, `auth.` (`--previous-name ""` once retired). |
| Branding | `frontend/src/features/signin/AccountsBrand.tsx` (+ `.module.css`) | "WorkEasy360 Accounts": `ACCOUNTS_NAME`, `accountsTitle()`, and the mark (logo + "Accounts") |
| Branding | `LoginScreen.tsx`, `ProductAccessChooser.tsx`, `SignOutConfirm.tsx`, `app/{login,product-access,sign-out,switch-account}/page.tsx` | The product sign-in heading on the accounts host, the mark above the chooser and sign-out cards, and page titles. ADR-066's chooser copy is kept. |
| Tests | `backend/tests/accounts_portal/` (new), `frontend/src/features/signin/accounts-brand.test.tsx` (new) | See section 6. `tests/portals/test_hosts_and_surface.py`: one docstring sentence, no assertion changed. |
| Docs | ADR-067, this file, `deploy/single-host/README.md` ("Accounts"), `docs/portals/design.md` (pointer) | |

**Not changed, on purpose:**

- No Django app label, migration, table, model, package or cookie name.
- No portal code (`auth` is still the role's name inside the code).
- No user, organization, client, key or secret, and no database migration.

---

## 3. New domain configuration and old-domain compatibility

`accounts.workeasy360.com` is `W360_AUTH_ORIGIN`. It serves everything `auth.` served (section 1): the same pages, the same API, the issuer root, and its own host-only session. Every browser link the platform builds for the central sign-in names it: the parked-request login, the organization choice, the access chooser, sign-out and Switch Account.

`auth.workeasy360.com` (`W360_AUTH_LEGACY_ORIGIN`, stages a-d):

| Request on auth. | Answer |
|---|---|
| `GET /.well-known/openid-configuration`, `/.well-known/jwks.json` | Served, as before. Same keys as on `accounts.`. `issuer` per the stage table. |
| `/api/v1/integrations/oauth/token`, `.../userinfo`, `.../entitlements/verify`, `.../events`, `.../usage/events`, `.../ai/*`, `.../rag/*`, `.../analytics/*` | Served, unchanged (server-to-server; no session read) |
| `GET`/`HEAD` `.../oauth/authorize`, `.../oauth/resume`, `.../oauth/logout`, and any page (`/login?resume=...`, `/product-access`, `/sign-out`, `/switch-account`, `/`) | **302** to `https://accounts.workeasy360.com` + the same path and query (temporary: no browser caches it past a rollback) |
| Everything else: other API calls (`auth/login`, `auth/session`, the chooser's and sign-out's data calls, any portal's API), any write to a page | 404 |

No cookie is ever set on `auth.`, and no session is read there. The web app is never reached on that name (the edge sends it all to the API).

---

## 4. OIDC issuer migration

Mechanism (ADR-067 decision 3):

- The canonical issuer is `W360_OIDC_ISSUER`.
- During the move, `W360_OIDC_TRANSITION_ISSUERS` names the other of the two sign-in origins.
- A request whose host is a configured issuer is answered as that issuer: the discovery `issuer` and endpoints, the `iss` it mints, and the client-assertion audience. Every other host is answered as the canonical issuer.
- The list is closed and checked at start-up. Issuer validation is never weakened: each product still verifies exactly its own configured issuer, and the platform verifies only its own two.

| Stage | `W360_AUTH_ORIGIN` | `W360_AUTH_LEGACY_ORIGIN` | `W360_OIDC_ISSUER` | `W360_OIDC_TRANSITION_ISSUERS` | A product on auth. gets | A product on accounts. gets |
|---|---|---|---|---|---|---|
| off | auth. | - | auth. | - | `iss` auth. | not served (400) |
| **a** (brief Stage A) | accounts. | auth. | auth. | - | `iss` auth., browser hop auth. -> accounts. | refused at discovery (fails closed) |
| **b** (Stage B) | accounts. | auth. | auth. | accounts. | `iss` auth. | `iss` accounts. |
| **c** (Stage C) | accounts. | auth. | accounts. | auth. | `iss` auth. | `iss` accounts. |
| **d** (end of window) | accounts. | auth. | accounts. | - | refused at discovery (fails closed) | `iss` accounts. |
| **e** (retired) | accounts. | - | accounts. | - | not served (400) | `iss` accounts. |

**Zero downtime.** Every stage change only adds or removes something no product is using at that moment, so no product has a sign-in refused because of a stage change. The browser hop (auth. -> accounts.) costs one redirect.

**In-flight sign-ins:**

- A parked request (resume token, 10 min) started on `auth.` before stage a is forwarded and completes on `accounts.`, because the request lives on the server, not in a cookie.
- A code issued before a stage change is redeemable after it: codes are not bound to a host, and the ID token's `iss` is chosen where the code is redeemed.

**Time limit:** stage d after 30 days with no `ecosystem.token.issued` naming `auth.`. Stage e after `auth.` traffic stops. Recorded in the runbook.

**The one thing a moving product must do itself:** move its stored identities to the new issuer in the same release (section 5). `sub` is identical on both names.

---

## 5. Connected-product migration checklist

The same for every product, in its own repository and release. Not started in any product repository: this was a provider-side change.

1. **Configuration:** `WORKEASY360_ISSUER=https://accounts.workeasy360.com` (no trailing slash). Client id, key id, private key, redirect URI and post-logout URI are **unchanged**: they are registered in Workeasy360 and on the product's own host.
2. **Identity data, same release:** re-point the rows that stored the old issuer.
   ```sql
   UPDATE <identity table> SET issuer = 'https://accounts.workeasy360.com'
    WHERE issuer = 'https://auth.workeasy360.com';
   ```
   Do this before the new configuration takes traffic. Skip it only if the product never signed anyone in under `auth.`.
3. **Restart:** discovery is cached for up to 1 hour.
4. **Verify** (brief Phase 6, steps 1-8):
   - Open the product. It goes straight to `accounts.workeasy360.com/api/v1/integrations/oauth/authorize` (no `auth.` hop).
   - Sign in, or continue as the current account (access chooser).
   - The callback is verified.
   - The **same** product user is found, not a new one.
   - Entitlement is checked: a member without the product is refused.
   - The product session works.
   - Product sign-out offers "sign out of WorkEasy360 too" on `accounts.`.
   - Switch Account in the platform makes the next product sign-in ask again.
5. **Rollback for that product:** the previous configuration and the reverse `UPDATE`, while the provider is still at stage b or c.

| Product | Repository and branch | State on 2026-11-16 | What to do |
|---|---|---|---|
| Easy CRM | `AI CRM`; SSO on `origin/main` since `1b4d83b`; deploys automatically to EC2; issuer read from SSM `/keel/production/WORKEASY360_ISSUER` | Not completing sign-ins (`sso_error=unavailable`). Its docs and `.env.example` show `https://app.workeasy360.com` as the issuer, a host that serves no discovery document. If SSM holds that value, it explains "unavailable". | Owner: read the SSM value. Set it to `https://accounts.workeasy360.com` once the provider is at stage b. Identity table `apps/accounts` `ExternalIdentity`: step 2 only if it holds rows for `auth.`. The unmerged connector branch (`feat/workeasy360-sso-connector`) takes the same setting. |
| EasyBook | `EasyBook`, branch `workeasy360-sso` (not on `main`); production Terraform sets no `WORKEASY360_*` | SSO not deployed | Configure `accounts.` from its first SSO release: no data step. `book.` vs `books.` is still an open owner decision (ADR-066). |
| Easy Project | `Project-Module`, branch `workeasy360-sso` (not on `main`, which conflicts with it) | SSO not deployed; host unreachable (521) | Configure `accounts.` from its first SSO release. **Never change its issuer later without step 2.** It matches users only by `(issuer, sub)`, never by email, so skipping step 2 creates new, empty users (`JpaFederatedIdentityStore`). |
| Easy Help Desk | `helpdesk-workeasy`, branch `workeasy360-sso` (not on `main`) | SSO not deployed; certificate error at the origin (526) | Configure `accounts.` from its first SSO release. Its `deploy/.env.example` and `backend/.env.production.example` set `WORKEASY360_ISSUER=https://app.workeasy360.com`, which is wrong (it should be `accounts.`). Fix them in that repository. |

**New integrations:** `connect_product_sso` prints `WORKEASY360_ISSUER=https://accounts.workeasy360.com` from stage b on.

---

## 6. Test results

| Run | Before (HEAD `769476a`) | After |
|---|---|---|
| Full backend suite (bare `pytest`, no path) | **5,609 passed, 0 failed** (2 h 32 min) | **5,694 passed, 0 failed** (2 h 31 min); the 85 extra are this change's |
| New `backend/tests/accounts_portal` | - | 84 passed |
| Affected suites (accounts_portal, portals, unified_login, universal_sso, platform/ecosystem, security, integration, phase12) | - | 1,617 + 4 failed on the first run. Three were architecture rules this change broke: a Host read outside `portal_surface.py`, and the word "entitlements" in it. They were fixed in the code, with no assertion changed: the issuer middleware moved into `portal_surface.py`. Re-run 145/145. The fourth is the known flaky `test_phase6_concurrency::test_concurrent_replays_of_one_dead_delivery_create_one`, which passed 6 of 7 alone and touches no host, issuer or middleware. |
| Frontend vitest | 1,706 / 1,706 | targeted 168/168; full run 1,707 / 1,711, the 4 being CPU-starvation timeouts in unrelated admin tests while two backend suites ran (110/110 alone) |
| Frontend production build (`next build`, scratch copy) | - | succeeds; `/login`, `/product-access`, `/sign-out`, `/switch-account` built |
| `caddy validate` (caddy:2, as CI): live config, Store on, accounts + previous name on | - | all three valid |
| ruff check + format, mypy, lint-imports (18 kept), bandit, eslint, tsc (src) | - | clean |
| Live DNS/TLS check (`verify_portal_dns_tls.py`, read only, 16 Nov) | - | 25/30. The 5 failures are all `accounts.`, which does not resolve; `auth.` and the four others pass |

What the new tests prove:

- **Old-domain compatibility:** discovery and keys unchanged on `auth.`; server-to-server endpoints served; every browser request forwarded with the same path and query; nothing else answered; no cookie set.
- **The new domain serves the central sign-in**, and links name it.
- **The authorization code flow with PKCE, state and nonce,** through an independent reference product, in every stage:
  - the ID token is verified by `kid`, `iss`, `aud`, `exp`, `nonce` and `typ`;
  - userinfo and entitlement verification work;
  - the same `sub` (the user's UUID) and organization appear on both names.
- **Issuer validation stays strict on both sides.** A product configured with a name the platform does not yet (or no longer) issue fails closed at discovery.
- **Client assertions** name one of our two token endpoints only.
- **Session isolation:** an old `auth.` session is never used to issue a code.
- **Invalid redirect URIs** are rendered, never followed, even after forwarding.
- **Production refuses** every unsafe configuration and accepts every stage the tool writes.
- **Rollback:** the stages round-trip, and stage off is exactly the old platform.

---

## 7. DNS and TLS requirements

1. **Cloudflare (owner):**
   - Add `accounts` exactly like `auth` today: proxied (orange cloud), with the same target as the `auth` record.
   - Leave the zone's SSL mode as it is for the other records (the runbook asks for Full (strict) with proxied records). Cloudflare's universal edge certificate covers one-level subdomains such as `accounts.`.
   - **Keep the `auth` record** until stage e.
2. **Origin certificate:**
   - Caddy obtains a Let's Encrypt certificate for `accounts.` on the first `up -d --no-deps edge` after stage a. It does this by HTTP-01 through the proxy, as it did for `store.` on 2026-11-12.
   - `auth.` keeps the certificate Caddy already holds (same site name, same storage).
3. **Check:** `python scripts/verify_portal_dns_tls.py` should show 30/30.
   - Before (16 Nov): 25/30. The five failures are all `accounts.`, which does not resolve.
   - After stage e: `--previous-name ""`.
4. **HSTS:** both names get `max-age=63072000; includeSubDomains; preload` from the edge, as every portal does.

---

## 8. Rollback

- **Stage rollback** (the usual one): `make_env.py accounts --stage <earlier>`, `w360 up -d --no-deps edge`, then `w360 up -d api worker-critical web`.
  - From a or b to off, products on `auth.` never notice.
  - Never go back past b while a product is configured with `accounts.`: it fails closed until it is configured back.
  - Tested: `test_each_stage_is_a_complete_state_so_going_back_is_the_rollback`, `test_rollback_to_before_the_move_is_exactly_the_old_platform`.
- **Code rollback** to the previous release (`2026.11.13-b57ad44`): **first** `--stage off`. The previous code refuses to start with `W360_AUTH_ORIGIN` other than the issuer, or with an extra allowed host. Then switch the release as usual.
  - No migration was added, so no database restore is ever needed for this change.
  - The ADR-066 rule still applies to any rollback past `b57ad44` (`migrate ecosystem 0005` first).
- **Backups:**
  - Before the deploy: `w360-backup` (database) and a copy of the env file, `production.env.before-accounts-<stamp>`.
  - Signing keys and client credentials are not touched: the same `SigningKey` rows, the same `W360_OIDC_SIGNING_KEY_*`, the same `IntegrationCredential` rows.

---

## 9. Remaining risks and manual steps

1. **Owner:**
   - Add the Cloudflare record `accounts`.
   - Approve the commit, push and deploy (none done).
   - Deploy at stage a; stage b can follow at once.
2. **People sign in once more** on `accounts.` the next time a product asks. A session on `auth.` is never read again; the brief accepts this.
3. **Easy CRM's real issuer value is unknown** (SSM, no access here). Check it before it is next deployed.
4. **Each moving product must do the identity data step** (section 5). Easy Project is the one where skipping it duplicates users.
5. **Help Desk's example env files are wrong** (`app.` as the issuer). Fix them in that repository.
6. **No monitoring on the host:** the stage d decision rests on the `ecosystem.token.issued` log lines. The api container keeps 5 × 50 MB of logs (`deploy/compose/production.yml`), so 30 days may not fit: check them weekly, or keep a copy (`w360 logs api | grep ecosystem.token.issued >> ...`), before stage d.
7. **Global logout is unchanged and partial:**
   - A product sign-out can end the central session.
   - A portal sign-out ends only that portal's session (ADR-059).
   - There is no back-channel logout to products on this branch. ADR-069 (PR #4, unmerged) adds it, and signs each logout token as the issuer the product is configured with during the move (ADR-069 addendum).
   - Nothing here made it worse.
8. **Not done, by scope:** moving the customer login and account pages to `accounts.` (owner chose against it); renaming internal identifiers (`auth` role, cookies, tables), which brings no safety and needs a migration.
