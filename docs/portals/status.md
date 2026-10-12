# Multi-portal domains + central auth + four products + 7-day trial: status

Date: 2026-10-11. Design: [design.md](design.md). Decision: [ADR-043](../architecture/adrs/ADR-043-multi-portal-hosts-and-central-sign-in.md). Browser check: [scripts/portals_browser_check](../../scripts/portals_browser_check/README.md). Nothing is committed or deployed.

## Result

**MULTI-PORTAL + FOUR-PRODUCT TRIAL INTEGRATION: PASS for the platform as built and verified. Go-live is BLOCKED outside the code.**

All four of these were verified end to end against the real host names, through the production edge configuration, in a real browser:
- the portal split;
- the central sign-in;
- the authorization boundaries;
- the 7-day trial of each product.

**What is not done:**
- None of the five hosts exists in DNS, and nothing is deployed.
- No plan or price has been published for the four products, so the 7-day trials are configured but switched off in a real catalog until staff publish one.
- No product has an OIDC client.

See section 4.

## 1. What was built

**Five hosts, one application.** `admin.`, `partner.`, `app.`, `auth.` and `api.workeasy360.com`.
- The frontend proxy chooses which UI a host shows. A page belonging to another portal is redirected there, and a signed-out visit is sent to the central sign-in.
- The backend's `PortalSurfaceMiddleware` returns 404 for each portal's privileged API on every other host:
  - admin;
  - partner;
  - organization;
  - staff catalog and integration administration;
  - sign-in;
  - hand-off;
  - OIDC authorize.
- It also returns 403 for a write that names another portal's origin.
- Both rules only refuse. Every grant is still StaffGrant, PartnerMember or Membership, read live.

**Central sign-in.** One form, on `auth.` only. The modes are Administrator, Partner and User, and the mode is navigation intent only.
- **Eligibility is decided from identity:**
  - live StaffGrants for the Admin portal, plus AAL2 when an authenticator is enrolled (verified on `auth.`);
  - an active partner membership for the Partner portal;
  - for the customer portal, any account may enter, and every organization request checks membership.
- **Hand-off:** a 256-bit, single-use, 60-second code, bound to the portal host and the portal's `state` cookie.
  - It travels in the URL fragment to `<portal>/auth/continue`.
  - It is redeemed once for that host's own session; the key is rotated.
  - AAL, step-up time and sign-in time are copied verbatim, so the session is never more elevated or longer-lived than the sign-in.
  - Only hashes are stored.

**Sessions and cookies.**
- Cookies are host-only. Production uses `__Host-w360_session`, `__Host-w360_csrf` and `__Host-w360_portal_state`.
- There is no `.workeasy360.com` cookie.
- One `sign_in_id` per sign-in: signing out on any host ends every session of it.

**Configuration.**
- Production refuses to start unless:
  - the five origins are set, https, and on distinct hosts;
  - ALLOWED_HOSTS is exactly those five hosts;
  - CSRF_TRUSTED_ORIGINS is exactly the four web origins;
  - the issuer is `auth.`;
  - the cookie domains are unset;
  - the hand-off TTL is 10-300 s.
- The Caddyfile, compose, the env example and the staging generator are updated to match.

**Products and trials.**
- `products.json` carries each product's 7-day trial. `register_products` applies it once, switched off while no plan is eligible; staff switch it on in the Product Manager.
- Explore shows every product, with the server's "Trial active — N days remaining", "Trial expired" plus "Upgrade / Buy now", "Start 7-Day Free Trial", Buy now and Learn more.
- My Products adds the rest of the catalog as EXPIRED or AVAILABLE.

**A real bug found and fixed.** Entitlement snapshots carried no end time, so a trial kept granting access, features and AI budget past `ends_at` until the sweeper next ran. Snapshot entries now carry `ends_at`, and every read honours it:
- `decide`, `products`, `feature`/`quota`;
- the product-facing `decision_for_organization` and `feature_for_organization`.

## 2. Evidence

