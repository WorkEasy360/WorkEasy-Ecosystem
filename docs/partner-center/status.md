# Partner Control Center + Administrator / User sign-in: status

**Date:** 2026-10-06
**Decision record:** [ADR-041](../architecture/adrs/ADR-041-partner-control-center-and-sign-in-modes.md)
**Browser check:** [scripts/partner_center_browser_check/](../../scripts/partner_center_browser_check/README.md)
**Commit state:** nothing is committed.

## Result

**PARTNER CONTROL CENTER + LOGIN MODE: PASS** (see the gate table in §2 and the full-suite line in §3).

## 1. What was built

| # | Item | Result |
|---|---|---|
| 1 | Sidebar | The Super Admin sidebar's seven partner links (Overview, Resellers, White-label, Customers, Products & Pricing, Commissions, Payouts) became **one "Partners" entry**, in the Audience group. It stays highlighted on every `/admin/partners/*` page. It links to the first Control Center section the staff member may read, and is hidden when they may read none. |
| 2 | Partner Control Center | `/admin/partners` layout: a header and a local navigation of nine sections, each shown only with the grant its page needs (`partnerCenterNav.ts`). Pills wrap on wide screens; below 640 px they become one strip that scrolls inside itself, keeping the current section in view. `aria-current="page"` is marked by weight and border, not by colour alone. Each page keeps its own single `h1`. |
| 3 | Partner dashboard | The existing factual cards plus **Total partners** and **Active affiliates**. No MRR, ARR, revenue or growth figure (the page says why). **Quick actions**: Add partner, Add reseller, Enable white-label, Create affiliate, Review payouts, each shown only with its grant. **Recent activity** has six panels: recent partners, recent customer provisioning, recent partner sales, pending payouts, domain verification required, recent commission activity. Every row links to the screen that explains it. All of it is one read (`GET /admin/partners/recent`) over existing data; no activity system was added. The money panels need `staff_commission.view` and are *absent* (`null`) without it, not empty. |
| 4 | All Partners | `/admin/partners/all`: every `PartnerAccount`, including one with no capabilities. Columns: Partner, Labels, Status, Customers, Products, Subscriptions, Commission, White-label, Created, Last activity. |
| 5 | Resellers | `/admin/partners/resellers`: a server filter on the canonical label function (`experience_keys(RESELLER)`). Columns: Reseller, Status, Customers, Products allowed, Active subscriptions, Commission, Payouts, White-label, Last activity, Actions. The old `?scope=all` address redirects to All Partners. |
| 6 | White-label | The existing filtered view over `PartnerBrand` and `PartnerDomain` (brand, logo, domain status, customers, updated), reached from the Control Center. Its "Enable white-label" picker is the quick action's target. |
| 7 | Affiliates | `/admin/partners/affiliates`: a server filter on `experience_keys(AFFILIATE)`. Columns: Affiliate, Status, Referral codes (up to three, then "and N more"), Conversions ("converted of captured"), Commission, Pending payout, Paid, Last activity. Figures come from the existing referral, ledger and payout tables, with three grouped queries per page. |
| 8 | Customers | The existing all-partner customer list, inside the Center. |
| 9 | Products & Pricing | The existing per-partner product view, inside the Center. |
| 10 | Commissions | The existing ledger, now at `/admin/partners/commissions`. `/admin/commissions` redirects there with its query. |
| 11 | Payouts | The existing payouts screen, now at `/admin/partners/payouts`. `/admin/payouts` redirects there with its query. |
| 12 | Partner detail | The header shows the name, the status and **badges derived from grants**. Tabs: Overview, Customers, Products & Pricing, Subscriptions, Commissions, Payouts, **Branding and Domains** (shown when the partner wears White-label), Users & Permissions, **Referrals** (new, shown when the partner wears Affiliate; read-only, codes plus counts by state plus a page of referrals, no prospect data), Activity. The Overview adds an "Affiliate & referrals" summary and an "Enable white-label" link when the partner is not white-label. Setup progress gains a **Capabilities** step. |
| 13 | Capability-derived labels | They come only from `capabilities.experience_labels`. They are now also returned on `GET /admin/partners/{id}` (`experience`). The frontend never derives or stores them. One account can wear Reseller, White-label and Affiliate at once and is one row everywhere (browser journey E). |
| 14 | Administrator sign-in | `/login` asks "How would you like to sign in?". **Administrator** ("Manage the Workeasy360 platform") and **User** ("Access your organization and products") are real links to `?mode=admin` and `?mode=user`. Then the one form. Administrator goes to `/admin`, or to a validated `next` under `/admin`. There, the second factor is asked (D-037) and the console opens. |
| 15 | User sign-in | The same form goes to `/dashboard?mode=user`, then the organization (or the chooser, or onboarding), the organization dashboard and **My products**. |
| 16 | StaffGrant authorization | `/api/v1/admin/session` decides on every console load. It requires `is_staff`, a session that verified a second factor, and **at least one StaffGrant in force, read live**. That last condition is new: a staff identity with no grant in force used to see an empty console and now gets 403. `is_superuser` is never read. |
| 17 | Non-admin denial | "You don't have administrator access." with **Continue as User** and **Sign out**. It names no permission and loads nothing from the console. The server answers 403 `permission_denied`. |
| 18 | Admin as User | `mode=user` only removes the dashboard's staff redirect. An administrator with a membership gets their organization; one without gets ordinary onboarding. Backend: the same session reaches `/organizations` and `/orgs/{slug}/apps`. Browser: journey D. |
| 19 | MFA | Staff verify a second factor before the console opens. This is asked *before* grants are judged, so the refusal order leaks nothing. Viewing needs no fresh step-up. Dangerous actions keep AAL2: a capability change from the Center with a lapsed step-up gets 401 `step_up_required` (test). |
| 20 | IDOR and isolation | A partner session gets 403 on every new admin endpoint. Another partner's referrals return 404 from the portal. An unknown partner id returns 404. Unrelated staff grants get 403. Suspending a partner leaves its customer's organization status and subscription unchanged. |
| 21 | Responsive | 85 reflow checks at 1440, 1024, 768, 390 and 320 px over 17 pages: **0 failures**. At 390 and 320 px the local navigation scrolls inside itself, the current section stays visible, and the page never scrolls sideways. |
| 22 | Accessibility | Real browser: 21 pages, **0 axe WCAG 2.1 A/AA violations**, 0 elements without a visible focus, exactly one `h1` on each, 0 CSP violations, 0 console errors. The mode choice is reachable and activated by keyboard (Tab, Enter) in journey A. jsdom axe passes on the new components. **Manual screen-reader pass: NOT EXECUTED.** |
| 23 | Browser journeys | **8/8 passed**: A (Administrator), B (a customer chooses Administrator), C (customer as User), D (admin as User), E (combined partner), the deep link, the old addresses, and the narrow-screen navigation. See §4. |
| 24 | Backend tests | See §3. 62 new tests are in `backend/tests/partner_center/`. |
| 25 | Frontend tests | **428 passed** (26 files), up from 390. Clean `npm ci`, `tsc`, ESLint, `next build` and `assert-dynamic-rendering` all pass. |
| 26 | Dependencies | **source-map-js 1.2.1 → 1.2.2** (lockfile only, 3 lines, transitive through `next`/`postcss`, `vite` and `jsdom`). It fixes the new GHSA-68fv-2mgg-jv7q (high), which appeared after the 2026-10-05 audit. `npm audit --omit=dev`: **0 vulnerabilities**. Full `npm audit`: 5 high, all the known dev-only `braces` chain via `eslint-config-next` (GHSA-vfj7-8cjw-p6xm, no fixed version; open since the audit). `pip-audit`: clean. Your own `frontend/node_modules` still has 1.2.1 until you run `npm install` (with `next dev` stopped). |
| 27 | Architecture findings | No partner type, no Reseller, WhiteLabel or Affiliate model, no AdminUser, no new table or migration (heads unchanged), one `ModelBackend`, one login route, a `login_view` that reads only email and password, a staff gate that reads no `is_superuser` and no request input, and no browser storage in the sign-in files. Pinned by `tests/partner_center/test_architecture.py` plus the unchanged Phase 10 architecture tests. A preset is still only registry keys. |
| 28 | Remaining P1/P2/P3 | See §5. |

