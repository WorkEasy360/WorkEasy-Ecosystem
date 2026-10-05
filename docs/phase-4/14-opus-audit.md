# 14 — Phase 4 Adversarial Audit

**Result: PASS**, after fixing one P0, nine P1s and eight P2s found
during it.
**Date:** 2026-09-22
**Scope:** security, architecture, frontend, session, access control,
resilience, performance. No Phase 5 work; no product-domain code; no Super
Admin, reseller, white-label or affiliate features.

**Baseline re-confirmed before any change:** 973 backend tests passed,
0 failed (13m05s, full suite). 11 frontend tests passed.
**After:** 1,010 backend, 29 frontend, both from clean state.

---

## 1. The headline

Phase 4's own report was accurate about the backend and wrong about the
frontend in one specific, total way: **the production build of the platform
frontend could not run any JavaScript at all on the four routes an
unauthenticated visitor reaches first, including `/login`.**

`src/proxy.ts` emitted `script-src 'self' 'nonce-<random>' 'strict-dynamic'`.
`'strict-dynamic'` makes a CSP Level 3 browser ignore `'self'` and every
host-source in that directive — a `<script>` runs only if it carries the
matching nonce. Next.js injects that nonce while *server-rendering a
request*, so a statically prerendered page, built when no request exists,
carries none. `next build` was emitting `/`, `/login`,
`/organizations/select` and `/_not-found` as `○ (Static)`.

Verified against a real production build before the fix: `/login` served
twelve `<script>` tags, **zero** with a nonce. Every one would have been
blocked. The page renders as an inert shell — no hydration, no session
fetch, a sign-in form whose submit handler never attaches.

Next.js states the rule plainly in its own bundled documentation
(`node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md`):
*"To use a nonce, your page must be dynamically rendered … Static pages are
generated at build time, when no request or response headers exist—so no
nonce can be injected."* The implementation copied that document's example
policy and not its constraint.

Nothing could have caught it. The frontend suite runs components in jsdom,
where there is no CSP. No test ever produced a production build. **CI had no
frontend job at all** — no lint, no types, no tests, no build. The two
Phase 4 documents even contradicted each other on the fact that mattered:
[08-csp-and-browser-security.md](08-csp-and-browser-security.md) asserted
"every page in this app is already dynamically rendered", while
[09-performance.md](09-performance.md) recorded "four are static" from the
same build output.

The same policy's `style-src 'self' 'nonce-X'` blocked all ~70 inline
`style` attributes the app renders, for a separate reason: CSP3's *does a
source list allow all inline behavior?* algorithm returns **Does Not Allow**
for inline style the moment the list contains any nonce or hash, and a nonce
cannot attach to an attribute. Adding `'unsafe-inline'` beside the nonce
would not have helped — the nonce's presence is what disqualifies the list.

**Fixed and verified.** After: all nine routes render dynamically, 12 of 12
script tags on `/login` carry the request nonce, `style-src` is
`'self' 'unsafe-inline'` (recorded as debt D-010 rather than presented as
strict), and Lighthouse on the production build scores performance 100,
accessibility 100, best practices 100.

---

## 2. Findings

### P0

| # | Finding | Status |
|---|---|---|
| 1 | Nonce-based `script-src` with `'strict-dynamic'` over four statically prerendered routes: all JavaScript blocked in production, sign-in impossible. | **Fixed** — `export const dynamic = "force-dynamic"` in `app/layout.tsx`; pinned by `src/proxy.test.ts` and by `scripts/assert-dynamic-rendering.mjs`, a CI step that reads the build's own `prerender-manifest.json`. |

### P1

