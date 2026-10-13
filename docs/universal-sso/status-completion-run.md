# Universal sign-in: record of the "final completion" run (2026-10-12)

**Superseded by `status.md`** (the "one login for all products" brief, 2026-10-13). Kept as the record of that run.

**Written for: the product owner deciding on release, and the engineers and operators who deploy the five applications.**

Brief: "UNIVERSAL SSO FINAL COMPLETION: EASY PROJECT + EASY HELP DESK + SECURITY REMEDIATION" (2026-10-12). Design: `completion-design.md`, built on ADR-044 (with its addendum, items 10-13) and `design.md`. Capacity: `capacity.md`. Browser check: `scripts/universal_sso_browser_check/` (README). The earlier run of the same day, when two products were BLOCKED, is `status-adr044-run.md`. **Nothing is committed in any of the five repositories.**

## Verdict

**UNIVERSAL USER-CONTROLLED SSO: PASS**

All four products sign in with Workeasy360 through one OIDC architecture:
- each passes its own complete test suite and the real-browser journeys;
- each re-checks access per person while its session lasts;
- each gates its background work.

The Easy Project and Easy Help Desk code was found in the `WorkEasy360` GitHub account and cloned next to the others:
- `Project-Module` became `Desktop/Project-Module`;
- `helpdesk-workeasy` became `Desktop/helpdesk-workeasy`. The live help desk runs this one; `WorkEasy360-HelpDesk` is an older copy.

**Can one Workeasy360 user now sign in once and securely access Easy CRM, EasyBook, Easy Project and Easy Help Desk through the same user-controlled OIDC architecture, without sharing passwords and while maintaining entitlement enforcement throughout active product sessions? YES**

## Matrix

|                        | CRM | BOOK | PROJECT | HELPDESK |
|---|---|---|---|---|
| SSO supported          | PASS | PASS | PASS | PASS |
| Auto SSO               | PASS | PASS | PASS | PASS |
| Ask each time          | PASS | PASS | PASS | PASS |
| Separate login         | PASS ¹ | NOT SUPPORTED ² | NOT SUPPORTED ² | NOT SUPPORTED ² |
| Trial SSO              | PASS | PASS | PASS | PASS |
| Paid SSO               | PASS | PASS | PASS | PASS |
| Expired access denied  | PASS | PASS | PASS | PASS |
| Account switching      | PASS | PASS | PASS | PASS |
| Entitlement refresh    | PASS | PASS | PASS | PASS |
| Security audit         | PASS ³ | PASS ³ | PASS ³ | PASS ³ |
| Dependency audit       | PASS ⁴ | PASS ⁴ | PASS | PASS ⁴ |
| Browser E2E            | PASS | PASS | PASS | PASS |

1. Proven with Easy CRM registered `sso_optional` (its password sign-in on). The production registration is `sso_only`.
2. `sso_only`: there is no sign-in of the product's own. A "prefer the product's own sign-in" choice resolves to "ask" at Workeasy360. The settings page shows no such option for these products and says "<product> only uses Workeasy360 sign-in, so Workeasy360 will ask you each time" (journey 13).
3. An independent audit (Opus, read-only) per product found no P0. Every P1 and P2 it found was fixed and re-tested (section 21).
4. 0 critical/high with a fix available. One high remains in each JavaScript project: `braces <= 3.0.3` (GHSA-vfj7-8cjw-p6xm).
   - It is a stack-exhaustion denial of service on deeply nested glob patterns.
   - No patched release exists.
   - It is reached only through ESLint/Jest tooling (`eslint-config-next` → `fast-glob` → `micromatch`).
   - It is absent from production installs: `npm audit --omit=dev` reports 0.
   - The patterns it would parse come from the repository's own configuration.
   - Accepted and kept visible, with no audit ignore. Workeasy360 has the same chain.

## Report

