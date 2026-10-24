# Simplified Super Admin partner management: status

Date: 2026-10-23. Decision: [ADR-048](../architecture/adrs/ADR-048-simplified-partner-management.md) (builds on ADR-047). Browser check: [scripts/partner_management_browser_check](../../scripts/partner_management_browser_check/README.md). **Not committed, not pushed, not deployed** - the owner tests the flow by hand first.

## Result

**SIMPLIFIED PARTNER MANAGEMENT: the feature works, one gate is red.** The full backend re-run (section 5a) passes 4,716 of 4,717; the one failure is an architecture rule that the guest-login addendum tripped (two redundant `is_staff` reads in `partner_views.py`), awaiting the owner's choice of fix. Every other gate is green (the 451 Docker errors of the earlier run are resolved).

**Does Super Admin Partner Management now contain only Dashboard, All Partners and Add Partner, while partner creation requires only the essential credentials/type and all detailed Reseller, White-label or Affiliate setup is completed later through that Partner's Complete Your Profile workflow?** **YES.**

The same caveat as ADR-047 holds for the sign-in host: the credentials work at the Workeasy360 partner portal (`/login?mode=partner`, and the `partner.` host the multi-portal configuration serves). `partner.workeasy360.com` in production currently serves the separate Partner Panel app (decision of 2026-10-20); pointing it at this portal is an owner/edge step.

## 1. What was built

- **Navigation**: Partners = Dashboard, All Partners, Add Partner (`partnerCenterNav.ts`). Eleven old addresses redirect: Resellers / White-label / Affiliates to All Partners filtered on the type; Customers, Products & Pricing, Commissions, Payouts (and `/admin/commissions`, `/admin/payouts`) to All Partners; the old add pages to Add Partner with the type; `white-label/<id>` to that partner's Complete Your Profile at the brand step. The cross-partner screens behind them were removed from the frontend; backend reads and commands are unchanged.
- **Dashboard**: Total, Active, Resellers, White-label partners, Affiliates, Suspended, Profiles incomplete (each a link to the matching All Partners filter and the same size as it), and Recent partners with type badges, completion and state. No MRR/ARR/growth.
- **All Partners**: one table - Partner Name, Admin Email, Partner Type badges (several for a combined partner), Profile Completion, Products, Customers, Status (*Setup required* / *Ready* / Active / Suspended / Terminated), Created, Last Login, Actions (View Details; Suspend / Reactivate with a recorded reason). Server-side filters: Partner type (by grants), Status, Profile complete/incomplete (the SQL form of the completion rule), and search by partner name or any email it is reached at (administrator, contact, business). Filters live in the address.
- **Add Partner** (`/admin/partners/new`): Partner name, Admin email, Password, Confirm password, Partner type cards (Reseller / White-label / Affiliate with the brief's descriptions), Country (optional) - nothing else. Creates a **draft** partner, the type's capability bundle, the owner membership and the login, atomically; the code is derived from the name, the preferred currency from the country. Existing email: "This email already belongs to an existing Workeasy360 account." with **Link Existing User as Partner Administrator** or **Cancel**; the existing password is never touched. Then it opens the partner's page with "Partner created successfully." and PROFILE INCOMPLETE / **Complete Your Profile**.
- **Partner Detail**: header with name, type badges, state, completion (percentage + what is still needed) and Complete Your Profile / Edit profile; Activate (only when complete), Suspend, Reactivate, Terminate. Sections: Overview (incl. the completion list), Profile, Products, Users & Permissions (capabilities, experience, members), Activity, and per capability Pricing, Commission, Customers (+ subscriptions), Branding, Domains, Referrals, Payouts.
- **Complete Your Profile** (`/admin/partners/<id>/profile`): steps follow the server's labels (union for a combined partner) - Reseller 8 steps, White-label 10 (with the reseller's commission step, since its bundle sells, and an optional customer-portal step), Affiliate 7. Each step is an existing panel saving through an existing command; completion is shown beside the steps; Review → Activate partner.
- **Profile completion** (`controlplane/profile_completion.py`): server-side, per capabilities held; optional sections (prices, custom domain) shown, never counted; percentage of required sections, floor (100% only when complete). One rule in Python and SQL, held equal by a test.
- **Staff referral codes**: `POST admin/partners/<id>/referral-codes` (needs `staff_partner.manage`, the partner must hold `referral.create`, never a terminated one).
- **Preferred currency** changeable while the partner is a draft with no price book; fixed afterwards. Time zone added to the profile form (a curated list of common IANA zones).

## 2. Brief checklist

