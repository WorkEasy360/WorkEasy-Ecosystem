# Universal, user-controlled product sign-in: the ADR-044 run (2026-10-12, superseded)

**Superseded by `status.md`** (the final-completion brief, the same day): Easy Project and Easy Help Desk were BLOCKED here because their code had not been found; it has since been found, both now have OIDC clients, and the verdict below is no longer current. Kept as the record of that run.

Brief: "WORKEASY360 — USER-CONTROLLED UNIVERSAL SSO / ONE ACCOUNT ACROSS ALL CURRENT + FUTURE PRODUCTS" (2026-10-12). Decision record: ADR-044. Design: `design.md`. Browser check: `scripts/universal_sso_browser_check/` (README). Nothing is committed.

## Verdict

**UNIVERSAL USER-CONTROLLED SSO: FAIL**, on the brief's own rule ("do NOT report SSO PASS until each external application actually implements and proves the OIDC client flow"):
- Easy Project and Easy Help Desk have no OIDC client, and their code is not available to this work. Both are BLOCKED. Workeasy360 registers them as separate sign-in (`local_only`) and labels them "Separate product sign-in required"; nothing pretends SSO works for them.
- Everything that was built passes: Workeasy360 as the identity provider, Easy CRM, EasyBook, and a product registered during the run.

**Can a Workeasy360 user now sign in once with one Workeasy360 account and access every entitled SSO-enabled product without re-entering credentials, while retaining complete control over whether Workeasy360 signs them in automatically, asks each time, or uses the product's separate login, and will the same architecture automatically support products added in the future? YES.**
- Every SSO-enabled product (Easy CRM, EasyBook) opens without a second password.
- All three preferences work, globally and per product.
- A product registered in the Super Admin console during the run followed the person's existing default with no code change.
- The two BLOCKED products are not SSO-enabled, which is why the question's scope is met while the four-product verdict above is not.

## Report

1. **Central authentication architecture:** one identity provider (Workeasy360, OIDC authorization code + PKCE S256, `private_key_jwt`). Products are OIDC clients holding their own sessions. The person's preference is enforced at the identity provider after every access check (ADR-044 §4).
2. **Password isolation:**
   - No password, hash or product password crosses the boundary. Products get an ID token and never a credential.
   - Easy CRM now also refuses a local password for any Workeasy360-linked account, even with password sign-in on (audit fix): it would be a second way in that skips Workeasy360's MFA and policy.
   - Architecture tests check that no password field reaches a product.
3. **User global preference:** Automatically / Ask each time (default) / Prefer each product's own sign-in. One row per person (`ecosystem_user_sign_in_preference`, `product_code ''`), changeable in Settings → Sign-in preferences, with "Reset all to default". Recorded in the person's account-security trail, not an organization's audit log. PASS (browser journeys 1, 2).
4. **Per-product override:** `inherit` (no row) / Automatic / Ask / Product sign-in (offered only where the product has one). PASS (journeys 3, 4).
5. **AUTO_SSO:** silent code on the existing Workeasy360 session, after every membership, organization, policy, entitlement and seat check. PASS (journey 1: Easy CRM, then EasyBook, no second password).
6. **ASK:** the access chooser on the auth host ("How would you like to access Easy CRM?"):
   - Continue with Workeasy360;
   - Use Easy CRM sign-in (only when offered);
   - Remember my choice;
   - Use another Workeasy360 account;
   - Cancel (`access_denied`).

   A parked request is bound to its person and cannot be resumed past the question. PASS (journeys 2, 3).