### 1. Workeasy360 final backend suite
- **4,239 passed, 0 failed, 0 errors, 0 skipped** (1 h 21 min), the complete bare `pytest -rs` (`testpaths` = `backend/tests` + `backend/workeasy360`), run after the last Workeasy360 code change.
- The run before it had 4,235 passed and 1 error. The error was `OSError: [WinError 10055]` ("the system lacked sufficient buffer space"): Windows ran out of socket buffers during a test's teardown flush while four agents, builds and Docker shared the machine. It was not a code defect, and the module re-ran 25/25. The suite was then run again in full on a quieter machine, with the audit fixes in: the result above.
- Other gates: ruff and format (728 files), import contracts (18 kept), mypy, bandit (no medium/high), pip-audit (none), `manage.py check --fail-level WARNING`, and `makemigrations --check` (no changes) all pass.

### 2. Workeasy360 frontend suite
- No frontend code changed in this brief. The gates ran in a scratch copy, with a clean `npm ci`.
- typegen + tsc, eslint and `next build` pass. vitest: 774 passed in 40 files.
- `npm audit --omit=dev`: 0. Full audit: the dev-only `braces` chain only.

### 3. Easy CRM
- Backend: the full suite has 1,006 passed, 0 failed, 0 skipped. ruff, format, mypy (362 files), bandit and pip-audit pass, and `makemigrations --check` reports no changes.
- Frontend: run in a scratch copy with a clean `pnpm install --frozen-lockfile`. tsc and eslint pass. vitest: 260 passed in 46 files. `next build` passes. `pnpm audit`: `braces` only.

