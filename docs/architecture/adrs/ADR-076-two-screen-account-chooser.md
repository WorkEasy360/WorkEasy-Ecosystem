# ADR-076: The account chooser in two screens

- Status: proposed (2026-11-20). Branch `feat/login-chooser-two-screens`, on `feat/accounts-v3` (PR #15). Not deployed.
- Context: the owner's brief "Universal Login UX Final Update: Simplified Account Switching, Signup & Future Product Integration" (20 November 2026).
- Builds on: ADR-044 (sign-in preferences and the access chooser), ADR-066 (Ask once, Create New Account), ADR-074 (the chooser before the access answer), ADR-075 (Use another account restarts in the new person's own organization).
- Changes: the frontend page `/product-access` only. No endpoint, rule, preference, migration or product changes.

## Context

The chooser showed every choice at once:

- Continue as;
- Use another account;
- Create New Account;
- the product's own sign-in;
- remember, Cancel and preferences.

The owner wants one simple screen first. Its question is "are you this person?", and nothing else competes with Continue.

## Decision

1. **Screen 1, "Continue to <product>":**
   - The page shows the account that is signed in (name, email and organization) under "Signed in as".
   - It offers exactly two choices: **Continue as <name>** (primary) and **Use another account**.
   - "Don't ask me again for <product>", Cancel and Sign-in preferences stay as small controls below them.
2. **Screen 2, "Choose how to continue":**
   - Two choices: **Log in to another account** and **Create new account**, plus **Back**.
   - Create new account is shown disabled, with the reason, while sign-up is closed.
   - The product's own sign-in, where the product has one, moves here as a small link. It is not offered on screen 1.
3. **Moving between the screens changes nothing:**
   - Use another account and Back are page state. Nothing is signed out, switched or sent until one of the real choices is pressed.
   - The choices that end a session are Continue, Log in to another account, Create new account, the product's sign-in and Cancel.
   - Screen 2 is a browser-history entry (`?view=another`), so the browser's Back returns to screen 1.
   - Back or Cancel therefore never leaves a half-switched identity.
4. **The server is unchanged:**
   - Log in to another account is the existing `switch_account` answer: signed out first, then `/login?resume=`, then the ADR-075 own-organization restart.
   - Create new account is the existing `create_account` answer: signed out first, then sign-up with the product's Store page as `next`.
   - A brand-new account holds no organization unless it names one, and holds no entitlement (ADR-066).
5. **"Don't ask me again" is sent only with Continue or the product's sign-in,** the only answers the server remembers. A remember ticked on screen 1 never travels with another account's choice.
6. **An ended session:** if the WorkEasy360 session ended while the page was open, a choice answered `401` goes to sign-in with the same `resume`. The page load already did this. Nothing continues without a password.

The global default stays ADR-066's **Ask once**. The chooser appears the first time each product is opened in a sign-in, for every registered product including ones added later by the automatic setup (ADR-074). No per-product setting is needed, and per-product overrides remain.

## Consequences

- Fewer choices on the first screen. Switching or signing up takes one extra click.
- Browser acceptance: the two-screen checks K.1-K.26 (`Accounts/e2e/browser/chooser_acceptance.js`, `W360_ULC_CHOOSER=1`). E.1 and H.19 press the extra "Log in to another account".
- Sign-up's own page still says "Create Organization Account" / "Your organization is set up when you confirm your email", although the company name is optional. That wording is ADR-066's, unchanged here.