| Gate | Result |
|---|---|
| Backend full suite (bare `pytest`, 3,830 collected) | **3,830 passed, 0 failed** (1 pre-existing warning) |
| New backend tests (`backend/tests/portals/`, 125) | 125 passed |
| ruff, ruff format, mypy, lint-imports (18 contracts), bandit, pip-audit | clean |
| `manage.py check` / `makemigrations --check` | no issues / no changes |
| `check --deploy`, production settings, five-portal env | no Django security warning (only 4 database-reachability warnings: no production database exists) |
| Frontend: clean `npm ci`, tsc, eslint, vitest | 0 / 0 / **747 passed** |
| `next build`, dynamic-rendering assertion | clean; no route prerendered (CSP nonce on every page) |
| npm audit | runtime dependencies **0**; dev-only: 5 high in the accepted `braces` chain (no upstream fix). New `sharp` advisory **fixed** (lockfile, 0.35.5) |
| Real-browser journeys (final code) | **67/67**, 15 pages audited: axe 0, CSP 0, console errors 0, focus 0, one h1 each |
| Reflow 1440/1024/768/390/320 | **45/45** |
| Edge rehearsal (production Caddyfile, https, verified TLS) | **43/43** |
| Live DNS/TLS (`scripts/verify_portal_dns_tls.py`) | **0/25: all five hosts NXDOMAIN** |
| Independent final audit (Opus) | no P0/P1; 4 P2 + 9 P3, all addressed (section 5) |

## 3. Final full backend suite

Bare `pytest` from the repository root (every testpath, run on the final code after the audit fixes): **3,830 passed, 0 failed, 0 skipped**, 1 warning (pre-existing, `test_ecosystem_security`). The previous full run, before this brief, was 3,690. A first attempt was cut off at 3,551/3,830 (all passing) when the session ended, so it was re-run in full.

## 4. The 35-item report

1. **Admin portal.** `admin.` serves only `/admin/**`; `/` goes to `/admin`. The browser test: signed out → `auth.` → password → TOTP on `auth.` → StaffGrant → console. The Product Manager lists the four products at "7 days, on". **PASS (local, real host names). Deployment BLOCKED.**
2. **Partner portal.** `partner.` serves only `/partner/**`. A partner member enters; a customer gets "You don't have access to the Partner portal." The partner sees only its granted products (not Easy Project). **PASS.**
3. **Customer portal.** `app.` serves the public site and `/dashboard`, `/o/**`, `/organizations`, `/onboarding` and `/billing`. Staff are no longer sent to the console from `app.`. **PASS.**
4. **Auth portal.** The only host with the sign-in form, sign-up, verification, reset, the invitation pages, and the OIDC issuer endpoints. **PASS.**
5. **API portal.** `api.` serves `/api/*` only (no UI). The portal-owned APIs are refused there (404). **PASS.**
6. **Host routing.** A pure routing table shared by the proxy and the pages (`lib/portals.ts`, `routePortalRequest`). Cross-portal navigation is a full navigation (`usePortalRouter`), because the CSP forbids cross-origin payload fetches. **PASS.**
7. **Sessions.** Host-only sessions joined by the hand-off. Each keeps the sign-in's age (the 30-day cap is never renewed) and AAL. **PASS.**
8. **CSRF.** Exactly the four web origins (production-enforced, no wildcard), plus a per-portal origin rule for writes. A product host, `evil.example`, a look-alike and `null` are all refused. **PASS.**
9. **Cookies.** Secure (production), HttpOnly, SameSite=Lax, `__Host-` in production, no Domain. Browser-verified as host-only, with nothing for the parent domain. **PASS.**
10. **Login redirects.** `next` is a path in a per-portal allowlist, and the origin is always taken from configuration. `javascript:`, `data:`, external URLs, `//host`, `..`, encoded escapes and localhost all fall back to the portal root (unit, HTTP and browser tests). **PASS.**
11. **Admin authorization.** A customer is signed in and refused, with no admin API call; a customer session presented on `admin.` gets 403. Staff without a grant in force are refused. A staff member without a factor reaches the D-037 gate. **PASS.**
12. **Partner authorization.** Live membership at the hand-off and on every partner request. **PASS.**
13. **User authorization.** Organization membership on every request. Staff and partner members on `app.` see only their own memberships (none in the test) and get 403/404 for a customer's organization. **PASS.**
14. **Easy CRM.** **PASS** (matrix below).
15. **EasyBook.** **PASS** (matrix below).
16. **Easy Project.** **PASS** (matrix below). The product's own sign-in is a P0 in that product (section 6).
17. **Easy Help Desk.** **PASS** (matrix below). Its host 301-redirects to `13-200-218-28.sslip.io` (section 6).
18. **7-day ProductTrial.** The existing `ProductTrial`/`ProductTrialConfig`; no second trial system. Every product has a 7-day duration recorded; it is switched on per product once a plan is eligible. Starts are idempotent; one per organization and product, enforced by the database. **PASS.**
19. **Trial expiry.** It ends at exactly `ends_at` by server time, before any sweeper runs:
    - backend test: allowed at `ends_at − 1 s`, denied at `ends_at`;
    - browser: the controlled clock moved the trial back 7 days and a minute;
    - launch, held products, features and AI budgets all end;
    - the organization and its history stay.

    **PASS.**