### 4. EasyBook
- Backend: 1,905 tests OK.
  - 5 skipped:
    - 1 is POSIX-only (gunicorn's `fcntl`); this machine runs Windows.
    - 4 are DB-bootstrap integration tests that need an admin DSN. They were run separately against the compose PostgreSQL and pass.
  - ruff, bandit, pip-audit, `manage.py check` and `makemigrations --check` are clean.
- Frontend: run in a scratch copy with a clean `npm ci`. typegen + tsc and eslint (`--max-warnings 0`) pass. vitest: 411 passed in 45 files. `next build` passes. `npm audit --omit=dev`: 0.
- The link-scanner test is slow, not racy:
  - It is synchronous file reads with no shared state.
  - It measured 60-170 ms warm, and up to 13.2 s cold on a busy OneDrive tree.
  - The scan now runs once per file instead of twice.
  - The two scanning tests have an explicit 45 s bound (3.4 times the worst observation, with the numbers in a comment).
  - 10 full-suite runs passed. Nothing was skipped or hidden.
- The "flaky" daily-schedule test was a real clock bug:
  - `date.today()` (local time) was compared with a UTC date. On Windows Django does not pin `TZ`, so a new daily rule looked "not yet due" from 00:00 to 05:30 IST.
  - The fix is one line, with a regression test.

### 5. Easy Project
- Backend: `./mvnw -B package` with the frontend built and a test PostgreSQL 17: **1,100 run, 0 failures, 0 errors, 0 skipped**. The baseline before this work was 722 run and 37 skipped, because the PostgreSQL-gated classes had never been enabled.
- Frontend: oxlint shows 0 errors (7 warnings that existed before). vitest: 241 passed in 32 files. `tsc -b && vite build` passes. `npm audit`: 0 vulnerabilities.
- Note: the JVM needs `-Duser.timezone=UTC` against `postgres:17` on this machine (`Asia/Calcutta` is refused). Production runs in UTC.
- The numbers above are from after the last change (a CSS contrast fix). One frontend test run in between, made while a Help Desk build saturated the machine, reported 40 of 241 failing. The next two quiet runs were 241 / 241, and the run inside the final Maven build passed too.

### 6. Easy Help Desk
Backend:
- `pnpm install --frozen-lockfile`, `pnpm -r typecheck` and `pnpm -r build` pass.
- Unit tests: api 234 (27 suites), worker 31, `@digisoft/workeasy360` 115, channels 67, analytics 16, ai 19, engine 24.
- API e2e: **354 passed in 24 suites** (322 in 23 before this round), against real PostgreSQL and Redis.
- Docker images for the API, worker and web all build.
- Both migrations pass `deploy/scripts/check-migrations.sh`.

Frontend:
- typecheck passes. lint: 0 errors.
- Playwright: 58 passed against the Docker-built production image.
- On this Windows machine, `pnpm build` stops at the standalone trace step (`EPERM` on a symlink, a privilege limit). The same build passes in Docker on Linux. The browser check uses a copy built without `output: 'standalone'`.

After the agent's final gate run, one more change was made: the sign-in and access-ended error alerts now use `text-red-700 dark:text-red-300` (contrast). It was checked by typecheck, lint and the real-browser run (0 findings on those pages); Playwright was not re-run for this class-only change.

Dependencies: `pnpm audit` shows `braces` only for highs, plus moderates.
- nodemailer moved 7 → 10, because no patched 7.x exists. The SMTP path was smoke-tested through the built API and worker providers into a scratch Mailpit:
  - both messages were delivered;
  - threading headers (`In-Reply-To`, `References`) were preserved.

### 7. Easy Project OIDC implementation
Before:
- Identity was trusted `X-User-Id` / `X-Org-Id` request headers: the Phase 12 **P0**, under which anyone could act as anyone.
- There was no Spring Security, no session, and no user or organization table.

Now: Spring Security's OAuth2 login, customised to the contract (completion-design §3).
- **Discovery** is fetched with timeouts and an exact issuer check, and refreshed without holding a lock.
- **PKCE S256 + state + nonce.** Up to 5 pending flows per browser, single use, 10 minutes, state compared in constant time.
- **`private_key_jwt`** with the product's key: RSA or EC P-256, PKCS#8 or PKCS#1/SEC1.
- **ID token checks:**
  - RS256/ES256 only; `kid` required;
  - `aud` exactly the client id, `azp` checked;
  - 30 s leeway;
  - `typ`, `jti`, `organization_id` and `product_code` enforced;
  - `wrong_organization` when the launcher asked for a different organization.
- **Federated identity** is `(issuer, sub)` in `federated_identities` (V14).
  - The internal user id is the Workeasy360 user id (`sub`), which is what `X-User-Id` was always meant to carry. A `sub` already bound under another issuer gets a fresh id instead.
  - The organization is the token's `organization_id`.
- **Session** is a server-side HttpSession:
  - a new session at sign-in (no fixation);
  - a different person signing in ends the previous session;
  - `__Host-` cookies when Secure;
  - CSRF cookie + header, and same-origin writes only.
- **Header identity is removed entirely.** Tests send the old headers and expect 401.
- **Entitlement** is checked per person (§4) with the read question, so a read-only organization can still read and writes get `workeasy360_read_only`. A refusal of the client is client-wide.
- **Production refuses to start** without the five settings, https URLs and a usable key.
- **No background work** exists: automations run inside the request, under its check. A test fails if someone adds a scheduled or async effect.
- Operator document: `Project-Module/docs/workeasy360-sso.md`.

### 8. Easy Help Desk OIDC implementation
NestJS module plus a shared package, `@digisoft/workeasy360` (discovery, client assertion, verification, entitlement cache, background gate), used by the API and the worker.
- `jose` is pinned at 5.10: the API is CommonJS, and 6.x ships ESM only.
- **Routes** sit outside the `/api/v1` prefix: start, callback and logout under `/api/auth/...`. nginx already sends `/api/` to the API.
- **The flow** is stored in Redis (GETDEL, 10 minutes) under the hash of a 256-bit httpOnly `__Secure-` cookie scoped to the callback path.
- **Provisioning** follows EasyBook's rules:
  - an organization is bound by `workeasy360OrganizationId`;
  - a new Workeasy360 organization gets a new Help Desk organization through the same provisioning as registration;
  - an existing agent is linked by address only when `email_verified` is true, inside the bound organization, and never onto a CUSTOMER or an already-linked account;
  - linking retires the password and revokes refresh tokens and outstanding reset/invite tokens;
  - a CLI links an existing organization before its first sign-in.
- **SSO-only by default in production.**
  - The agents' password login, registration, forgot/reset password, change-password and invitations are refused.
  - The customer portal keeps its own sign-in, with a portal-only reset route.
  - Password-era sessions are refused.
- **Entitlement guard:**
  - on every agent route, the portal and the realtime socket (checked at connect, re-checked every 60 s, disconnected when access ends);
  - a CUSTOMER token is refused on agent routes and on the socket;
  - portal writes need write access.
- **Logs** redact the `Location` header, and 5xx log lines carry the path only. The nginx template logs `/api/auth/` without its query string.
- Operator document: `helpdesk-workeasy/docs/workeasy360-sso.md`.

### 9. Easy CRM dependency security
| Package | Before | After | Advisory |
|---|---|---|---|
| urllib3 | 2.7.0 | 2.8.0 | PYSEC-2026-4175, -4176, -4177 |
| next (+ eslint-config-next) | 15.5.25 | 15.5.27 | GHSA-4jqv-mc3x-m676, GHSA-mcj8-r9mp-w47p |
| sharp | 0.35.4 | 0.35.5 | GHSA-wq5f-xc86-pv6w |
| source-map-js | 1.2.1 | 1.2.2 | GHSA-68fv-2mgg-jv7q |
| brace-expansion | 1.1.18 / 2.1.4 / 5.0.9 | 1.1.21 / 2.1.7 / 5.0.12 | GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p, GHSA-q2hr-2g5m-vwhr |

Same major line throughout. pip-audit is clean. `pnpm audit` shows only `braces` (footnote 4).

### 10. EasyBook dependency security
| Package | Before | After | Advisory |
|---|---|---|---|
| next (+ eslint-config-next) | 16.3.5 | 16.3.8 | GHSA-vcvr-r3jv-pc5j (critical RCE in `next/og`), GHSA-cjq9-62q9-8jv4 (SSRF in image optimisation), and 5 moderate/low |
| PyJWT | 2.14.0 | 2.15.0 | PYSEC-2026-4141, PYSEC-2026-4183 |
| urllib3 | 2.7.0 | 2.8.0 | PYSEC-2026-4175, -4176, -4177 |
| sharp | 0.35.4 | 0.35.5 | GHSA-wq5f-xc86-pv6w |
| source-map-js | 1.2.1 | 1.2.2 | GHSA-68fv-2mgg-jv7q |

pip-audit is clean, `npm audit --omit=dev` reports 0, and the full audit shows `braces` only. The developer's own `frontend/node_modules` still holds 16.3.5 until `npm ci` runs; it was not touched, because a dev server may hold it.

### 11. Background-job entitlement enforcement
The rule is completion-design §5:
- Class A, customer business effects, need `allow` and `write_allowed` when the job runs.
- Class B (cleanup) and class C (retention and maintenance) run regardless.
- A skipped job is logged (`workeasy360.background_skipped`), not retried in a loop, and resumes once after renewal. Nothing is dropped or doubled.
- Sweeps look for due work first, so an idle or expired organization costs nothing.

Per product:
- **Easy CRM.** Class A:
  - reminders, deal-health sweep, outbound email and WhatsApp, mailbox sync;
  - integration dispatch, delivery, sync and inbound events;
  - RAG indexing, and imports.
  Queued messages and received inbound events are picked up again after renewal by sweeps. A skipped import pauses instead of failing.
- **EasyBook.** Class A:
  - automation execution, event dispatch, due schedules and scans;
  - recurring invoices, bills and expenses (`next_run_at` untouched; catch-up drafts once each, never posted);
  - OCR and RAG indexing (a new resume sweep).
  Purging expired AI data is class C.
- **Easy Project.** No background work exists.
- **Easy Help Desk.** Class A:
  - automation and escalation, channel replies, outbound webhooks (deliveries stay PENDING), AI, notifications;
  - access-requiring email (automation, CSAT);
  - the global SLA scan (a denied organization's tickets are left out until its answer is due again).
  Report-export completion and CSAT expiry are class B; metric rollups are class C.

### 12. Entitlement verification capacity
`capacity.md` has the model, measured cost and rules.
- **Demand per product, per minute:**
  - `R = P/15 + B/15 + D`, where:
    - `P` is people active in a 15-minute window;
    - `B` is organizations with background work due;
    - `D` is denied people retrying, at most one each per minute.
  - Per-person questions roughly double the cost:
    - organization-level p50 is 36 ms;
    - per-person p50 is 71 ms (test environment).
- **Limits:**
  - `W360_RL_VERIFY` 1,200/min per product client (was 600). This serves 18,000 active people per product. It is a bound on harm: 20 requests/s is about half of the Phase 12 three-worker host.
  - New `W360_RL_VERIFY_ORG` 6/min per (client, organization, person), asked **before** the client bucket. A looping key is refused without spending the product's budget (tested).
  - Keys are canonical UUIDs.
- **Rules:** size the API before products approach their caps; alert on `ecosystem_rate_limited_total{bucket="verify"}`; never lengthen `revalidate_after` to save capacity.

### 13. CSP
- Easy CRM's blocked `eval` was Zod 4's capability probe. The fix is `z.config({ jitless: true })`, loaded before any schema is created, with guard tests.
- Easy Help Desk had the same probe. It is fixed the same way, through a `lib/shared.ts` wrapper, so `frontend/shared` stays byte-identical with the backend copy.
- No `unsafe-eval` anywhere.
- Real browser: 0 violations on Workeasy360 pages, 0 on product pages, and 0 console errors.

### 14. Redirects
- Exact-match redirect and post-logout URIs:
  - Easy Project: `https://ops.workeasy360.com/api/auth/callback/workeasy360` and `https://ops.workeasy360.com/`.
  - Easy Help Desk: `https://help.workeasy360.com/api/auth/callback/workeasy360` and `https://help.workeasy360.com/login`.
- No wildcards.
- `next` must be a same-site path: no whitespace, control characters or backslashes; not `//`, `#` or an auth page; at most 2,048 characters. Each product tests the open-redirect variants: `//evil`, `/\evil`, `/<tab>/evil`, `https://evil`, encoded forms.
- The launcher's `/?organization=<uuid>` always starts a sign-in for that organization.

### 15. Trial SSO
For each of the four products separately (journey 6):
- the 7-day trial starts, and the product opens through Workeasy360 and serves the organization;
- after `ends_at` there is no Open, the launch is refused, and Explore says "Trial expired" with Upgrade / Buy now;
- the open product session is refused within the window;
- a new sign-in from the product is refused (`not_entitled`).

### 16. Paid SSO
For each of the four (journey 10):
- checkout and payment through the offline test provider;
- My Products shows the product paid, with Open;
- it opens through Workeasy360 with **the same federated product account** as during the trial;
- it serves the organization again within a minute (a denial is re-asked after 60 s).

### 17. Account switching
- In every product, a different person signing in ends the previous session server-side:
  - Easy CRM: `django_login` flushes the session;
  - EasyBook: the previous refresh token is revoked;
  - Easy Project: a new session, the old one invalidated;
  - Easy Help Desk: the presented refresh family is revoked.
- Journey 7 proves it for Easy CRM, Easy Project and Easy Help Desk: A signs out of Workeasy360, B signs in, and the product holds B. It also covers "Use another Workeasy360 account".
- Session fixation is covered by each product's tests.

### 18. User preferences
- AUTO (journey 1), ASK (2), REMEMBER (3), CHANGE (4), the per-product OVERRIDE matrix (11) and PRODUCT_LOGIN → NOT SUPPORTED with an explanation (13) were all verified for all four products.
- A product registered during the run followed the person's default with no code change (5).
- A preference never grants access: it is applied at the authorization endpoint after every membership, organization, policy, entitlement and seat check (ADR-044 §4).

### 19. Browser journeys
- **74 of 74 checks passed** in 13 journeys, on a fresh scratch state.
- Production frontend builds; real backends; the installed Chrome.
- Six applications: Workeasy360, the four products, and Future Product.
- 26 pages were audited with axe (WCAG 2.1 A/AA), a focus indicator and exactly one `h1`, plus reflow at 1280, 768 and 375 px.
  - Results: 0 reflow overflow, 0 CSP violations, 0 console errors.
  - The only findings were colour contrast on Easy Project's signed-in pages: the "Ctrl K" hint and the account button's "org …" line. Both came from the dark theme's `--color-text-faint`, at 4.42:1 against the required 4.5:1. The token was nudged to `#848a9e` (about 5.0:1).
  - After that, Easy Project's gates were re-run (Maven 1,100 / 0 / 0; vitest 241) and journeys 1 and 8, which audit those pages, were re-run: 11 of 11 checks, 9 pages, **0 findings**.
- Earlier runs on the way also found and fixed:
  - the Help Desk's Zod `eval` probe (CSP);
  - missing `h1`s on two Help Desk pages;
  - the Help Desk sign-in error contrast;
  - Easy Project contrast on the sidebar tagline, section titles and sign-in footnote.
- The scratch Workeasy360 answers `revalidate_after = 30 s`, so "an open session loses access within the window" is observed live for every product, with no hook inside any of them.
- New journey 12: Ana suspends Ben in Workeasy360, and each of the four products refuses his open session within the window.

### 20. Final matrix
Above.

### 21. Remaining P0 / P1 / P2 / P3
**P0: none. P1 in code: none.**

The independent audit's P1s were all fixed and re-tested:
- Easy Project locked read-only organizations out completely.
- The Help Desk realtime socket ignored entitlement.
- The Help Desk let password-era sessions survive SSO-only.
- The Help Desk broke the customer portal's password reset.
- EasyBook returned 500 on every request during a Redis outage.

**Outside the code (before go-live):**
- Set the `WORKEASY360_*` variables:
  - Easy Project: the Elastic Beanstalk environment, with the key from a file or secret;
  - Easy Help Desk: `deploy/.env`.
  Both refuse to start without them.
- Register both with `manage.py connect_product_sso`, which now switches a stored `local_only` registration to `sso_only`.
- Easy Project only: review the rows written in the header era, using the queries in its document.

**P2:**
- Size the Workeasy360 API for the verification rate before any product nears its cap (`capacity.md` §4).
- Easy Project keeps sessions in memory. That needs one instance or sticky sessions, and a deploy signs everyone out; they come back through Workeasy360.

**P3:**
- The dev-only `braces` advisory (footnote 4).
- "Sign out of all sessions" or a password reset in Workeasy360 does not end product sessions. Removal, suspension, a disabled account or an unassigned seat does, within the window. There is no front- or back-channel logout (ADR-044).
- Easy CRM's entitlement call runs inside the request transaction, bounded at 5 s.
- A portal customer who registered an employee's address blocks that employee's first Help Desk sign-in (`account_conflict`), until an administrator resolves it.
- The first person from a new Workeasy360 organization becomes that product organization's administrator (EasyBook, Easy Help Desk): there is no role claim to use.
- The Help Desk refresh cookie's path widened to `/api`, so the callback can revoke the previous person's family. It is httpOnly, SameSite=Lax and Secure.
- Building the Help Desk frontend locally on Windows needs symlink rights.
- A manual screen-reader pass was not done.
- Item 21 of the brief (switch organization from an access-ended screen) is done in Easy CRM and EasyBook. Easy Project and Easy Help Desk have one organization per session.

**Unchanged and out of scope:** D-035 (sign-in on a partner's custom domain).

## Architecture check (brief item 35)
- **One identity provider.** Workeasy360 issues every ID token. Each product verifies offline against Workeasy360's key set, with an exact issuer match.
- **No shared passwords.** No password, hash or product password crosses a boundary. Linking an existing product account retires its local password.
- **No product-specific forks.**
  - All four implement the same contract: completion-design §3-5 and integration guide §5-7.
  - Workeasy360's sign-in machinery names no product. Architecture tests check its sources, now including `verification.py`.
- **No tokens in URLs.**
  - Only the authorization `code` and `state` travel in the callback query, as OAuth requires: single use, 60 s, PKCE-bound.
  - No access or ID token is put in a URL.
  - Product logs and the Help Desk's nginx template redact the callback query.
- **Exact redirect allowlists.** String equality at Workeasy360, one callback per product.
- **One entitlement authority.** Workeasy360's entitlement API answers every product, per organization and now per person. Products only cache its answers.
- **Future products are registry-driven.** A product registered in the console followed the person's default with no code change (journey 5). `products.json` and `connect_product_sso` are data plus one command.
- **A preference never grants entitlement.** It is applied after every access check.
- **Product sessions are independently secured.** Each product has its own session, cookie, CSRF and fixation protection, its own per-person re-check, and its own sign-out.