| # | Finding | Status |
|---|---|---|
| 2 | `style-src` carried a nonce, blocking every inline `style` attribute the app renders. | **Fixed** — `style-src 'self' 'unsafe-inline'`, debt D-010, pinned by a test asserting no nonce appears in that directive. |
| 3 | `POST /auth/login` shipped publicly reachable and **entirely unthrottled**. Argon2id is tuned to ~250 ms of memory-hard work and Django hashes a dummy password even for an unknown email, so it was a workload anyone on the internet could schedule; per-account lockout cannot see one password sprayed across many accounts. | **Fixed** — `login_ip` and `login_account` buckets (D-009 below), 9 tests. |
| 4 | **D-009 trigger reached.** Rate limits were keyed on `REMOTE_ADDR`, which behind CloudFront/ALB is the proxy — every caller in one bucket, so one abuser throttles a whole customer base. | **Fixed** — `platform/observability/client_ip.py`: the forwarded chain is read *from the right*, `W360_TRUSTED_PROXY_COUNT` hops in; at 0 the header is not read at all; `required=True` in production. 11 tests, including a 30-entry spoofed chain. **D-009 closed.** |
| 5 | `SECURE_PROXY_SSL_HEADER` unconditionally trusted `X-Forwarded-Proto`, so with nothing in front any caller could tell Django the request arrived over TLS — defeating `SECURE_SSL_REDIRECT` and making `request.is_secure()` answer to the client. | **Fixed** — derived from the same proxy-count declaration; pinned by a test asserting the two always agree. |
| 6 | Phase 4's headline security property — seat enforcement at `/authorize` **and** at redemption — had **no test at either boundary**. The code was correct; nothing proved it, and nothing would have noticed its removal. | **Fixed** — three tests: `product_no_seat` at authorize, seat released between authorize and token → `invalid_grant`, and an organization-wide product still authorizing with no seat (the false-positive half). |
| 7 | **No frontend CI job existed.** Every frontend gate was a command a human remembered to run. | **Fixed** — `frontend` job: `npm ci`, lint, typecheck, test, build, `npm audit`, prerender gate. Added to the required `gate`. |
| 8 | `manage.py check --deploy --fail-level WARNING` **could never pass** in the environment CI runs it in (development settings, `DEBUG=True`), so that gate enforced nothing. | **Fixed** — CI runs `check --fail-level WARNING`; Django's deployment checks are asserted against the *real* production settings module by the existing `test_production_accepts_a_valid_configuration` / `test_production_refuses_insecure_configuration`, which is stronger. |
| 9 | `pip-audit --strict` over `requirements-dev.txt` reported a known vulnerability (pytest 8.3.4, PYSEC-2026-1845), and `make security` audited only `requirements.txt` while CI audited both — so local and CI disagreed about whether the gate passed. | **Fixed** — pytest 9.1.1 / pytest-django 4.14.0 / pytest-cov 7.1.0; `make security` widened to match CI. Suite runs unchanged on pytest 9. |
| 10 | `frontend` could not be type-checked on a clean checkout: `LayoutProps` is generated by Next.js into `.next/types`, so `tsc --noEmit` failed before a build had run. | **Fixed** — `typecheck` is now `next typegen && tsc --noEmit`. |

### P2

| # | Finding | Status |
|---|---|---|
| 11 | Seat assignment accepted any `membership_id` from the URL. The composite tenant foreign key is `DEFERRABLE INITIALLY DEFERRED`, so a foreign or unknown id was rejected only at `COMMIT` — after the view answered **201**, producing a **500** on the way out. Invisible to tests, because pytest rolls each test back and never commits. | **Fixed** — `active_membership_exists` checked in the service; uniform `membership_required` for "another organization's" and "does not exist". 3 tests. |
| 12 | The app directory cost **two queries per product**; one was `can_launch_product` re-reading a catalog descriptor `app_directory` had just fetched in bulk. | **Fixed** — slope halved (50 products: 119 → 69 queries), budget 23 → 21, and a new test measures at two catalog sizes and asserts the *difference*, so the per-product cost is falsifiable rather than commented. |
| 13 | `AppCard` rendered `{app.description \|\| copy.hint}`, so "ask an administrator for a seat" and "contact your account team" appeared **only** for a product with no catalog description — which no real product has. Every unusable card named no next step. Found by writing the accessibility test, not by axe. | **Fixed** — the hint is its own line; a test asserts every actionable state names an action. |
| 14 | `app/error.tsx` rendered `<html>`/`<body>` inside the root layout. Those belong only in `global-error.tsx`, which replaces the layout. | **Fixed.** |
| 15 | `organizations.selectors.active_member_ids` was dead — no caller, no test. | **Removed**, replaced by `active_membership_exists`, which finding 11 needed. |
| 16 | No `robots.txt`; the login page was indexable and Lighthouse's `robots-txt` audit failed outright. | **Fixed** — `Disallow: /`, with `robots.txt` excluded from the proxy's session redirect so a crawler can actually read it. |
| 17 | Redis-outage behaviour was asserted by "231 tests pass with the container stopped", which proves little: the test settings use `LocMemCache`, so stopping Redis changes nothing those tests touch. | **Fixed** — `test_cache_degradation.py` points the cache at a dead port and asserts sign-in still works, the throttle still bounds the endpoint in-process, an entitlement never reads as denied, and an OIDC launch still completes. |
| 18 | `logging_config()` (~35 lines) and `set_request_id()` in `platform/observability` have no caller anywhere. Pre-existing (Phase 1), outside this audit's stated scope. | **Reported, not removed** — either wire `logging_config` into `LOGGING` or delete it; a Phase 5 decision. |