20. **Trial to paid.** Explore → Upgrade / Buy now → the existing checkout → test payment → ACTIVE, launchable, one trial row and one paid entitlement, no duplicate organization. Done for each product. **PASS.**
21. **Catalog.** Explore shows all four with icon, name, category, tagline, price, trial availability, and the brief's actions and states. **PASS.**
22. **My Products.** Held products show ACTIVE, TRIAL ("N days left") or SUSPENDED; the rest show EXPIRED or AVAILABLE with the right action. **PASS.**
23. **Launcher.** The destination is server-owned (the manifest). Launch requires entitlement, a seat and an active integration. The browser reached the real `crm.`, `book.`, `ops.` and (via redirect) Help Desk hosts. **PASS.**
24. **SSO.** **SSO INTEGRATION REQUIRED** for all four. None of their login pages or JS bundles contains an OIDC client, and each registered callback URL returns 404. Nothing is faked; no token is passed in a URL.
25. **Reseller.** Partners see and sell only granted products (backend-enforced, Phase 7/11 tests unchanged and passing; browser-verified product list). **PASS.**
26. **White-label.** Branding is presentation only; a partner domain serves only the branded landing and hands over to `auth.` (ADR-039, rehearsed at the edge). **PASS.**
27. **Affiliate.** The existing referral, commission and payout engine, unchanged, across all products (existing suites). **PASS.**
28. **DNS/TLS.** Configuration prepared, and rehearsed with verified TLS: 308 from http to https, a certificate per host, HSTS (2 years, includeSubDomains, preload), Host forwarding, routing and hidden internal paths. **Live: BLOCKED.** The five names are NXDOMAIN; the owner must create the DNS records and deploy the edge (ACME issues and renews automatically).
29. **Security findings.** See section 5. Open items are listed in section 6.
30. **Browser journeys.** 67/67 (A, N, P, U, T×4, S, R, L). **PASS.**
31. **Accessibility.** axe WCAG 2.1 A/AA: 0 violations on 15 pages (login, step-up, refusal, admin dashboard, Product Manager, partner chooser and products, customer dashboard, My Products ×3, Explore ×2, checkout, payment page). Keyboard focus is visible on every page. **Manual screen reader: NOT EXECUTED.**
32. **Backend tests.** 3,830 passed, 0 failed (full suite). **PASS.**
33. **Frontend tests.** 747 passed, with tsc, eslint, a clean `npm ci` and the build all clean. **PASS.**
34. **Migrations.**
    - `identity.0005`: the hand-off table and the session's `sign_in_id`, atomic;
    - `identity.0006`: a CONCURRENT index, alone;
    - both pass the overlap-safety guard, and `makemigrations --check` is clean.

    The user's dev database is **not** migrated (it needs their approval).
35. **Remaining P1/P2/P3.** See section 6.

### Product matrix

| | CRM | BOOK | PROJECT | HELPDESK |
|---|---|---|---|---|
| Registered | PASS | PASS | PASS | PASS |
| Explore | PASS | PASS | PASS | PASS |
| 7-day trial | PASS | PASS | PASS | PASS |
| Trial entitlement | PASS | PASS | PASS | PASS |
| Trial expiry | PASS | PASS | PASS | PASS |
| Buy | PASS | PASS | PASS | PASS |
| Paid entitlement | PASS | PASS | PASS | PASS |
| Dashboard | PASS | PASS | PASS | PASS |
| Launch | PASS | PASS | PASS | PASS (lands on sslip.io) |
| SSO | BLOCKED: integration required | BLOCKED | BLOCKED | BLOCKED |
| Reseller | PASS | PASS | PASS (not granted, refused) | PASS |
| White-label | PASS | PASS | PASS | PASS |
| Affiliate | PASS | PASS | PASS | PASS |
| Isolation | PASS | PASS | PASS | PASS |