7. **PRODUCT_LOGIN:** the launcher opens the product's registered `login_url` with no organization hint. A product with no sign-in of its own is asked about instead, never silently signed in. PASS (journey 4, with Easy CRM registered `sso_optional` in the run; production keeps it `sso_only`, see the matrix).
8. **Future-product inheritance:** nothing names a product. "Future Product", registered during the run, followed the person's default (Automatic, then Ask). PASS (journey 5).
9. **Product registration:** the Super Admin Authentication section covers:
   - SSO Enabled / Disabled / Integration required, mode, client ID, redirect URIs, sign-out return URIs, product sign-in page, status;
   - register, edit, activate and suspend;
   - add a public key, or generate a client secret shown once;
   - revoke a credential.

   A draft reads "Integration required"; an active integration with no usable credential is refused at the authorization endpoint, not offered (audit fix). PASS (journey 5).
10. **Easy CRM SSO:** PASS. Silent, chooser, product sign-in, trial, paid, expiry, account switching, "Confirm it's you" (`prompt=login`, current organization, same `sub`, fresh `auth_time`), and RP-initiated sign-out.
11. **EasyBook SSO:** PASS. Silent, chooser (Workeasy360 only: EasyBook is `sso_only`), trial, paid, expiry, account switching, and RP-initiated sign-out. A production defect found by the browser run was fixed: its SSO routes redirected to `localhost:3000` behind the proxy.
12. **Easy Project SSO:** BLOCKED. No client code here; registered `local_only`.
13. **Easy Help Desk SSO:** BLOCKED. Same.
14. **Entitlement enforcement:**
    - At Workeasy360, the authorization and token endpoints check entitlement and seat live, and a preference never skips them.
    - In the products, a session opened through SSO no longer outlives the access behind it. Both products re-check through the entitlement API every 15 minutes, re-ask a denial after 60 s, and refuse with "access has ended". An outage is never a denial; a refusal of the product's own client is; a known denial never lapses. PASS (journeys 6, 10).
15. **Trial enforcement:**
    - During the 7-day trial both products open through SSO.
    - Past `ends_at`: no Open, the launch is refused, Explore says "Trial expired" with Upgrade / Buy now, each product's open session ends at its next check, and a new sign-in is refused (`not_entitled`).
    - After purchase, both products open again. PASS (journeys 6, 10).
16. **Account switching:**
    - A different person signing in ends the previous sign-in, and the product then holds the new person.
    - "Use another Workeasy360 account" signs the current account out.
    - A "Confirm it's you" answered by another account is refused and switches nobody.
    - PASS (journeys 7, 9).
17. **Logout:**
    - "Sign out of the product" leaves Workeasy360 signed in.
    - "Sign out of the product and Workeasy360" uses RP-initiated logout: Workeasy360 asks first (a GET signs nobody out), then returns to the exact registered URI.
    - Signing out of Workeasy360 does not end other products' sessions (no product implements front- or back-channel logout); stated in the product docs.
    - PASS (journeys 4, 8).
18. **Security findings:** an independent audit (three reviewers: the identity provider, Easy CRM, EasyBook) found nothing critical or high. Every medium and low finding was fixed (design.md §12) except the accepted items under 28.
19. **Redirects:** exact-match redirect and post-logout URIs, with no wildcard, credentials or percent-encoded host. Every product `next` target refuses whitespace, control characters and backslashes; EasyBook's tab-character bypass (`/<tab>/evil` → `//evil`) was fixed. The launcher and chooser destinations come from the registry, never from the request.
20. **PKCE/state/nonce:** S256 only, single-use 60 s hashed codes, state and nonce compared in constant time by both products, `auth_time` checked for `prompt=login`. Covered by the security tests (code replay, wrong client, audience, redirect URI, expired code, PKCE, state and nonce mismatch, cross-product client, cross-organization launch).
21. **Token handling:**
    - No token in a query string.
    - No refresh tokens are issued.
    - ID tokens are verified offline (kid from JWKS, issuer, audience, expiry, typ, nonce).
    - New log lines carry codes, product and organization ids only; a test asserts no code, token, state or nonce appears.
22. **Frontend UX:**
    - Workeasy360: sign-in line on every product card, chooser, settings with per-product overrides, sign-out confirmation, Super Admin Authentication section, "Confirm it's you" sign-in.
    - Products: "Already a Workeasy360 user?" landing, both sign-out options, and the access-ended screens. The access-ended screens say "can't confirm your access" when Workeasy360 refused the product's check.