### Assessed, no change

- **`SessionEpochMiddleware` treats a session carrying no recorded epoch as
  current** rather than revoked, to avoid a mass sign-out on deploy day. Only
  `login_view` creates sessions and it always writes the epoch, so no path
  produces one — acceptable now, and a trap if a future flow (password reset
  auto-login, impersonation) calls `django_login` without it. Worth a guard
  when such a flow is added.
- **`launcher.services.launch()`** is a one-line passthrough to
  `can_launch_product`. Kept: it is what makes "views call exactly one
  application service" (G4) true for `launch_view`, and removing it is churn.
- **`LaunchTargetDTO.is_terminal`** is unused — documented as deliberate in
  ADR-021 §4 so a future caller can distinguish "retired" from "draft".
  Kept, and named here so it is a decision rather than an oversight.

---

## 3. Audit-by-audit

### Launch decision, launcher/OIDC consistency, seats (1–5, 48)

There is one decision function, `platform.launcher.access.can_launch_product`,
and both the directory and the launch endpoint call it — the launch endpoint
re-runs it rather than trusting what the browser was shown. Independently,
the OIDC flow re-asks the same two questions through the same gate at
`/authorize` **and** again at `_revalidate_subject` during redemption. No
weaker subset path exists: searched views, services, selectors, admin
surfaces and the session flows.

The states are backend-produced and closed (`active`, `no_seat`,
`no_entitlement`, `integration_suspended`, `product_unavailable`); the
frontend derives no security semantics from raw fields. `licensing_mode` is
read from the catalog, never inferred from seat count — proven by
`test_an_organization_wide_product_never_requires_a_seat`, which would fail
if a seat check were applied indiscriminately.

**Seat removed between authorize and redemption now denies, with a test.**
That was the one mandatory case with no coverage.

### Seat concurrency (4)

Existing coverage is real concurrency with a thread barrier, 8 workers:
limit 10, 9 assigned, 8 contenders → exactly one succeeds; the same member
assigned twice takes one seat (resolved by the partial unique index, since
no lock ordering can prevent that race); reconcile-vs-assign does not
deadlock. Added the one case that was missing: **concurrent assign and
release from a full pool**, asserting neither over-allocation nor a lost
release.

### PendingAuthorization, login resume, organization chooser (6–8)

Single-use by the same conditional UPDATE `redeem_code` uses; expiry checked;
pruning exists (`prune_expired_pending`). Every field is *copied* at park
time from a request already verified by `resolve_client`/`parse_request`, so
client, redirect URI, scope, state, nonce and PKCE challenge cannot be
influenced at resume — there is no code path that re-reads them from the
browser. The only browser-supplied field at resume is `organization_id`, and
only when the original request named none; it is then re-verified against a
live membership by the same `resolve_subject` the authorization endpoint
uses. Substitution is covered by an existing test that inspects the minted
code directly. Resume is now rate-limited, which it was not.

### Session revocation, fixation, cookies, CSRF (9–12)

`django_login` rotates the session key. `SessionEpochMiddleware` sits
immediately after `AuthenticationMiddleware` and calls `django_logout` —
which flushes the session outright — on a mismatch; covered over real HTTP.
Cookies: `HttpOnly` + `SameSite=Lax` + `Secure` in base, with `Secure`
relaxed only in the test settings. CSRF: login goes through the ordinary
middleware (not exempt), `session_view` carries `@ensure_csrf_cookie` so the
cookie exists before any write is reachable, and the launch POST is a real
form submission carrying `csrfmiddlewaretoken`. A new architecture test
asserts that the only CSRF-exempt views are the three credential-
authenticated ones that enumerate themselves in `CREDENTIALED_VIEWS`.

### Multi-org, cache isolation, multi-tab (13–16)

