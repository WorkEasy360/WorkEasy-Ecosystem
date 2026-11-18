# Unified platform, shared login and product access (brief of 13 November 2026): status

**Written for: the owner and the engineers of Workeasy360 and of each product.**

Brief: "WorkEasy360 - Unified Platform, Shared Login & Product Access System (master development prompt, updated requirements)". Branch `feat/partner-admin-panel`: commit `b57ad44`, pushed (PR #3). **Deployed on 13 November 2026 as release `2026.11.13-b57ad44`** (backup `20261113T070750Z` first; `ecosystem.0006` and `identity.0009` applied, 0 unapplied; api healthy; 0 error lines in api, web and worker logs; a read-only headless Chrome pass over `auth.../switch-account`, `app.../login?switched=1`, `app.../confirm-email` and the Store home: the expected page each time, one `h1`, 0 console errors, 0 CSP violations, 0 API 5xx). Rollback: `migrate ecosystem 0005`, then release `2026.11.12-249d8d8` (section 7). Decisions: [ADR-066](../architecture/adrs/ADR-066-same-or-different-account-switch-account-and-email-change.md). Browser check: [scripts/unified_login_check](../../scripts/unified_login_check/README.md).

The two documents the brief names as authoritative, `07_Product_Web_Mobile_Flow.txt` and `08_Credits_Wallets_System.txt`, are still not on this machine or in git (as on 9 and 12 November, ADR-060/065). Nothing here invents a rule they would decide; the gaps are listed in section 5.

## 1. What already existed (audit, Phase 1 of the brief)

Most of the brief was built in earlier rounds and is live. It was inspected, not rebuilt:

| Brief | Already in place | Where |
|---|---|---|
| 04 One identity, many organizations, roles per organization | One `identity.User` per person; memberships per organization; roles per membership; the tenant is the URL path, re-checked on every request (`TenancyMiddleware`) | ADR-043, phases 1-4 |
| 06 SSO | OIDC authorization code + PKCE (S256, required for every client); identity provider on `auth.`; host-only `__Host-` cookies, no parent-domain cookie; exact redirect-URI matching; tokens never in a query string (codes in the redirect, one-time links in the fragment); RP-initiated logout with a confirmation page; `prompt` none/login/consent/select_account; `auth_time` | ADR-043, ADR-044 |
| 09 Product access verification | Membership, organization status and access policy, entitlement (with the trial's end time), seat and integration readiness are checked at the identity provider before any code; each product re-checks the entitlement for the organization and the person within 15 minutes and refuses with `workeasy360_access_ended` | ADR-044 items 4, 6, 11 |
| 16 Direct product URLs | An SSO-only product starts the sign-in itself on a direct visit; a person in one organization is not asked which | ADR-044 items 15, 16 |
| 07 Switch organization | `/organizations/select` and the top-bar organization switcher; nothing about the organization is trusted from the client | Phase 4, ADR-064 |
| 08, 11 Several subscriptions, User Admin | Per-product plans, periods and seats under one organization; User Admin's seven menus (dashboard, My Products, billing, wallets, team) | ADR-056, ADR-064 |
| 12 Store | `store.workeasy360.com`: one-product checkout under a chosen organization, Platform Wallet split, Recharge, My Account's pass into User Admin; no separate Store user table | ADR-065 |
| 13 Three wallets | `platform` (organization or partner), `ai_credits`, `consumption` (organization only); owner checked on every entry; unique idempotency keys on entries, holds and usage events | ADR-060, ADR-064, ADR-065 |
| 14, 15 Super Admin, Partner Admin | Host-scoped consoles; a customer session opens neither (`PortalSurfaceMiddleware` 404s the admin and partner APIs on other hosts; staff and partner permissions are separate); partner KYC before activation; Login as Guest audited, visible and exitable | ADR-059, ADR-061, ADR-062, ADR-063 |
| 5.6 Account security | Change password (current one required, other sessions end), forgotten-password reset, sessions list with "sign out" and "sign out all others", recent account activity | Phase 8 extension, Phase 9 |

What was missing against this brief:

1. The "same account or a different one" question (section 5, **mandatory**). The access chooser existed, but since 13 October the default was Automatic, so a person who never chose was never asked. It also had no "Create New Account".
2. Changing the sign-in email (5.6).
3. Switch Organization and Switch Account in the header account menu (19). There was also no way to end the central sign-in from a portal: since ADR-059 a portal's sign-out leaves the `auth.` session alive, so the next product would open as the previous person.
4. Clearing the previous person's browser state on sign-out (5.3.7-8, test 21).
5. Native mobile sign-in (17), an HR product, and the `books.` host. See section 5.

## 2. What changed (ADR-066)

- **Ask once is the default.** The first time a product is opened in a Workeasy360 sign-in, the chooser shows. After that, the same sign-in opens the product with no question: no question on internal pages, refreshes or later launches. A new sign-in asks again. People who stored a choice keep it. The question always comes after every access check, so a refusal is never hidden behind it. Signing in on the central page for the request counts as the answer. `prompt=select_account` always asks.
- **The chooser reads the brief's way:**
  - "Opening EasyBook", then **Welcome to WorkEasy360** and "How would you like to continue?".
  - **Currently signed in as**: the name, email and organization.
  - **Continue with Same Account** (primary), **Use Different Account**, and **Create New Account** (only while sign-up is open).
  - The product's own sign-in where it has one, "Don't ask me again for EasyBook", Cancel and Sign-in preferences.
- **Create New Account** signs out first and spends the request. It then opens the Store's sign-up, which returns to the product's Store page (`next=/store/product/<slug>`), or the plain sign-up when the Store does not sell the product. No entitlement is granted before a real purchase.
- **Switch Account** (User Admin's account menu, and any account portal) signs this portal out *and* the central sign-in. It goes through a 120-second signed hand-over in the URL fragment, which can only end the same person's `auth.` session. The browser then lands on that portal's sign-in page with "You're signed out. Sign in with the account you want to use."
- **The account menu**: My Profile, Switch Organization, Switch Account, Account & Security, the consoles the person holds, Logout. Its header names the current organization and role.
- **Leaving an account forgets it in the browser.** Every sign-out and switch removes the person-bound browser storage: recent admin records, a cached partner list, carts, the Store's chosen organization and return pages, notifications seen. Layout choices are kept, and so is what a visitor brought before signing in. The shells then load the sign-in page afresh, so nothing stays in memory or in Back.
- **Change sign-in email** (Account & Security, in a popup):
  - The current password is required.
  - A one-time link goes to the new address. Nothing changes until it is used, and a click on the link's page confirms (a mail scanner changes nothing).
  - The previous address is told, with the new one masked.
  - Other outstanding links are spent.
  - It is the same identity: same id, password, sessions, organizations, products and wallets. The products know people by `sub`.
- **Account & Security** also lists the **sign-in methods**: the WorkEasy360 password, and products through single sign-on with a link to the preference. Two-step verification stays off by the owner's decision (ADR-050).
- **One accessibility fix found by the browser check, live since 12 November:** the User Admin search button's accessible name did not contain its visible words (WCAG 2.5.3).

## 3. Results

Only what actually ran, 13 November 2026.

| Check | Result |
|---|---|
| Backend: new suites (`tests/universal_sso/test_ask_once.py`, `tests/unified_login/`) | **41 / 41** (Ask once 13; Switch Account 13; email change 15) |
| Backend: the suites the change touches (architecture, portals, phase9, extension, universal_sso, unified_login, partner_center, phase12, notifications, ecosystem, identity) | First run **1,439 / 1,441**: two architecture guards wanting a reviewed declaration (the migrations' `overlap_safety`; `/api/v1/auth/email` on the list of Workeasy360 endpoints a password may go to). Both added; those files re-run **34 / 34**. 14 older tests pinned the Automatic default or the old list of choices and were updated to the new rule (each still tests its subject) |
| Backend: the whole suite (bare `pytest`) | **5,606 / 5,609** in 1 h 46 min. The 3 were this change's own, all fixed: an architecture guard listing the frontend's top-level routes (the two new pages added with their reason); the authorization endpoint's query budget (27 against 25, because `authorize` recorded the first entry even for Automatic - now only `resume`, where an account is actually chosen, records it); and an integration test that signs in with `force_login` (no sign-in time) about organization substitution, now set to Automatic so it tests its subject. Those suites re-run (universal_sso, unified_login, integration, architecture, the query budgets): **820 / 821**, the one a webhook-replay concurrency test this work does not touch, which passed in the full run and **3 / 3** alone |
| Frontend: vitest, whole suite | **1,706 / 1,706** (97 files) |
| Real browser: production build, every host behind the edge stand-in, real backend, scratch database | **67 / 67**: the five journeys of section 2; one `h1`, axe WCAG 2.1 A/AA clean and no sideways scroll at 390 and 320 px on every audited page (chooser, Store sign-up, sign-in after a switch, Account & Security, confirmation link, sign-in preferences); 0 CSP violations, 0 console errors, 0 API 5xx |
| Static | `ruff check` and `ruff format --check` clean (927 files); import contracts 18 / 18; `mypy` (CI scope) clean; `bandit -ll` no issues; `makemigrations --check` no changes; `eslint` clean on every changed file; `tsc` clean for `src` (the only errors are stale generated files in the dev server's `.next/types`) |

The browser run found, and the harness was fixed for, two harness bugs (a button's label runs into its hint; Chrome asked the fake product host for a favicon) and one real defect (the search button above).

## 4. The brief, part by part

**Completed and tested** means implemented and verified by the tests named. **Existing** means built and verified in an earlier round and unchanged here.

| Brief | Requirement | Status | Evidence |
|---|---|---|---|
| 02 | Portals use central records; customer login never grants Admin or Partner | Existing | `tests/portals/test_hosts_and_surface.py`, `tests/security/test_commercial_security.py` |
| 03 | Direct product URLs, unified sign-in, organization, entitlement, roles, seats | Existing + Ask once on first entry | `tests/universal_sso/test_one_login_guards.py`, `test_ask_once.py`; browser journey 1 |
| 04 | One identity, many organizations, no duplicate account per product | Existing | `tests/universal_sso/test_one_login_guards.py` (one person model, one password endpoint) |
| 5.1 | Login selection screen | **Completed and tested** | `ProductAccessChooser.tsx`; `signin.test.tsx`; browser journey 1 (screenshots `chooser-1440`, `chooser-390`) |
| 5.2 | Continue with Same Account: no password, organization, entitlement, role, seat, then the product | **Completed and tested** | `test_ask_once.py`, `test_chooser.py`; browser journey 1 |
| 5.3 | Use Different Account: secure sign-in, nothing of the previous account kept, switch again | **Completed and tested** | `test_chooser.py` (switch account), browser journey 2; browser storage forgotten (`lib/accountState.ts`, `accountState.test.ts`, journey 4) |
| 5.4 | Create New Account: no duplicate identity, organization or invitation, return to the purchase route, no paid entitlement before activation | **Completed and tested** | `test_ask_once.py` (signs out, request spent, Store page or plain sign-up, refused while sign-up is closed); browser journey 3. Uniqueness and "already have an account": existing signup (`tests/phase9/test_self_service.py`) |
| 5.5 | Shown on first entry, on Switch Account, when the product asks; never on every page, refresh or right after a choice | **Completed and tested** | `test_ask_once.py` (first entry, later entries, per product, new sign-in, bound to person and sign-in, `select_account`, `prompt=none`) |
| 5.6 | Change password, change login email with verification, reset, authentication methods, sessions | **Completed and tested** (email, methods section); password, reset and sessions existing | `tests/unified_login/test_email_change.py`; `change-email.test.tsx`; browser journey 5 |
| 06 | Standards-based SSO, CSRF/XSS/login-CSRF/fixation defences, safe redirects, logout, account switching | Existing + Switch Account | `tests/universal_sso/*`, `tests/unified_login/test_switch_account.py` (CSRF, forged and expired hand-over, someone else's session kept, host surfaces) |
| 07 | One organization record; Switch Organization re-validates everything | Existing; menu entry added | `Shell.test.tsx`; tenant from the URL path (`tests/isolation/*`) |
| 08, 09, 10 | Several subscriptions, access outcomes, roles and seats on the backend | Existing | `tests/universal_sso/test_authorize.py`, `test_entitlement_per_person.py`, `tests/portals/test_entitlement_end.py` |
| 11 | User Admin areas incl. Account & Security with Switch Account / Organization / Logout | **Completed and tested** (menu); the rest existing | `Shell.test.tsx`; browser journey 4 |
| 12 | Store: central login, organization, checkout, wallet and recharge | Existing (ADR-065); Store checkout shows "Signed in as / Not you?" | `tests/store_domain/*` |
| 13 | Three wallets, owner rules, idempotency | Existing | `tests/store_domain/test_wallet_payments.py`, `tests/user_admin_panel/test_wallets.py` |
| 14, 15 | Super Admin and Partner Admin boundaries, KYC, guest login | Existing | `tests/admin_panel`, `tests/guest_login`, `tests/partner_admin_panel` |
| 16 | Scenarios A-D | A, B, C completed and tested (browser journeys 1, 2, 4); D existing (refused before any question; the product shows plans) | `test_ask_once.py::test_a_refusal_always_comes_before_the_question` |
| 17 | Android and iOS | **Not implemented** - see section 5 | - |
| 18 | Shared records | Existing | - |
| 19 | UI: account menu, product launcher, navigation | Account menu **completed and tested**; launcher existing (My Products shows held products with their state; "Explore products" opens the Store for the rest) | `Shell.test.tsx` |
| 20 | Security list | Existing + the new flows' own tests; see section 6 | - |
| 21 | Domains | **Conflict**, not changed: see section 5 | - |

The brief's 30 test cases:

| # | Case | Covered by |
|---|---|---|
| 1 | Signup | `tests/phase9/test_self_service.py` |
| 2 | Existing login | `tests/portals`, browser journeys |
| 3 | Same Account | `test_ask_once.py`, browser 1 |
| 4 | Different Account | `test_chooser.py`, browser 2 |
| 5 | Explicit switching | `test_switch_account.py`, browser 4 |
| 6 | Credential change, reset | `test_email_change.py`, `tests/phase9`, `tests/extension` (password), browser 5 |
| 7 | Expired SSO session | `tests/universal_sso/test_reauthentication.py`, `test_sessions_and_observability.py` |
| 8 | Several organizations | `test_one_login_guards.py` (asked which; chooser shows the implied one) |
| 9 | Switching organizations | Tenant from the URL path, re-checked per request (`tests/isolation`) |
| 10, 11 | First and additional purchase | `tests/store_checkout`, `tests/store_domain` |
| 12 | No entitlement | `test_authorize.py`, `test_ask_once.py` |
| 13 | No permission | `tests/extension`, `tests/admin_panel` |
| 14 | Seat limit | `test_authorize.py` (no seat), `test_entitlement_per_person.py` |
| 15, 16 | Expiry, renewal | `tests/portals/test_entitlement_end.py`, `test_four_product_seven_day_trials.py`, billing suites |
| 17 | Direct product URL | `test_one_login_guards.py` (journey B) |
| 18 | Cross-domain redirect | `test_switch_account.py`, `tests/portals/test_handoff.py` |
| 19 | User Admin and Store consistency | `tests/store_domain/test_store_host.py` |
| 20 | Cross-organization API | `tests/isolation/*`, `test_one_login_guards.py` (another organization) |
| 21 | History and cache after switching | `accountState.test.ts`, `Shell.test.tsx`, browser 4 |
| 22 | Partner KYC | `tests/admin_panel`, `tests/partner_admin_panel` |
| 23, 24 | Customer into Super Admin / Partner Admin | `tests/portals/test_hosts_and_surface.py`, `tests/security` |
| 25 | Wallet owner isolation | `tests/store_domain/test_wallet_payments.py` |
| 26 | Duplicate payment | idempotency tests in `tests/store_domain`, `tests/store_checkout` |
| 27 | Logout and revocation | `tests/extension` (sessions), `tests/universal_sso/test_logout.py` |
| 28 | Mobile | **Not covered**: not implemented (section 5) |
| 29 | Responsive and accessible | browser check (axe, 390/320 px) |
| 30 | Regression | the whole suites in section 3 |

## 5. Not implemented, blocked, or awaiting a decision

- **Native Android and iOS sign-in (17): not implemented.** The identity provider has confidential clients only. It has no public clients, no custom-scheme or claimed-https app redirect URIs and no refresh tokens, and no native app exists in any repository. Easy CRM's Android app is a Trusted Web Activity: it runs the web sign-in in Chrome and works as it is. A native app needs a public client with PKCE (RFC 8252), an app redirect URI policy and a token-lifetime decision. **Owner decision needed:** which apps, and whether they talk to Workeasy360 directly or through each product's backend.
- **HR product: blocked.** No HR product is registered (`infra/ecosystem/products.json`; `products/hr` is a reserved empty package). Once one exists it joins through the Super Admin console with no platform code change (ADR-044 item 8).
- **Domains (21): conflict, not changed.**
  - The brief lists `books.workeasy360.com`, but EasyBook is registered and deployed as `book.workeasy360.com`.
  - Easy Help Desk (`help.`) is registered but not in the brief.
  - `crm.` and `ops.` match.
  - Moving EasyBook is a DNS, deployment and redirect-URI change across two repositories. **Owner decision needed.**
- **The products' own "Switch account" button** (inside Easy CRM, EasyBook, Easy Project, Easy Help Desk) is in their repositories. They can already send `prompt=select_account`, which always shows this chooser. Adding the button there was not part of this round.
- **The products' copy of a changed email** stays as it was until they refresh it from the ID token. Accounts match on `(issuer, sub)` (checked in Easy CRM and EasyBook), so no duplicate is created.
- **`07_` and `08_` specifications** are still missing (mobile purchase routes, wallet expiry, refunds, taxes, minimum top-ups, negative balances). Nothing was invented.

## 6. Security review and remaining risks

- **The question never decides access.** It is read after every check, and resume re-runs them all. "Entered in this sign-in" is server-side session data bound to the person and the sign-in id. A forged or copied value only causes a question (`test_the_record_answers_only_for_the_person_and_sign_in_it_was_written_for`).
- **Create New Account cannot carry the previous person's request or organization.** The request is spent, never re-parked, and the destination is built from configuration only.
- **Switch Account cannot be used to sign a stranger out.** The hand-over is minted only by the person's own CSRF-protected session. It lives 120 s, travels in the fragment, and ends the `auth.` session only when the user ids match. Every other case answers the same way.
- **Email change:**
  - The password is required, and a wrong one counts toward the lockout.
  - The answer is the same whether or not another account holds the address.
  - Link hashes only, single use, latest link only, 24 hours.
  - The old address is notified.
  - Reset links mailed to the old address die.
  - A race for the address is refused by the unique index.
  - Rate limits: 5 per person per hour, 3 per target address per hour.
- **Remaining risks:**
  - A person who chose Automatic, or "Don't ask me again", is not shown the chooser. That is their choice.
  - If a portal session ends without Switch Account (expiry, another tab's sign-out), the `auth.` session can outlive it until its own idle limit. The next product entry then still names the account on the chooser, unless that product was already entered in that sign-in.
  - Browser storage clearing covers this application's keys only. The server never trusted them.

## 7. Deployment (live since 13 November 2026)

- **Migrations:**
  - `ecosystem.0006_sign_in_ask_once` widens a check constraint.
  - `identity.0009_email_change` adds one column with a database default, plus longer choice lists.
  - Both declare why the previous release keeps working during a rolling deploy.
  - Neither changes anyone's stored data.
- **Configuration:** none required. New optional rate-limit settings: `W360_RL_EMAIL_CHANGE_USER/IP/ACCOUNT`, `W360_RL_EMAIL_CHANGE_CONFIRM_IP`, `W360_RL_SWITCH_ACCOUNT_USER/IP`. Three new email templates are registered in code. No Caddy or DNS change: `/switch-account` is served by the existing web container on `auth.`, and `/confirm-email` on each account portal.
- **Rollout (done):** patch build from the live `249d8d8` images (dependencies unchanged), backup `20261113T070750Z`, the migrate task applied both migrations, `api`, `worker-critical` and `web` restarted. The local dev database has not been migrated.
- **Rollback:** switch back to the previous release and run `migrate ecosystem 0005` first. The previous release cannot read an `ask_once` row, and that migration deletes such rows (they exist only for people who picked Ask once explicitly). `identity.0009` can stay.
- **What people will notice:** on the first product opened after each sign-in, one extra screen naming the account. Automatic is one setting away.
