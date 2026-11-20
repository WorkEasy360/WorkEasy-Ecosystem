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
| 9.2 | Next protected request rejects a revoked session | **Tested** on the platform; **Partial** for products: userinfo answered 15 min; a code minted before revoke was still redeemable | `ecosystem_views.userinfo`, `authorization.redeem_code` | epoch/sid check in userinfo; sign-in check at redeem |
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
