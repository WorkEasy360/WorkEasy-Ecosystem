# Partner business panel: status

Date: 2026-10-16. Decision: [ADR-045](../architecture/adrs/ADR-045-partner-business-panel.md). Browser check: [scripts/partner_panel_browser_check](../../scripts/partner_panel_browser_check/README.md). Freeze check 2026-10-16: all gates below pass on the final tree. Not pushed or deployed.

## Result

**WORKEASY360 PARTNER PANEL: PASS for everything the platform has a model for. NOT-IMPLEMENTED items are listed below, each with its reason; none is faked.**

**Can a partner now log in and operate their reseller, white-label and affiliate business, including customers, sub-partners, products, pricing, commissions, payouts, branding, communication and product management, without weakening the central identity, entitlement, commercial or tenant-isolation architecture?**

**YES, with these stated limits.** Pricing is read-only for the partner (Workeasy360 staff set a partner's prices, as before). Communication is the sender identity and a read of the platform's wording; there is no partner mail server, template editor, alert or delivery log. There is no partner cost, customer limit, recharge, payment link, add-on or partner-authored package, because the platform has no model for them (section 3). Go-live of the portal hosts remains blocked outside the code exactly as recorded in `docs/portals/status.md`.

## 1. What was built

- **Navigation** in the brief's six groups, drawn from the permissions the server resolves; 34 screens under `/partner/<id>/...`. The routes keep the existing `/partner/<partnerId>` shell (ADR-043's partner host serves `/partner/**`) rather than the bare `/dashboard` of the brief.
- **Dashboard**: welcome, date range (7/30/90 days or custom), customers, active subscriptions, transactions, gross sales, commission earned, pending commission, pending payout, sub-partners, a per-day chart (with a table view), quick links, recent customers/commissions/payouts, empty states. Figures come from the commission ledger and the customer projections; a section the role may not see is null, never zero.
- **Customers**: the existing list (owner activation, sell, diagnose) plus joined/trial, first/last name on add, and a bounded, audited, rate-limited, formula-safe CSV export. The owner's **address is deliberately not shown or exported** (the Phase 10e privacy contract: a partner sees whether the owner has activated, never who they are); the brief's Email column is therefore NOT PROVIDED. **Orders/Payments/Subscriptions** are reads over the partner's customers' own orders and subscriptions.
- **Sub-partners** (`PartnerHierarchyLink`): create in draft, equip within the parent's own delegable capabilities and whole-product grants, activate, suspend, resume; one level deep; isolated both ways.
- **Affiliates**: referral links (the sign-up address with the code), conversions (no visitor detail), payouts, the programme's read-only settings (window, rules naming the partner). Sub-partners that hold a referral capability appear with counts.
- **Products / Product Manager / Product Billing / Plans / Pricing / Currencies**: cards, the partner's own switches (enabled for sale, Explore, My Products, order) over its grants, server prices (partner price beside list price), the platform's currency registry, staff-defined packages (read-only).
- **Team** (`Users & Permissions`): invite, change role, remove, resend, MFA state; roles Owner, Admin, Sales, Finance, Support, Marketing, Viewer as presets over capabilities.
- **Settings**: the partner's own business profile, with a time zone and the version check. **Authentication** and **Communication** are statements (and the sender identity link); **Appearance** applies one of six fixed colour schemes through the existing validated brand theme.
- **Activity**: the partner-scoped slice of the one audit log, labelled; changes and staff reasons are not echoed.
- **Demo partner**: `manage.py create_demo_partner` (development and test only).
- **Temporary password rule**: `User.password_change_required` and the middleware.

## 2. Evidence