23. **Accessibility:** 12 pages audited in a real browser with axe (WCAG 2.1 A/AA): 0 violations, a visible focus indicator on every tabbed element, exactly one `h1` (Easy CRM's gate screens gained theirs). No reflow overflow at 1280, 768 and 375 px. A manual screen-reader pass was not done.
24. **Browser journeys:**
    - The final run on a fresh scratch state with production builds: 32 of 32 checks across 10 journeys (auto, ask, remember, change, future product, trial and expiry, paid, account switching, logout, re-authentication).
    - 0 Workeasy360 CSP violations; 0 console errors.
    - Product pages: 3 CSP reports, all Easy CRM's pre-existing Zod 4 eval probe (blocked by its policy, no effect; see 28).
25. **Backend tests:**
    - Workeasy360: the full suite, 4,195 tests: 4,194 passed and 1 failed - the Partner Control Center migration guard, which pins each app's newest migration and did not yet know `identity/0007` (no table added). Its expectation was updated with that reason, and its module re-run passes 10/10. No production code changed after the full run; the suite was not repeated for that test-only change. New suite `backend/tests/universal_sso` covers preferences, resolution, the chooser, `prompt`, re-authentication, the launcher, RP logout, future products, sessions, observability, contract shapes and architecture rules. The required security tests are included: code replay, wrong client, audience, redirect URI, expired code, PKCE, state and nonce mismatch, cross-product client, cross-organization launch, expired trial, suspended subscription, removed member, removed seat, account switching, session fixation and open redirects.
    - Easy CRM: about 929 tests, 0 failures.
    - EasyBook: 1,813 tests OK (5 skipped).
26. **Frontend tests:**
    - Workeasy360: 774 tests in 40 files.
    - Easy CRM: 250 tests in 45 files.
    - EasyBook: 400 tests in 45 files: 400/400 in two consecutive runs. `src/lib/routes.test.ts` (a whole-tree link scanner, 5 s default timeout) timed out in 2 earlier full runs on a busy machine - timing, not a defect.
27. **Migrations:**
    - `ecosystem/0005_universal_sign_in`: additive; new table; nullable or database-defaulted columns.
    - `identity/0007_account_event_sign_in_preference`: widens a varchar and adds a choice; declared overlap-safe.
    - `makemigrations --check`: no changes in Workeasy360, Easy CRM and EasyBook.
    - The products gained no migrations.
28. **Remaining P1/P2/P3:**
    - **P1:** Easy Project and Easy Help Desk need OIDC clients (the pattern of Easy CRM and EasyBook, including the entitlement re-check), then registration as `sso_only` or `sso_optional`. This is what turns the verdict into PASS.
    - **P2:** background jobs in both products still run for an organization whose access has ended. No person reaches its data, but they keep writing and sending: recurring invoices and bills, automations and outbound webhooks, e-mail and CRM sync, reminders.
    - **P2:** Easy CRM's CSP reports a blocked `eval`: Zod 4's capability probe. This predates this work; `z.config({ jitless: true })` silences it.
    - **P2:** Workeasy360's entitlement-verify limit is per client (`W360_RL_VERIFY`, 600/min, about 9,000 organizations per product at the 15-minute cadence); size it before then.
    - **P3:** the access-ended screens have no organization switcher.
    - **P3:** EasyBook's signed download URL lifetime is uncapped configuration (default 300 s, inside the window).
    - **P3:** no front- or back-channel logout, by design.
    - **P3:** a manual screen-reader pass was not done.
    - **P3:** EasyBook's `src/lib/routes.test.ts` link scanner can exceed vitest's 5 s default on a busy machine; give it a longer timeout.
    - **P1 (outside this brief, pre-existing in the products' committed lockfiles):** EasyBook runs `next 16.3.5`, which has critical advisories including RCE in `next/og` and SSRF in image optimisation; upgrade to 16.3.8 or later, as Workeasy360 did. It also pins `PyJWT 2.14.0`, which has two advisories; neither path is used by its OIDC client, which verifies the signature before parsing claims and uses no OKP keys, but upgrade to 2.15.0. Easy CRM has `urllib3 2.7.0` (fixed in 2.8.0) and frontend advisories through `next 15.5.25` and `sharp`.
    - Unchanged and unrelated: D-035 (sign-in on a partner's custom domain refused by the CSRF origin check).

## Matrix

|                       | CRM | BOOK | PROJECT | HELPDESK |
|---|---|---|---|---|
| SSO supported         | PASS | PASS | BLOCKED | BLOCKED |
| Auto SSO              | PASS | PASS | BLOCKED | BLOCKED |
| Ask each time         | PASS | PASS | BLOCKED | BLOCKED |
| Separate login        | PASS ¹ | NOT SUPPORTED ² | BLOCKED ³ | BLOCKED ³ |
| Trial SSO             | PASS | PASS | BLOCKED | BLOCKED |
| Paid SSO              | PASS | PASS | BLOCKED | BLOCKED |
| Expired access denied | PASS | PASS | BLOCKED ⁴ | BLOCKED ⁴ |
| Account switching     | PASS | PASS | BLOCKED | BLOCKED |

1. Proven with Easy CRM registered `sso_optional` (its password sign-in on). The production registration is `sso_only` (the 2026-10-12 decision), where the choice is not offered.
2. EasyBook is `sso_only` and has no sign-in of its own in production.
3. Workeasy360 sends people to their own sign-in (tested), but the products are not in reach.
4. Workeasy360 refuses the launch once access ends (tested for all four products); their own sessions are not in reach.

## Gates (run after the final code changes)

| Gate | Workeasy360 | Easy CRM | EasyBook |
|---|---|---|---|
| Full backend suite | 4,194 / 4,195; the 1 failure was a stale migration-guard expectation, fixed and re-run | PASS (~929) | PASS (1,813, 5 skipped) |
| Lint (ruff check / format) | PASS / PASS (backend; harness scripts too) | PASS / PASS | PASS / not a gate there |
| Types (mypy) | PASS (configured scope) | PASS (360 files) | not a gate there |
| Import contracts (import-linter) | PASS (18 kept) | - | - |
| bandit | PASS (no medium/high) | PASS | PASS |
| pip-audit | PASS (no known vulnerabilities) | FAIL: urllib3 2.7.0 (pre-existing, fixed in 2.8.0) | FAIL: PyJWT 2.14.0 (pre-existing, fixed in 2.15.0) |
| `manage.py check` | PASS (`--fail-level WARNING`; deploy checks: no errors) | - | PASS |
| `makemigrations --check` | PASS (no changes) | PASS | PASS |
| Clean `npm ci` (scratch copy) | PASS | - | - |
| Typecheck | PASS (typegen + tsc) | PASS | PASS (typegen + tsc) |
| eslint | PASS | PASS | PASS (`--max-warnings 0`) |
| Frontend tests (vitest) | PASS (774) | PASS (250) | PASS (400; 400/400 twice in a row; one link-scanner test is timing-sensitive) |
| `next build` | PASS | PASS | PASS |
| npm audit | Production: 0. Full: the known dev-only `braces` advisory (GHSA-vfj7-8cjw-p6xm, via eslint-config-next) | FAIL: pnpm audit, 9 high (next 15.5.25, sharp; pre-existing) | FAIL: production, next 16.3.5 critical advisories (fixed in 16.3.8), sharp, source-map-js (pre-existing) |
| Real-browser journeys, axe, CSP | PASS: 32/32 checks; 0 axe findings on 12 pages; 0 Workeasy360 CSP violations | (in the same run) | (in the same run) |

The products' dependency advisories are all in versions committed before this work (their HEAD lockfiles). They are listed under item 28 and were not changed here; upgrading another product's framework is outside this brief.
