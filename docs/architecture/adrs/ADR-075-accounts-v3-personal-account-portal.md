# ADR-075: Accounts V3, the four-menu personal account portal

- Status: proposed (2026-11-20). Branch `feat/accounts-v3`, stacked on `feat/universal-login-complete` (PR #14). Not merged, not deployed.
- Context: "WorkEasy360 Accounts & Universal Product Login - Final Update and Development Sign-off Note, Version 3" (owner, 19 November 2026), and the gap audit in `docs/accounts-v3/status.md`.
- Builds on: ADR-066 (email change, Switch Account), ADR-067 (the `accounts.` name), ADR-069 (back-channel logout), ADR-071 (Accounts hosts identity), ADR-072 (Accounts Control Center), ADR-074 (chooser before access).
- **Amends ADR-050** (second factor removed): a person may again turn on two-step verification for their own account. Everything else ADR-050 removed stays removed.
- Amends ADR-072: the Control Center's menus become V3's four, and the profile is no longer name-only.

## Context

The V3 note keeps the Universal Login journey of ADR-074 and asks the Accounts portal for exactly four menus: My Profile, Login & Security, Devices & Sessions and Settings. The audit (`docs/accounts-v3/status.md`) found most of the session, password and email machinery already in place and tested. These pieces were missing:

- **Profile:** photo, country, language and time zone.
- **Email change:** status, resend and cancel for a pending change.
- **Two-step verification:** removed by ADR-050.
- **Settings:** personal notification choices, account closure and signed-out account support.
- **Menus:** four instead of eight. Today the Control Center also has My Products, Organizations and Overview, which V3 forbids.
- **Revocation:**
  - userinfo kept answering for up to 15 minutes after a sign-out.
  - A code minted in the moment before a revocation could still be redeemed.
- **"Use another account" on someone else's organization's launch:** a dead end.

## Decision

1. **Four menus.** On the Accounts host, with `W360_ACCOUNTS_IDENTITY` on:
   - `/account` is **My Profile** and opens first;
   - `/account/security` is **Login & Security**;
   - `/account/sessions` is **Devices & Sessions**: sessions, the products signed in to, and login history;
   - `/account/settings` is **Settings**: notifications, recent security activity, links to the person's organizations and their billing, closure, and Help & Support.

   The old pages redirect to their new homes. The backend endpoints of ADR-072 stay; only the menus change. Switch off (production today), `/account` is ADR-071's page, as before.
2. **Profile** (`identity.0011`): `User.country` (ISO 3166-1), `language` (`contracts.values.languages`), `time_zone` (IANA) and `closed_at`, plus `ProfilePhoto`.
   - **The photo:**
     - at most 256 KB;
     - PNG, JPEG or WebP, checked by its bytes;
     - stored in the database, because a person has no tenant for the file store;
     - served at `/api/v1/public/profile-photos/<token>` with `nosniff` and a sandbox CSP. The 256-bit token changes on every upload.
   - **Products read the profile; nothing is pushed.** The `profile` scope now also releases `picture`, `locale`, `zoneinfo` and `updated_at`. Products see the new values at their next sign-in, and userinfo has them at once.
   - **Personal only.** Language and time zone never change an organization's reports, invoices or billing.
3. **Pending email change.** `auth/email/pending` (GET/DELETE) and `.../resend` act on the latest unused change link.
   - When the new address belongs to another account, a link is still recorded (never mailed, and refused at confirmation while the address is taken). So "pending" reads the same either way: it does not reveal whether the address has an account.
   - The old address keeps working until the confirmation.
4. **Two-step verification, personal and optional** (`identity.two_step`, `TwoStepMethod`, `TwoStepRecoveryCode`, `TwoStepChallenge`):
   - **Algorithm:** TOTP (RFC 6238, standard library, tested against the RFC vectors) with ten single-use recovery codes.
   - **Storage:** the secret is Fernet-encrypted and the codes are HMAC-keyed, both under the new `W360_TWO_STEP_KEY`.
     - The key is not the removed `W360_MFA_ENCRYPTION_KEY`, and the table is not the dormant `AuthFactor`. So nothing enrolled before ADR-050 comes back.
     - Without the key, setup answers 503, and an account that has two-step on fails closed at sign-in.
   - **Setup:** password, then a code from the app confirms it. The recovery codes are shown once.
   - **Replacing the app, new codes, turning off:** password and a current code or a recovery code.
   - **Sessions and notices:** turning on, replacing and turning off end the person's other sessions (and their products', by back-channel) and mail the verified address.
   - **Sign-in:**
     - Every password sign-in of such an account answers `401 two_step_required` with a challenge: 5 minutes, 5 attempts, voided by a password reset or "sign out everywhere".
     - `auth/login/two-step` completes it.
     - Each time step is accepted once.
     - A recovery code used to sign in is reported by email.
   - **What stays removed (ADR-050):**
     - organization-required two-step;
     - step-up for dangerous actions;
     - the `aal` claim;
     - the old routes and names. `tests/security/test_no_second_factor.py` still guards these.
5. **Notification choices** (`identity.preferences`, `PersonalPreference`):
   - security and recovery notices are required and locked on;
   - new-sign-in alerts are on by default (mailed for a device and network not seen in 90 days, never on a first sign-in);
   - product news is off by default.
   - Organization console alerts stay per membership in User Admin.