| # | Item | State |
|---|---|---|
| 1 | Partner Dashboard | PASS - seven factual counts + recent partners |
| 2 | All Partners | PASS - the ten columns |
| 3 | Partner type filter | PASS - server-side, combined partners in each relevant view |
| 4 | Add Partner | PASS - name, email, password x2, type, optional country |
| 5 | Admin-created email/password | PASS - hashed, never stored/logged/audited/returned; no temp password; existing account never overwritten |
| 6-8 | Reseller / White-label / Affiliate creation | PASS (browser journeys A-C) |
| 9 | Partner Detail | PASS |
| 10 | Complete Your Profile | PASS - dynamic steps |
| 11-13 | Reseller / White-label / Affiliate profile fields | PASS for every field the platform models; see section 4 for brief fields it does not |
| 14 | Profile completion | PASS - server-side, per capability, optional sections not counted |
| 15 | Product grants | PASS - `PartnerProductGrant`; affiliates granted none |
| 16 | Pricing | PASS - existing price book / partner prices; server computes figures |
| 17 | Commission | PASS - `CommissionRule` versions, never backdated |
| 18 | Branding | PASS - `PartnerBrand` (name, logo, accent, support) |
| 19 | Domain | PASS - `PartnerDomain` lifecycle and DNS proof; D-035 stated honestly |
| 20 | Partner login | PASS at the Workeasy360 partner portal (host caveat above) |
| 21 | Correct workspace | PASS - reseller / white-label / affiliate dashboards |
| 22 | Multi-capability | PASS - union of steps and requirements; badges; filters |
| 23 | Security / isolation | PASS - section 3 |
| 24 | Redirects / removed navigation | PASS - 11 redirects verified in the browser |

## 3. Evidence