**The organization is never in the session.** The Django session holds
exactly `w360_session_epoch` and `w360_aal`; organization context comes from
the URL path and is re-resolved to a membership on every request. That makes
the multi-tab question answer itself: Tab A on Org A and Tab B on Org B are
independent, and no action can execute against an unexpected organization
because a sibling tab navigated.

On the frontend there is no cache to leak from: every call is
`cache: "no-store"`, there is no state-management or data-fetching library,
no Server Component fetches Django, no `unstable_cache`, no `force-cache`,
no module-level mutable state, and nothing is written to `localStorage` or
`sessionStorage` (`document.cookie` is read only for the CSRF token, which
is deliberately readable). Every route is now `force-dynamic`, so there is
also no prerendered HTML that could be shared between users — before this
audit `/login` was being served with `Cache-Control: s-maxage=31536000`.

### App directory API, launch URL, iframes, icons (17–20, 32, 60)

The wire shape is `product_code`, `display_name`, `description`,
`licensing_mode`, `state`, `can_launch` — no client secret, no redirect
configuration, no subscription object, no price-book internals. `launch_url`
is deliberately absent, and a new architecture test scans `interfaces/` for
any dict literal keyed `launch_url` so a future endpoint cannot reintroduce
it. The browser POSTs a product code; the server re-runs the decision and
builds the redirect from `ProductIntegration` metadata read at that moment,
so metadata changed while a launcher page was open takes effect on the next
click. `javascript:`/`data:`/foreign-domain targets are unreachable because
the browser never supplies a target at all. No iframe anywhere. Product
icons are text monograms computed from the display name — no `next/image`,
no `remotePatterns`, no remote URL, no SVG.

### Product outage, health, Redis, RabbitMQ (21–24)

`launch_target` is a database read of the local registry; the launcher makes
no outbound call to any product, so a launcher page load cannot fan out into
a cascading failure. Product health never reaches `/healthz/live` or
`/healthz/ready` (fitness test A17, plus a readiness test with an empty
registry). 331 tests across login, the launcher, the OIDC flow and the
resume flow pass with both Redis and RabbitMQ stopped, and — new this audit —
four tests pass against a *genuinely unreachable* cache, which is what
separates capacity degradation from correctness degradation.

### CSP, headers, CORS, XSS (27–31)

Covered in §1 and in [08](08-csp-and-browser-security.md). No
`dangerouslySetInnerHTML` anywhere; every dynamic string (product names and
descriptions, organization and user names, error copy) goes through JSX text
interpolation. Authoritative layers: Next.js sets the frontend origin's
policy, Django sets the API origin's (`default-src 'none'`), and HSTS/TLS
redirect belong to the production Django settings. The only wildcard CORS
header is on the two credential-free public documents (discovery, JWKS),
which accept no cookie — asserted by an existing test.

### Accessibility and performance (33–39)

axe-core 4.13 over the app card in all five states, the sign-in form, the
seat table and the status screens: **0 violations**. Lighthouse 12.8.2 on
the production build: **performance 100, accessibility 100, best practices
100**. Bundle: 190 KB gzipped across all chunks, largest 70 KB, no UI/icon/
date/state library — the only runtime dependencies are `next`, `react`,
`react-dom`. Query slopes measured, not asserted: see
[09-performance.md](09-performance.md).

### Organization onboarding (40) and the chooser scan (39)

Both are real gaps, both measured or pinned rather than described:

- **Onboarding does not exist.** Existing organizations can be administered;
  a new customer cannot create one, and a freshly created organization's
  owner holds no permissions until a role is assigned. **P1 for Phase 5**, a
  go-live prerequisite, not a Phase 4 exit failure — Phase 4's task list
  never included it and everything Phase 4 *did* build works.
- **The chooser scan** costs 5 queries per organization *on the platform*
  per request: 500 organizations = 2,506 queries and 1.7 s, and past the
  2,000 cap a user silently vanishes from their own chooser. Recorded as
  debt D-013 with the measurements. **P1, go-live prerequisite.** Not fixed
  here: the fix is a projection table needing every membership write path
  audited, and the brief is explicit that a projection table is not to be
  built unless the trigger is reached. It is not yet — correctness below the
  cap is not in question.

### Product-domain freedom (61–63)

`products/` holds ten `__init__.py` files and nothing else, asserted
unconditionally. No platform module branches on a product code (AST scan
over `==` and `match`). No product-domain route. Added a fitness test that
extends the same rule to `frontend/src`: no file or folder named after a
product may exist there. A CRM tile is metadata; a CRM frontend would be a
product.

