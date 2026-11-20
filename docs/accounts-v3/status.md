# Accounts V3: gap register and status

Spec: "WorkEasy360 Accounts & Universal Product Login - Final Update and Development Sign-off Note, Version 3" (19 Nov 2026). Decision record: ADR-075. API contract: `api-contract.md`.

Baseline audited: `feat/universal-login-complete` at `bfa271a` (PR #14). It was audited by reading the code and tests, not by inferring from the 56-check report.

## Phase 1: V3 against the code at bfa271a

Legend:

- **Tested**: implemented and tested.
- **Untested**: implemented, not tested.
- **Partial**: partly implemented.
- **Missing**: not implemented.
- **Blocked**: needs a deployment, an approval or a provider.

Evidence paths are relative to `backend/workeasy360/` or `frontend/src/`.

| V3 | Requirement | At bfa271a | Evidence | V3 work |
|---|---|---|---|---|
| 04 | Exactly four menus, My Profile first | **Missing** (8 menus: Overview, My Products, Connected Logins, Active Sessions, Login History, Organizations, Security, Settings) | `features/account/centerConfig.ts` `CENTER_SECTIONS` | Four menus; old routes redirect |
| 1.1 | Name, photo, verified-email header | **Partial**: name and badge only; no photo; badge untested | `AccountsCenter.tsx:31`, `identity/models.py` (no photo field) | Photo model + header |
| 1.2 | Edit name, photo, country, language, time zone | **Partial**: name only (`identity/account.py` `update_profile`); no Cancel | `ProfileForm.tsx` | Fields, photo, Edit/Save/Cancel |
| 8.1 | Profile authoritative; products refresh | **Partial**: pull only, `name` claim | `identity/selectors.py` `_SCOPE_CLAIMS` | `picture`, `locale`, `zoneinfo`, `updated_at` claims |
| 2.1 | Stable central user id | **Tested** | `User.id` UUID = `sub`; `ecosystem/tests/test_oidc_flow.py` | kept |
| 2.1 | Email verification | **Tested** | `tests/phase9/test_self_service.py` | kept |
| 2.1 | Mobile number and verification | **Missing** and **Blocked** (no SMS provider) | `User` has no phone; only Email/InApp channels | Not built; the UI says it is not available (V3 8.2) |
| 8.2 | Verified email change | **Tested** (12 tests) for reauthentication, atomic switch, conflicts and the old-address notice; **Missing**: pending status, resend, cancel | `administration/self_service.py`, `tests/unified_login/test_email_change.py` | pending/resend/cancel, no address oracle |
| 8.3 | Password change and forgot password | **Tested** | `identity/account.py` `change_password`; `tests/extension/test_account_sessions.py` | kept |
| 8.3 | Expiring single-use tokens | **Tested** (hash-only, single use); reset-link expiry untested | `identity/tokens.py` | kept |
| 8.3 | Rate limits | **Tested** (partly) | `control_plane_http.limited`, `base.py` | new buckets for every new write |
| 2.3 | Two-step setup, recovery, replacement | **Missing**: removed by ADR-050 (live since 24 Oct) | `docs/architecture/adrs/ADR-050*`, `tests/security/test_no_second_factor.py` | Personal TOTP and recovery codes (ADR-075) |
| 8.3 | Sessions revoked after a sensitive change | **Tested** for password change and reset; email change by design keeps sessions (ADR-066) | `tests/universal_sso/test_backchannel_logout.py` | two-step changes end the other sessions |
| 3.1 | Sessions list, device, last active, current | **Tested** | `tests/accounts_center/test_sessions_history_profile.py` | moved under Devices & Sessions |
| 3.2 | Sign out one / all others / everywhere | **Tested** | `tests/accounts_center/test_session_revoke.py`, `test_backchannel_logout.py` | kept |
| 9.2 | Next protected request rejects a revoked session | **Tested** on the platform; **Partial** for products: userinfo answered 15 min; a code minted before revoke was still redeemable | `ecosystem_views.userinfo`, `authorization.redeem_code` | epoch and sign-in (`w360_sin`, every product) checked in userinfo; sign-in check at redeem |
| 9.2 | Refresh cannot restore a revoked session | **Tested** (no refresh tokens) | `ecosystem/tests/test_oidc_flow.py` | kept |
| 4.1 | Personal notification preferences | **Missing** (only per-membership console alerts) | `notifications/preferences.py` | `PersonalPreference`; security notices locked |
| 4.2 | Recent security activity (redacted) | **Tested** | `tests/extension/test_account_sessions.py` | on Settings |
| 4.3 | Links to User Admin, subscriptions, billing | **Tested**, but on My Products, not Settings | `CenterProducts.tsx` | `auth/account/organizations` + Settings links |
| 4.4 / 10.2 | Account closure; sole owner | **Missing** | no closure code; owner cannot be removed (`organizations/services.py`) | closure with sole-owner refusal |
| 10.3 | Account-security support (signed out) | **Missing** (tickets need an organization) | `admin_panel_views.py` | `AccountSupportCase` + public form + Super Admin list |
| 10.3 | Security actions not automatable | **Tested** | CSRF/session-only; `tests/architecture/test_architecture.py` | new writes are session + CSRF |
| 5.2-5.5, 06 | Chooser before access; access decisions | **Tested** (56-check acceptance, ADR-074) | `e2e/evidence/universal-acceptance-2026-11-19/` | unchanged |
| 6.5 / 7.1 | Use another account on another person's organization's launch | **Partial**: correct refusal, but a dead end ("not an active member") | `ecosystem_views.resume`, `chooser.py` | offer own organizations, restart product with the chosen one |
| 5.4 | Deep link preserved | **Tested** for User Admin (`safeNext`) and product `state`; record paths are the product's | `scripts/universal_sso_browser_check` "14 DIRECT" | browser test with the template product |
| 7.2 | Two organizations in two tabs | **Implemented** (per-URL `/o/<slug>`, nothing global), **untested** | `tenancy/resolution.py` | browser test |
| 5.1 | Invitations do not create organizations | **Implemented**, **untested** as such | `administration/invitations.py` `accept` | backend test |
| 5.4 | Public forms, storefronts, booking pages | **Not in this repository** (products own them); the platform's own public pages are public | `proxy.ts` public paths | browser check of the platform's public pages |
| 2.7 / 6.7 | Embedded Forms and built-in AI without separate signup or key | AI: **Implemented** (platform-held keys, entitlement-gated); Forms: **not in this repository** | `intelligence/ai/credentials.py` | backend assertion for AI |
| 11 | Automatic product onboarding (ADR-074) | **Tested** (Test G) | acceptance G.1-G.12 | regression |

## What V3 changes (exact)

See ADR-075, and the commit list in the final report.

## Not done, and why

- **Mobile sign-in, SMS:** no provider is chosen. V3 8.2 forbids implying phone sign-in that does not exist.
- **Profile push to products:** it would need a receiver in every product (a connector release). Products read the profile at sign-in and from userinfo (standard OIDC).
- **Ownership transfer:** it belongs to User Admin, not Accounts (V3 03). The only owner of an organization is refused closure and pointed to support.
- **Public forms and booking pages:** these live in the product repositories (EasyBook, Forms), outside this change.

## Results (20 November)

Code complete is not tested; tested is not deploy-ready; deploy-ready is not merged; merged is not deployed; deployed is not production verified.

| Stage | State |
|---|---|
| Code complete | **Yes**: PR #15 (`feat/accounts-v3`), plus Easy CRM name refresh on AI-CRM#19 (`9be589b`) |
| Tested | **Yes**, with the limits below |
| Deploy-ready | **No**: owner gates open (below) |
| Merged | **No**: PRs #4-#15 are all open drafts |
| Deployed | **No**: production is `2026.11.18-b10530e` |
| Production verified | **No** |

**Automated tests**

- **CI on `fb35256`:** 13/13 checks green. Backend **6,209 passed, 0 failed**. Frontend lint, types, vitest and build green. CodeQL, image vulnerability scan, secret scan and dependency audit green.
- **New backend tests:**
  - `tests/accounts_v3`: 48 tests across profile, photo, claims, pending email, two-step (RFC vectors, replay, attempts, expiry, fail-closed key, recovery codes, replace and turn off), notifications, closure (only owner, member, two-step), support, boundaries and the review regressions;
  - `tests/universal_sso/test_v3_own_organization_and_revocation.py`;
  - `tests/extension/test_v3_invitation_one_organization.py`.
- **Frontend:** vitest 2,029 tests in 111 files.
- **Second review (20 November, automated, not human):** it found M1 (userinfo kept answering a token whose one sign-in had ended, for products without back-channel logout) and L1 (no daily budget or notice for wrong codes on account changes). Both are fixed, with four regression tests that fail without the fix (ADR-075, "Review findings").

**Browser acceptance** (real platform, edge, CRM, template products, Chrome; harness `e2e/run_universal_acceptance.py` with `W360_ULC_V3=1`)

- **The 56 Universal Login checks:** 56/56 on `04a5b38` and on `fb35256` (same runtime code).
  - In the last run (`evidence/accounts-v3-acceptance-2026-11-20-final`), B.4 failed once: the page was not loaded in time on an overloaded machine. It passed in both earlier runs.
- **V3 checks H.1-H.22:** 21 of 23 passed in the last run.
- **H.15 (sign out one device) and H.17 (sign out everywhere):**
  - The central session ended at once (`accounts_ended=true`).
  - Easy CRM's own log shows each back-channel logout received and its sessions ended (`scope=session sessions_ended=1`, then `scope=everywhere sessions_ended=1 sessions_revoked=2`).
  - Both arrived about 2 minutes later, because the worker was lagging on the loaded machine. That is past the harness's 120 s window.
  - Product-side revocation is therefore **confirmed by the product's log, not by the browser check**.

**Gaps that stay open**

- **Mobile sign-in (SMS):** no provider.
- **Profile push:** products refresh at their next sign-in; there is no push.
- **Ownership transfer:** User Admin's job.
- **Public forms and booking pages:** in product repositories.
- **Products without back-channel logout:** revocation reaches them only when they call userinfo or sign in again.