## 2. Quality gates

| Gate | Result |
|---|---|
| Full backend `pytest` (bare, all testpaths) | see §3 |
| `ruff check backend`, `ruff format --check backend` | pass (670 files formatted) |
| `mypy` | pass (30 files) |
| `lint-imports` | 18 kept, 0 broken |
| `bandit -ll` | 0 medium or high |
| `pip-audit --strict` | no known vulnerabilities |
| `manage.py check --fail-level WARNING` (migration settings) | no issues |
| `manage.py check --deploy --fail-level ERROR` (development settings) | pass; only the 6 transport warnings CI documents as expected under development settings |
| `makemigrations --check --dry-run` | no changes |
| Clean `npm ci` (scratch copy), `tsc --noEmit`, ESLint | pass |
| `vitest` | 428 passed |
| `next build`, `assert-dynamic-rendering` | pass; every route dynamic, so the CSP nonce applies |
| `npm audit` | production 0; dev 5 high (known `braces`, no fix upstream) |
| Production CSP in the real browser | 0 violations on 21 pages |
| axe in the real browser | 0 violations |
| Real-browser journeys | 8/8 |

## 3. Backend test count

**3,388 passed, 0 failed, 0 skipped** on the final tree: bare `pytest -q`, all testpaths, 1 h 44 min. That is up from 3,326, with 62 new tests in `backend/tests/partner_center/`:
- 21 sign-in mode and StaffGrant tests;
- 26 Partner Control Center tests;
- 10 architecture tests;
- 5 contract-shape tests.