---

## 4. The silent-failure question (67)

> *"What could keep all current tests green while silently giving the wrong
> user access to the wrong product or organization in production?"*

Answering "none" would have been wrong, and the audit found the proof: a
Content Security Policy that made the entire production application inert
while 984 tests passed. Not an access-control failure, but the same class —
a property no test could observe, in a layer no test ran in.

The realistic remaining candidates, and what now stands between each and
production:

1. **Deferred-constraint violations are invisible to the test suite.** Every
   tenant-to-tenant foreign key is `DEFERRABLE INITIALLY DEFERRED` and
   pytest rolls each test back rather than committing, so a violation is
   never evaluated. A cross-organization write therefore *passes* in tests
   and 500s at commit in production. Found and fixed for seat assignment
   (finding 11); **the same shape exists anywhere else a client-supplied id
   becomes a tenant foreign key**, and the defence is the application check,
   never the constraint. This is the highest-value thing to carry into
   Phase 5.
2. **Proxy headers.** Now closed for client IP and `X-Forwarded-Proto`, both
   keyed to one explicit declaration that production refuses to start
   without.
3. **Browser and CDN caching.** Every route is `no-store` now; before, a
   prerendered `/login` was served `s-maxage=31536000`, which a CDN would
   have cached.
4. **Multi-instance state.** The only correctness-adjacent state not in
   PostgreSQL is the throttle's in-process fallback during a cache outage,
   which degrades capacity and is read by no authorization decision
   (D-012). `PendingAuthorization`, sessions, seat assignments and launch
   decisions are all database-backed.
5. **Seat and membership changes mid-flow.** Re-checked at redemption, now
   with tests.
6. **Multi-tab organization context.** Structurally safe: the organization
   is in the URL, never in the session.
7. **Clock skew** at the OIDC boundary is bounded and tested (Phase 3).

Tests were added for 1, 2, 3 and 5.

---

## 5. Quality gates

| Gate | Result |
|---|---|
| `pytest` (fresh database, `--create-db`) | **1,010 passed, 0 failed, 0 skipped** |
| `ruff check` | clean |
| `ruff format --check` | clean |
| `mypy` | clean (20 files) |
| `bandit -ll` | 0 medium, 0 high |
| `pip-audit --strict` (runtime + dev) | 0 vulnerabilities |
| `lint-imports` | 12 contracts kept, 0 broken |
| `manage.py check --fail-level WARNING` | clean |
| `manage.py makemigrations --check` | no changes |
| `eslint` | clean |
| `tsc --noEmit` | clean (from a clean checkout) |
| `vitest` | **29 passed, 0 failed** |
| `next build` | 9 routes, all dynamic |
| `assert-dynamic-rendering` | clean |
| `npm audit` | 0 vulnerabilities |
| axe-core (WCAG 2.1 A + AA, non-visual) | 0 violations |
| Lighthouse (mobile, production build) | 100 / 100 / 100 |

Nothing was skipped, marked `xfail` or deselected to reach a pass.

One flake appeared in a test this audit wrote and was fixed rather than
rerun: the limiter's fixed window is `int(time.time()) // seconds`, so a
throttling test whose requests straddle a minute boundary sees the counter
reset half way through. A `frozen_throttle_window` fixture now pins the
clock for every throttling assertion. Recorded because "rerun it" is how a
suite stops being believed.

---

## 6. Carried into Phase 5

1. **Organization creation and owner-role bootstrap** — go-live prerequisite.
2. **The chooser projection table** (D-013) — go-live prerequisite, measured.
3. **Deferred-constraint blindness in the test suite** — decide whether to
   add a commit-forcing test mode, or to require an application-level check
   wherever a client-supplied id becomes a tenant foreign key. Finding 11 is
   the worked example.
4. **`style-src 'unsafe-inline'`** (D-010) — earn it back by moving ~70
   inline style attributes into the CSS modules the app already uses.
5. **Manual screen-reader and contrast pass** — the only accessibility work
   an automated tool cannot do.
6. **CSP violation reporting** — both P0/P1 CSP findings would have been
   reported by a browser on the first page load.
7. **MFA/step-up UI** — unchanged from Phase 4; nothing in this UI is a
   `dangerous` permission yet.
8. **`logging_config()` / `set_request_id()`** — wire up or delete.
