# ADR-071: WorkEasy360 Accounts is the home of a person's identity

- Status: proposed (2026-11-17). Branch `feat/accounts-portal`, stacked on PR #5. Not deployed.
- Context: the owner's final decisions of 17 November 2026, Decision 1: "accounts.workeasy360.com is the permanent central authentication and account management portal, similar to Zoho Accounts ... Keep app.workeasy360.com as the User Admin portal ... Reuse the existing backend, identity database, user UUIDs and authentication logic ... Preserve old app. account-related routes during the transition, using safe redirects only after their replacement flows are tested."
- Builds on: ADR-043 (hosts and the hand-off), ADR-044/066 (product sign-in, the chooser, Switch Account), ADR-067 (the sign-in's name is `accounts.`), ADR-069 (back-channel logout).
- Amends: ADR-059 (each portal signs in on its own host) - **for the customer portal only, and only while `W360_ACCOUNTS_IDENTITY` is on**.

## Context

ADR-059 moved every portal's sign-in onto its own host. Under ADR-043, the earlier chain had left people stuck on "Sign-in couldn't be completed": the state cookie expired, the 60-second code lapsed, a second tab spent it, or something dropped a step. Since then:

- the customer's sign-in, sign-up, verification, password reset and account pages are all on `app.`;
- `accounts.` serves only the product sign-in.

The owner now wants Accounts to be what Zoho Accounts is: the one place a person signs in and manages their account. User Admin (`app.`) remains the organization administration.

## Decision

1. **One switch, `W360_ACCOUNTS_IDENTITY` (`off` | `on`)**, read by the API and the web app. **Off is ADR-059 exactly**, so it is safe to deploy first. It requires the multi-portal configuration.
2. **On, Accounts hosts the customer portal's identity pages:**
   - `/login` without `?resume=` (Accounts' own sign-in), `/signup`, `/r` (referral), `/verify-email`, `/forgot-password`, `/reset-password` and `/confirm-email` (`frontend/src/lib/portals.ts`, `IDENTITY_PAGES`);
   - the new **account page** `/account`: who is signed in, their organizations (opening in User Admin), sign-in email, password, sign-in methods, sessions and devices, sign out everywhere and recent activity;
   - `/account/sign-in-preferences`: which account each product opens with (ADR-044/066).

   The product sign-in, the chooser, Switch Account, organization choice and product sign-out stay where ADR-067 put them, on Accounts. `/account` belongs to Accounts in either position of the switch.
3. **On, the customer portal signs in through Accounts** with the ADR-043 hand-off, whose server side ADR-059 kept (`/api/v1/auth/handoff`, `/handoff/redeem`, `/auth/continue`):
   - **Starting.** A signed-out `app.` page, or `app./login`, makes the proxy set a fresh binding value (32 random bytes) as `app.`'s host-only, HttpOnly state cookie (10 min). It then redirects to `accounts./login?mode=user&next=<safe app path>&state=<value>`. `next` is kept only when it is a page of `app.` (`safeNext`), otherwise the dashboard.
   - **On Accounts.** After the password, or at once if Accounts is already signed in, the hand-off is issued: `next` is checked again on the server, and a one-time code goes in the **fragment** of `app./auth/continue`. A sign-in on Accounts with no binding value goes to `/account`.
   - **Completing on `app.`.** The code is spent once against the binding cookie, and `app.` gets its own host-only session carrying the Accounts sign-in's id.
   - **Robustness, the ADR-059 complaint.** A hand-off that cannot be completed is **retried once, automatically**: `app./login` starts again, and Accounts, still signed in, hands back without asking for the password. Only a second failure within two minutes shows "Sign-in couldn't be completed", with "Sign in again".
4. **On, `app.`'s old identity addresses move** to the same path and query on Accounts with a 302. The browser keeps the `#token` fragment of a verification or reset link across it. **Emailed identity links name Accounts** (`identity.portals.identity_url`): verification, password reset, sign-in-email confirmation and the sign-in after Switch Account. Invitations still open on `app.`, because accepting one joins an organization there.
5. **Unchanged:**
   - the Admin and Partner portals' own sign-in, sign-up rules and reset (ADR-047/048/059), and the Store's (ADR-065);
   - the API: every account endpoint already answered on Accounts (`portal_surface.py`);
   - users, UUIDs, organizations, sessions storage, cookies (host-only, `__Host-`), the OIDC provider and every product.
6. **Sign-out:**
   - Signing out on Accounts ends its sign-in, and with it every portal session handed over from that sign-in (they carry its id) and, through ADR-069, the sessions of products that take back-channel logout.
   - "Sign out everywhere" on the account page ends every session of the person.
   - Signing out on `app.` alone ends `app.`'s session; the next `app.` page signs in again through Accounts without a password while Accounts is signed in.

## Consequences

- One password entry on Accounts serves User Admin and every product. Every cookie stays host-only (ADR-043): there is still no `.workeasy360.com` cookie.
- The cutover is a configuration change with a tested rollback: set the switch back to `off` and restart api and web. Links already emailed under `on` name Accounts and keep working under `off`, because Accounts' identity pages then redirect to `app.` with the same path, query and fragment.
- **Not done here:**
  - **MFA.** No second factor exists for customers. ADR-050 removed it for every portal, so "Account Security and MFA" covers security without a second factor until a new decision adds one.
  - **A dedicated profile editor.** There is no name or photo editing on Accounts; the existing profile screens stay on `app.`.
  - **Admin and Partner portals.** They do not sign in through Accounts; that needs its own decision.

## Rejected

- **A shared `.workeasy360.com` session cookie:** rejected again, for ADR-043's reasons.
- **A second sign-in implementation on Accounts:** the one login endpoint, its rate limits and lockout are reused.
- **Redirecting `app.`'s pages before the flow is tested:** the switch stays `off` until the cutover checks in `docs/universal-sso/release-runbook.md` pass.