Each product was walked separately, in the browser and in `backend/tests/portals/test_four_product_seven_day_trials.py`. The plans and prices used exist only in the scratch and test databases.

## 5. The independent audit, and what changed

There was no P0 or P1. Everything else was fixed and pinned in `backend/tests/portals/test_audit_fixes.py`:

- **P2-1: the hand-off renewed the 30-day absolute cap.** Fixed: the redeemed row is dated from the sign-in. The first attempt at this fix was ineffective (`cycle_key` inserts the row early); the new test caught it.
- **P2-2: staff catalog and integration administration under `/orgs/` was served on `app.`.** Fixed: it is now `admin.` only.
- **P2-3: the time-exact end covered launch only.** Fixed: held products, features, quotas and the product path now honour it as well.
- **P2-4: a trialing subscription that converts at trial end.** No change. The platform stores no payment method (D-028), so nothing auto-converts, and the writer already ends such access at `trial_ends_at`.
- **P3: fixed.**
  - default ports normalised;
  - the binding-cookie name derived from the scheme;
  - ALLOWED_HOSTS must be exact, and the TTL is bounded;
  - a sign-in id is minted for older sessions;
  - the migration is split;
  - the per-portal Origin rule is added;
  - an unreadable end now fails closed in the merge;
  - staff hand-off sessions are recorded;
  - the two-tab comment is corrected.
- **P3: noted for operations.**
  - The first reconcile after deploy reports drift for every organization, because stored snapshots gain `ends_at`. This is expected once; tell the on-call.

## 6. Open items

**Outside this repository / owner actions:**
- **P0, Easy Project (product).** Sign-in uses `X-User-Id`/`X-Org-Id` headers ("There is no password"); re-checked 2026-10-11.
- **P1.** None of the five portal hosts resolves (NXDOMAIN), and the platform is not deployed. Create DNS for `admin`, `partner`, `app`, `auth` and `api.workeasy360.com` pointing at the edge, then run `scripts/verify_portal_dns_tls.py`.
- **P1.** No product has an OIDC client (all four: SSO INTEGRATION REQUIRED). *Update 2026-10-12:* EasyBook and Easy CRM now have one, in their own repositories (uncommitted). Each has its `docs/workeasy360-sso.md`, and both were verified live against a scratch Workeasy360 with real keys. An operator connects each one with `manage.py connect_product_sso`. The `organization` scope (`organization_name` claim) was added for them. Easy Help Desk and Easy Project are still pending; their code is not on this machine.
- **P1.** No plan or price exists for the four products. To make trials startable, publish a plan per product and switch its 7-day trial on in the Product Manager.
- **P1.** Phase 12's go-live blockers still stand: real email and payment providers, scheduled backups, monitoring.
- **P2.** `help.workeasy360.com` 301-redirects to `13-200-218-28.sslip.io`.

**Operations, done on 2026-10-11 at the owner's request:**
- **Frontend dependencies:** `npm ci` in `frontend/` (no dev server was running) installed `sharp` 0.35.5 and `next` 16.3.8. The lockfile is unchanged byte for byte; runtime advisories are 0.
- **Dev database:** `identity.0005` and `identity.0006` were applied to `workeasy360` after a backup (`%USERPROFILE%\workeasy360-dev-backups\workeasy360-before-identity0005-20261011-221325.dump`). Nothing is pending, the index is valid, the app role has its privileges, and `manage.py check` is clean.
- **Staging environment:** updated **in place**, not regenerated. `make_env.py` mints new secrets that would no longer open staging's existing database, MFA secrets or sealed values.
  - The host settings of the env file in use (both identical copies, plus the load-test copy) now follow the five-host shape `admin|partner|app|auth|api.staging.workeasy360.com`, with the issuer on `auth.`. `W360_CANONICAL_HOST` is removed. Each file has a `.pre-portals.bak` beside it.
  - Every secret is byte for byte unchanged (hash-checked).
  - The staging settings load under the new production checks, and `docker compose config` accepts it.
  - Staging itself is still stopped, on the older image `p12-audit2`. Running this code there needs new images and a deploy.
- **Still to expect:** the first reconcile after deploying (and in development) reports drift for every organization once, because stored snapshots gain `ends_at`. This is harmless.

**Accepted:** the dev-only `braces` advisory (no upstream fix; unchanged since the 2026-10 audit).