The run printed one warning: structlog's existing "Remove `format_exc_info`" notice, in `tests/security/test_ecosystem_security.py`. It is unrelated to this work.

The Phase 10 tests changed to the new intended behaviour:
- The overview card list now includes `total_partners` and `active_affiliates`.
- A list row's `payouts` now includes `paid`.

## 4. Browser journeys (production build, real backend, scratch DB `w360_pcc_fresh`, now dropped)

- **A. Administrator.** Chose "Administrator" by keyboard and signed in. Landed on `/admin`, answered the second-factor prompt, and the dashboard showed a single **Partners** sidebar entry (no Overview/Resellers/White-label/Commissions/Payouts links). Then Partners → Partner dashboard (cards and Recent activity; the local navigation had nine sections with Dashboard current) → Resellers → "Seed Reseller", whose page shows the "Reseller" badge and no Referrals tab. After sign-in, `localStorage` and `sessionStorage` were empty and no script-readable cookie named a mode or role.
- **B. A customer chooses Administrator.** The only admin API answer was `403 /api/v1/admin/session`. The page showed "You don't have administrator access." with no console navigation, no table and no staff text. **Continue as User** led to `/o/customer-company/dashboard`.
- **C. Customer as User.** `/o/customer-company/dashboard`, then My products at `/o/customer-company/apps`, which listed "Example Desk".
- **D. Admin as User.** The path was `/login` → `/dashboard` → `/o/staff-own-company/dashboard`; no `/admin` address at any point.
- **E. Combined partner.** The badges read Reseller, White-label, Affiliate. The tabs included Branding, Domains and Referrals. The partner appeared as exactly one row in All, Resellers, Affiliates and White-label.
- **Deep link.** A signed-out visit to `/admin/partners/affiliates` went to `/login?mode=admin&next=%2Fadmin%2Fpartners%2Faffiliates`; after sign-in and the second factor it returned to `/admin/partners/affiliates`. A hostile `next=https://evil.example/admin` landed on `/admin`.
- **Old addresses.** `/admin/commissions?unpaid=1`, `/admin/payouts?status=open` and `/admin/partners/resellers?scope=all&status=active` all land on their Control Center pages, filters kept.
- **Narrow screens.** At 390 and 320 px the strip scrolls inside itself, the current section is visible, and the page does not overflow.
- **Expected refusals:** 3. Two are the password-only staff session's `mfa_required` before its code; one is the customer's 403.

**Bug the browser check found, and fixed:** the Next.js proxy (`src/proxy.ts`) sends any cookieless request to a bare `/login`, so a signed-out visit to `/admin/...` lost the Administrator mode and its return page before any client code ran. The proxy now uses the same validated `adminSignInUrl`. Unit tests cover the console path, look-alike paths (`/administrator`) and the product `resume` token, which stays the only thing carried for products.

## 5. Remaining items

- **P1:** none introduced.
- **P2:**
  - D-035 (sign-in on a partner's custom domain) is unchanged by this work. Branded and `resume` sign-ins skip the mode choice and are always User.
  - The dev-only `braces` advisory remains open upstream.
- **P3:**
  - The manual screen-reader pass was not executed.
  - The brief's "Products Sold" card is reported as **Active partner subscriptions** (live subscriptions of partner customers). A "products sold" count would need a definition of a sale that the platform does not have.
  - "Pending payout" on the Affiliates list shows unpaid commission plus open payouts; it does not add them together across currencies.
  - Your `frontend/node_modules` needs `npm install` for the source-map-js bump.