| Gate | Result |
|---|---|
| New backend tests (`backend/tests/partner_panel/`) | **125**, all pass (architecture 15, customer presentation 4, dashboard/money/export 21, demo partner and password 15, hierarchy 19, scale 10, products/profile 23, roles/team 18) |
| Full backend suite (bare `pytest`), final tree | **4,396 collected, 4,396 passed, 0 failed, 0 skipped** (1 h 21 min); see section 4 |
| ruff, ruff format | clean |
| mypy (contracts, strict) | clean |
| lint-imports | 18 contracts kept, 0 broken |
| bandit (medium and above) | 0 |
| pip-audit --strict | no known vulnerabilities |
| `manage.py check`, `makemigrations --check` | no issues, no changes |
| `check --deploy` | production-like environment (the security baseline's variables, a random secret key): **no Django security warning**. The four `W360.W002/W010` checks that read the database could not run, because production settings refuse the dev database password; they run against the test database in the suite |
| Frontend: clean `npm ci`, tsc, eslint, vitest | clean / 0 / 0 / **795 passed** in 41 files (774 before; 21 new) |
| `next build` (scratch copy, clean install) | clean; every new route dynamic |
| npm audit | runtime 0; dev-only 5 high in the accepted `braces` chain (unchanged) |
| Real browser (production build of the final sources, real backend; re-run at the freeze) | **15/15 journeys pass**; 38 pages audited: **axe 0, CSP 0, console errors 0**, focus visible, one `h1` each; **reflow 170/170** at 1440, 1024, 768, 390, 320 px |
| Secret scans (CI's detect-secrets against the audited baseline; gitleaks) | 0 unaudited findings; no leaks. The browser-check scripts take a per-run random password (`CHECK_PASSWORD`) instead of a fixed one |
| Manual screen reader | **NOT EXECUTED** |

A real bug the browser check found and that is fixed: at 320 px the sub-partner form's grid had a 300 px minimum column and overflowed the page by 21 px. All `auto-fit` grids now shrink below their minimum.

## 3. The brief, item by item

1. **Partner login.** Signed out goes to sign-in (browser A1); a customer who is no member sees "no access" and API 404 (A2); the owner reaches the dashboard (A3). Super Admin is not a partner (membership is the only door; unchanged and re-tested). **PASS.**
2. **Demo credential.** `create_demo_partner`: refuses production, staging and any unknown environment *before* touching the database; the password is 24 random characters from the OS source, shown once and only on an interactive terminal (piped output is refused before anything is created); only the hash is stored; the account must change it at first sign-in (browser A5: every other call is 403 `password_change_required`; the temporary password stops working). Demo email `partner.demo@workeasy360.test`. **The temporary password has NOT been displayed to you:** this session's terminal is not interactive and the command correctly refuses to print into it. Run it yourself, once, in your own terminal (section 7). **PASS (command), password step is yours.**
3. **Dashboard.** PASS. **Partner cost**: NOT IMPLEMENTED (no wholesale-cost model). **Customer limit**: NOT IMPLEMENTED (no commercial rule). Both are shown as explanations, not zeros.
4. **Customers.** PASS. Owner email: deliberately NOT PROVIDED (privacy contract above). Country and last activity: NOT IMPLEMENTED (an organization has no country; activity would come from inside the products). "Suspend relationship" by the partner: not offered (staff end or suspend links).
5. **Sub-partners.** PASS. Revenue/commission of a sub-partner: NOT IMPLEMENTED (no wholesale chain; commission is staff-enabled per partner).
6. **Affiliates.** PASS for links, conversions, payouts and read-only settings. Per-partner programme settings (enable/disable, attribution window, allowed offerings): NOT IMPLEMENTED (platform settings).
7. **Plans.** PASS (read; a partner cannot create a global plan).
8. **Add-ons.** NOT IMPLEMENTED (no purchasable add-on in the catalog).
9. **Currencies.** PASS (the platform's registry; no FX; never summed).
10. **Pricing.** Read-only fixed prices; partner-authored packages and rules NOT IMPLEMENTED.
11. **Partner balance.** PASS as the append-only commission ledger (pending, available, in a payout, paid; per currency). No mutable balance exists (pinned).
12. **Payments.** PASS as a read of the partner's customers' orders.
13. **Payment links / recharge.** NOT IMPLEMENTED (no partner payment model).
14. **Product billing.** PASS.
15. **Brand settings / 16. Domains.** The existing screens, unchanged; domains still need DNS proof; custom-domain sign-in stays on the central page (D-035 not weakened; no wildcard origins).
17. **Product manager.** PASS (cards, order, visibility; presentation only).
18. **Appearance.** PASS for six validated colour schemes. Custom CSS/JS deliberately NOT offered; login background, landing cards, preloader, navigation style: NOT IMPLEMENTED (no storage for them).
19-22. **Communication.** Channels (email live; webhook/WhatsApp/SMS not available), templates (read), alerts and logs: NOT IMPLEMENTED. **Partner SMTP** deliberately not built (a partner-chosen host makes the platform connect anywhere); nothing stores or returns a mail credential.
23. **Integrations.** NOT IMPLEMENTED for partners (connectors belong to customer organizations).
24. **Partner products.** PASS (granted vs available; a partner cannot grant itself a product).
25. **Auth configuration.** A statement only; nothing a partner can change.
26. **Shared resources.** NOT IMPLEMENTED (no cross-tenant file model).
27. **Users and permissions.** PASS.
28. **Activity.** PASS.
29. **Settings.** PASS.
30-33. **Reseller, white-label, affiliate, hierarchy.** One `PartnerAccount`; labels derived; pinned by tests.
34. **Financial security.** Integer minor units; currencies never added; the ledger is append-only and not writable from the panel; no recharge/credit route exists (pinned); the export is bounded.
35. **Secret security.** No mail credential anywhere; communication answers carry no credential field; the temporary password is never stored in clear.
36. **Isolation.** Partner A to B (every read answers 404, browser I), sub-partner to parent (404) and parent to child (counts only), customer to partner (404), finance to customers (403), IDOR on member id, child id, product code (404).
37. **Responsive.** 170/170.
38. **Accessibility.** axe 0 on 38 pages; manual screen reader NOT EXECUTED.
39. **Journeys.** 15/15.
40. **Performance.** Query counts are **identical at 10, 100 and 500 rows** for dashboard (28), balance (17), sub-partners (16), team (16), products (25), plans (18), orders (12), activity (14), affiliates; wall time in a test process 40-140 ms at 500 rows. Customers are paged by cursor (100 max); the export runs 3 queries per 100 rows, capped at 5,000 rows.

## 4. Final full backend suite

Bare `pytest` from the repository root on the final tree, after every change of this work: **4,396 collected, 4,396 passed, 0 failed, 0 skipped**, 1 h 21 min. The earlier complete run (4,390 of 4,395) had five failures that were consequences of two deliberate changes (the session payload's `password_change_required`; the customer list's `access_class` and a short-lived `owner_email`, removed again for the Phase 10e privacy contract); they were fixed and the whole suite now passes in one run.

## 5. Security notes

- Rate limits: customer export (5/min/user), sub-partner and customer creation (existing provisioning bucket), team invite (existing invite bucket); domain verification and referral endpoints keep theirs.
- Dangerous actions (team changes, sub-partner changes, customer provisioning, selling, domains) need the second factor; reads never do.
- The commission rate becomes derivable by a partner (gross beside commission, rules naming it) because the brief asks for both; ADR-045 records this.
- Background jobs: the panel adds none.

## 6. Existing expectations that intentionally moved

`test_the_dangerous_permissions_are_exactly_the_pinned_set` (two new dangerous keys), `test_only_a_partner_manager_with_a_second_factor_can_invite` (a partner's own administrator may now invite into its own partner; into any other it is 404), `test_the_role_must_be_a_known_preset` (owner is a role now), the two model/migration pins, and the dangerous-permission step-up test (the team permission belongs to no capability).

## 7. Open items

- **P2 (yours): create the demo credential** in your own terminal: `python backend/manage.py create_demo_partner` (from the repo root with your dev settings). It shows the password once.
- **P2: dev database.** Migrated at your approval after a backup (`workeasy360-before-partner-panel-*.dump`). The Docker containers had stopped after a restart; I restarted them with `docker compose -p workeasy360 start`.
- **P3:** manual screen-reader pass; `check --deploy` under a full production environment.
- **Outside the code (unchanged from ADR-043 status):** DNS and TLS for the five hosts, product OIDC clients, providers, backups, monitoring.