6. **Account closure** (`administration.account_security.close_account`):
   - **Reconfirmed:** the password, the code when two-step is on, and the typed word `CLOSE`.
   - **Refused** for the only owner of an organization (named, from the access projection) and for staff.
   - **Effect:** the account is disabled with `closed_at`, and every session ends, the products' included.
   - **Not touched:** memberships, subscriptions and organization records stay, under the existing retention (`ACCOUNT_EVENT_RETENTION_DAYS`). Closure, membership removal, organization closure and cancellation stay separate operations. Reopening is a support request.
7. **Central account-security support** (`AccountSupportCase`):
   - **Intake:** `POST public/account-support`, open signed out. It gives the same answer for any address, is throttled per address and per inbox, and sends a receipt mail.
   - **Who sees it:** listed for staff with `platform.staff_user.view`, changed with `.manage`. It never goes to a partner or an organization, and a case grants nothing.
8. **Revocation on the next request:**
   - **Access tokens** carry the account's session epoch (`w360_sep`) and, for every product, the WorkEasy360 sign-in that authorized (`w360_sin`, a private claim). `sid` stays an ID-token claim for back-channel products only (ADR-069).
   - **userinfo** refuses a token whose epoch moved or whose sign-in ended, or of an inactive account.
   - **The token endpoint** refuses a code whose sign-in has no live session. Such a code would otherwise open a product session that no logout covers.
9. **Your own organization after "Use another account"** (`ecosystem.0012`, `PendingAuthorization.refused_organization_id`):
   - **The trigger:** at resume, a person who cannot open the organization the product named (`membership_inactive`), but has organizations of their own.
   - **What they see:** the organization chooser, with nothing of the named organization shown.
   - **The restart:** the product would refuse a code for another organization than it asked for. So the chosen one is checked as a membership, recorded as this sign-in's answer, and the product's own sign-in restarts at its registered `launch_url?organization=<chosen>`.
   - **No organizations of their own:** the refusal is delivered as before.

## Consequences

- **New migrations, all additive:**
  - `identity.0011_accounts_v3`: five new tables, four new nullable or defaulted `User` columns, and the event choices;
  - `ecosystem.0012_pending_refused_organization`: one nullable column.
- **New optional production secret:** `W360_TWO_STEP_KEY`. Without it, two-step is shown as "not available". With it, it cannot be rotated without everyone setting up their app again.
- **New dependency:** `segno` (pure Python, no dependencies) for the setup QR code.
- **Products need no change.** Back-channel logout is unchanged. The new claims are additive and scope-gated. The organization restart uses the launch URL every product already registered.
- **What revocation still cannot reach:** a product without back-channel logout keeps its own session until it next asks userinfo or re-authenticates. That is unchanged and documented in the runbook.

## Rejected

- **Reviving `AuthFactor` and the old MFA key:** that would bring back factors enrolled before ADR-050 that their owners may no longer hold.
- **Pushing profile changes to products:** it needs a new product-side receiver in every product (a connector release). Claims at sign-in plus userinfo are standard OIDC and need nothing new.
- **Deleting a closed account's data at once:** the platform has no person-erasure policy beyond event retention. Inventing one here would destroy organization history V3 says to keep.
- **A mobile number field:** there is no SMS provider. V3 8.2 says not to imply phone sign-in that does not exist.

## Review findings fixed before merge (20 November)

An automated, read-only security review of `bfa271a..` (an agent separate from the author, not a human reviewer) found two medium and four low issues. All six are fixed, and each has a regression test in `tests/accounts_v3/test_review_fixes.py`:

| Finding | Fix |
|---|---|
| **M1:** a wrong password on two-step setup, renewal, turning off or closure was rolled back with the refusal, so it never counted toward the lockout | `two_step.check_password_counted` runs before any transaction, and a locked account is refused |
| **M2:** closure counted a closed or disabled co-owner as an owner; two co-owners closing at once could both pass | Only active co-owners count, and the owner rows are locked in a fixed order inside the closing transaction |
| **L1:** wrong sign-in codes had no daily bound and no signal | A per-person daily budget (`two_step_login_daily_user`, 30). Each wrong code goes on the person's trail; the fifth in an hour mails them |
| **L2:** an old verification link could sign in an account that has two-step on | `establish_session` refuses such an account unless the code was checked, verification no longer signs it in, and invitation activation spends the signup's links |
| **L3:** the organization restart recorded "Ask once" without the person answering in this flow | Recorded only when they answered at the chooser or signed in for this flow |
| **L4:** photo metadata (EXIF and GPS) was kept, and the cache lifetime was one day | JPEG APP1-15/COM, PNG text and eXIf chunks, and WebP EXIF/XMP are stripped; the cache lifetime is 5 minutes |

The minor finding is fixed too: an unknown support case answers 404, not 500.

A second automated review of the whole stack (#4 to #15, also not a human) found two issues in this ADR's code. Both are fixed, each with regression tests that fail without the fix:

| Finding | Fix |
|---|---|
| **M1:** only back-channel products got the sign-in in their access token. "Sign out this device", "sign out other devices" and a two-step change move no epoch, so for every other product userinfo kept answering that sign-in's token for up to 15 minutes | Every access token names its sign-in (`w360_sin`); userinfo refuses it once that sign-in has no live session. Tests: `tests/universal_sso/test_v3_own_organization_and_revocation.py` |
| **L1:** the code checks on account changes (replacing the authenticator, new recovery codes, turning it off) had only the 5-minute bucket, and wrong codes sent no notice | A per-person daily budget (`two_step_daily_user`, 30), and wrong codes count toward the same hourly notice as the sign-in's. They are not put on the sign-in trail. Tests: `tests/accounts_v3/test_review_fixes.py` |