| Gate | Result |
|---|---|
| Full backend suite (bare `pytest`) | first run: 4,703 collected, 4,688 passed, 15 failed - all tests pinning behaviour this brief changed (old dashboard cards, old readiness keys, fixed currency) or three architecture rules (a `partner_type` identifier, an inline style, a pricing import in `profile.py`); fixed in code or updated, and the 8 affected modules re-run: 164 passed. **Final full run (all fixes in): 4,480 passed, 0 failed, 451 errors** - every error is `the connection is closed` after Docker Desktop's engine failed mid-run (its API answers 500); the 451 tests did not execute and must be re-run once Docker is restarted |
| New backend tests | `tests/partner_management/` (completion per type, SQL = Python over a matrix, filters, dashboard, staff referral codes, currency, the brief's security checks); `tests/partner_experience/` adapted to create-then-complete, plus race tests on derived codes |
| ruff, ruff format, mypy, lint-imports (18 contracts), bandit (medium+), pip-audit --strict, `manage.py check`, `makemigrations --check` | clean / clean / clean / 0 broken / 0 / 0 / no issues / no changes (no migration in this change) |
| Frontend (scratch copy): clean `npm ci`, `next typegen` + tsc, eslint, vitest | clean / 0 / 0 / **812 passed in 46 files** |
| `next build`, `assert-dynamic-rendering` | clean; every route dynamic |
| npm audit | production 0; CI policy passes (only the accepted `braces` advisory, lint tooling) |
| Real browser (production build, real backend, headless Chrome) | **8/8 journeys** (N, A, B, C, F, E, D, E2); **24 pages**: axe WCAG 2.1 A/AA 0, CSP 0, console errors 0, focus visible, one h1 each; reflow **100/100** at 1440-320 px |
| Secret scan | detect-secrets 1.5.0 on Linux: 0 unaudited (baseline refreshed: entries of removed code dropped) |
| Independent review (read-only) | 0 P0/P1; one P2 (Review-step links did not move the step) and four P3s fixed and tested; remaining P3s in section 4 |
| Manual screen reader | **NOT EXECUTED** |

Security checks (backend tests + browser): plaintext password never persisted/logged/audited/in outbox; existing email not overwritten; affiliate refused customer/pricing/brand/domain endpoints; reseller without the domain grant refused domain and brand configuration; partner A refused partner B; suspended partner keeps history, loses actions (ADR-047 tests); partners refused every staff product-grant, price, commission, capability, experience, status and referral-code command; a draft with a complete profile still cannot use the portal; redirects build fixed paths only (no open redirect); names render escaped (XSS test kept).

## 4. Open items

**P1: none.**

**P2 - owner decisions**

1. `partner.workeasy360.com` serves the separate Partner Panel (ADR-047 P2-1, unchanged).
2. **Activation is gated by completion in the console, not in the status service** (the service's one hard rule remains "an administrator exists"; other flows and tests activate through it). Enforcing completion server-side is a one-line rule change if wanted.
3. **The cross-partner payout queue and commission ledger are no longer in the navigation** (the brief removes them); payouts and commission are reached per partner. If finance needs a cross-partner queue, it would be a filter on All Partners or a report.
4. Dev database: no new migration, but ADR-047's `partners.0007` is still not applied to your local database (needs your go-ahead).
5. ADR-047's open owner decisions (no forced password change; linking makes the person an owner) are unchanged.

**P3**

- Brief fields the platform does not model (stated on the screens or in ADR-048): brand icon, favicon, secondary colour, privacy/terms URLs; commission type other than a percentage, plan-level scope, per-partner hold period; payout threshold; marketing library; partner SMTP; "May manage renewals" is part of manage subscriptions (no separate capability).
- "A commission rule applies" counts the programme default and a 0% rule (Python and SQL agree).
- The credentials-check endpoint (not used by the new form) does not list a problem for a disabled account in link mode; `create` refuses it.
- jsdom drops the space before visually-hidden text in accessible names (tests match both); browsers read it correctly.
- Passwords are not Unicode-normalised (platform-wide, pre-existing). Manual screen-reader pass NOT EXECUTED.

## 5. Owner steps

1. `npm install` in `frontend/` (lockfile from ADR-047), and apply `partners.0007` to the dev DB when you agree.
2. Try it: `/login?mode=admin` → Partners → + Add Partner → the partner's page → Complete Your Profile → Review → Activate; then `/login?mode=partner` with the typed email and password.
3. Commit only after you have tested.

## 5a. Re-verification after the addendum (2026-10-23, later session)

- **Full backend suite (bare `pytest`, Docker healthy): 4,717 collected, 4,716 passed, 1 failed** - the 451 Docker errors of section 3 are gone. The one failure is `tests/architecture/test_phase7_architecture.py::test_django_is_staff_is_never_an_authority`: the guest-login addendum added two reads of `user.is_staff` in `interfaces/api/v1/partner_views.py` (`_guest_entry`, `guest_view_end_view`), a file the rule does not list. Neither read is an authority (the guest view is a cache entry bound to the staff member's user and sign-in id, written only by `guest.start` after `staff_partner.manage`; `guest_context` re-checks the grant on every request; `guest.end` only deletes the caller's own entry), so both can simply be removed, or the file allow-listed with a reasoning comment. **Not yet changed: it edits a security rule's surroundings and awaits the owner's choice. The suite is red until then.**
- Frontend (clean scratch `npm ci`): tsc 0, eslint 0, **vitest 817 passed in 46 files**, `next build` clean and every route dynamic, production `npm audit` 0, full audit only the accepted `braces` advisory.
- Backend static gates re-run: ruff / ruff format (backend, as CI) / mypy / lint-imports (18 kept) / bandit / pip-audit --strict / `manage.py check` / `makemigrations --check` all clean.
- **Real browser, re-run against the addendum build: 8/8 journeys, 24 pages, axe 0, CSP 0, console errors 0, focus 0, one h1, reflow 100/100.** The harness had to follow the addendum (Add Partner lands on All Partners with `?created=<id>`, not on the partner's page; journey F checks the twelve-column table). The guest-login flow has no browser journey (backend tests and vitest only).
- **Two places the addendum departs from the brief text**, both owner-requested ("agency table") but worth confirming: (1) Add Partner lands on All Partners rather than directly on Partner Detail; (2) All Partners has no Products, Customers or Profile Completion columns (type badges and setup state are in the Name cell; products and customers are on the partner's page).

## 6. Addendum (2026-10-23): agency-style All Partners and Guest login

Owner request: the partner list should look like an agency table, keeping **Complete setup** and **Guest login**.

- **All Partners table**: Status (switch: Active ↔ Suspended, with a recorded reason; disabled for a draft, which is activated from its page once complete, and for a terminated partner) · Action (⚙ Complete setup / Edit setup → Complete Your Profile; ↪ Guest login) · Panel (the partner portal link, copy) · Partner ID (short, copy) · Name (link to the detail page, type badges, setup state) · Email (copy) · Phone · Country · Currency · Wallet (commission balance per currency, as the server summed it) · Last Login · Joining Date. List rows gain `phone` and `country` from the profile (one query per page).
- **Add Partner** now lands on All Partners (`?created=<id>`, validated as a UUID) with "Partner created successfully." and the new row marked; the detail page is one click away.
- **Guest login** (`commercial/partners/guest.py`): `POST admin/partners/<id>/guest-view {reason}` needs `staff_partner.manage` (dangerous: recent second factor, audited, refused under impersonation). It opens that partner's portal **read-only** for **30 minutes**, for the staff member's own sign-in only (grant in the cache under the sign-in id; an unreadable cache means no guest view). The staff member is never given the partner's identity: requests carry a `PARTNER` context whose user is the staff member, `is_break_glass` with the reason. The portal adapter refuses every unsafe method (`guest_read_only`), the resolver gives a guest the viewer tier only, dangerous permissions and the customer export are refused. Start and end are audited against the partner and show in **the partner's own Activity** ("Staff opened a guest view"). The portal shows a banner on every page with **Exit guest view** (`POST partner/guest-view/end`). Opening another partner ends the first; losing the staff grant ends access at once.
- Tests: `backend/tests/partner_management/test_guest_view.py` (12); frontend `partner-console.test.tsx` (table, copy, guest login, created marker), `controlplane.test.tsx` (guest banner), `add-partner.test.tsx` (lands on All Partners).
- Not done: a write-capable guest login (the architecture requires a second approver for read-write impersonation, 12-security.md); the real-browser harness was not re-run for this addendum.
