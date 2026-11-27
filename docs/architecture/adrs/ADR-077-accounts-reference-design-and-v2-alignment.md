# ADR-077: WorkEasy360 Accounts in the owner's reference design, and the V2/V3 alignment fixes

- Status: proposed (2026-11-27). Branch `feat/accounts-v2-alignment`, from `main` at `e5eb2a0`. Not merged, not deployed.
- Context: the owner's brief "WorkEasy360 — Complete Accounts & Unified Login Audit, Gap Analysis and Alignment" (27 November 2026), with two reference images ("Account Login" and "User Account Dashboard").
- Builds on: ADR-071 (Accounts hosts identity), ADR-072 (Accounts Control Center), ADR-075 (Accounts V3: four menus, personal two-step, closure, support), ADR-076 (two-screen chooser).
- Audit record: `docs/accounts-v3/alignment-2026-11-27.md`.

## Context

A requirement-by-requirement audit of `main` against the V3 sign-off note (which consolidates the V2 personal-account note) found the portal and universal login largely implemented and tested. It also found:

- one **incorrect** security behaviour: with two-step verification on, changing the sign-in email asked only for the password, while closure and turning two-step off ask for the code;
- one **missing** notice: a signed-in password change mailed nobody (a reset does);
- **incorrect wording**: Close account said "Permanently close" and "ends your memberships", while ADR-075 keeps memberships and makes reopening a support request;
- a **gap in callback validation**: the automatic sign-in setup compared callback addresses by host only, so another port on the same host passed;
- the chooser's "Log in to another account" / "Create new account" did not clear the previous person's browser state, as Switch Account does (V3 7.1);
- sign-up titled "Create Organization Account" although an organization is optional (V3 5.1).

The two reference images ask for a split-screen Accounts login and a dashboard-style landing. The landing image shows a fifth "Home" menu, an AI Assistant button, a notification bell and a verified mobile number. None of these exist as approved Accounts functionality.

## Decision

1. **Accounts login** (`AccountsLogin.tsx`): the `auth` host's sign-in and product sign-ins use the reference layout:
   - the Admin/Partner split frame (`AuthSplitFrame`, now with a `badge` slot) and an Accounts story naming the four menus;
   - **Log In | Sign Up** as two pages, where the current one is not a link (so `?resume=` is never dropped);
   - Remember me (the existing `remember` login field, as on the Admin, Partner and User Admin sign-ins: ticked = the remember window, unticked = a browser-session cookie), Forgot Password and Create WorkEasy360 Account;
   - One Account / Secure Access / All Products, and the Accounts address.

   Behaviour is unchanged: the same login endpoint, two-step step, resume and hand-off. A partner-branded sign-in (`?brand=`) keeps the plain page. Sign-up on the `auth` host uses the same frame.
2. **My Profile in the dashboard layout** (`CenterShell.tsx`, `ProfileOverview.tsx`):
   - a deep green sidebar with exactly the four menus and a Help & Support card;
   - a top bar with Help and an account menu holding Switch account and Sign out;
   - on My Profile: a greeting, the person's card (with Edit Profile), the four sections as cards, Account Security at a glance and Recent Security Activity, then the editable details.

   **"Home" is not added:** My Profile stays the first of four menus and carries the overview (V3 04). **Not drawn, because they do not exist:**
   - the mobile number (no SMS provider; V3 8.2 forbids implying phone sign-in);
   - the bell (no personal notification feed);
   - the AI Assistant (organization-scoped and credit-metered; V3 10.3 keeps account security independent of it).

   Password "last changed" is shown only when the recent activity records it, never invented.
3. **Email change behind two-step:** `request_email_change` takes `code`. When two-step is on, a current code or an unused recovery code is required, spent in the request's transaction. A wrong code spends the `two_step_daily` budget and counts toward the hourly notice, exactly as closure does. The form asks for the code when the caller knows two-step is on, and otherwise once the server asks.
4. **Password-change notice:** the new template `platform.account.password_changed_signed_in`, sent by `account_security.password_changed_notice` after a successful signed-in change. It is a security notice, so no preference turns it off.
5. **Closure wording** now matches ADR-075: memberships stay on record, and reopening is a support request.
6. **Callback origin:** `_address_problem` compares the scheme, the host and the effective port.
7. **Chooser:** `forgetAccountState()` runs before navigating for `switch_account` and `create_account`.
8. **Sign-up wording:** "Create WorkEasy360 Account". The company name is optional, and invitees are pointed to their invitation link.

## Consequences

- No migration and no new endpoint. The one API change is additive: `POST /api/v1/auth/email` accepts `code`, and requires it only for accounts with two-step on. Those accounts' old clients now receive `400 two_step_code_invalid` until they send it. The User Admin form handles this by revealing the field.
- The portal is still visible only with `W360_ACCOUNTS_IDENTITY` on (production: off). This ADR changes nothing about that release gate.
- Not done here, recorded in the audit:
  - profile push to products;
  - distinct `product_expired` / `temporarily_unavailable` outcomes at `/authorize`;
  - carrying `resume` through sign-up and forgot-password;
  - the inert `product_news` toggle;
  - the publish gate for an awaiting automatic setup;
  - product-side back-channel receivers and `backchannel_logout_uri` in the seed manifests.

## Amendment (2026-11-27, later): the landing matches the reference exactly

The owner asked for the signed-in page to look exactly like the reference image, and chose how its extras behave. This replaces the "Home is not added" and "Not drawn" points above:

- **Home** is the first menu and the page `/account` opens. It carries the overview: greeting, the person's card, the four sections as cards, Account Security and Recent Security Activity. **My Profile** moves to `/account/profile`, with the WorkEasy360 ID and the editable details. Home's "Edit Profile" opens `/account/profile?edit=1`. Home is an overview of the four sections, not a fifth section with settings of its own.
- **AI Assistant** (top bar): guided answers to common account questions, each linking to the page that does it (`TopbarMenus.tsx`, `ASSISTANT_TOPICS`). No AI model is called, and nothing leaves the page. A real assistant can replace the answers later.
- **The bell**: this account's own recent security activity (`/api/v1/auth/activity`, the same feed as Settings). The dot marks activity newer than the last time the bell was opened in this browser (localStorage, wrapped in try/catch). Without storage, it marks activity of the last seven days.
- **Mobile**: shown as "Not added - coming soon" on the card and in Account Security. No phone number is stored and nothing claims one is verified (V3 8.2 still holds).
- No backend or schema change.
