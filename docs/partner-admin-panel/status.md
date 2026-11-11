# Partner Admin Panel (specification of 08 November 2026): status

Branch `feat/partner-admin-panel`, cut from `feat/admin-panel` at `dccf643` (the live release). **Not committed, not pushed, not deployed.** Nothing was run against production, and the local development database was not migrated. Decisions: [ADR-063](../architecture/adrs/ADR-063-partner-admin-panel-eight-menus-wallet-and-onboarding.md). Audit before the work: [00-gap-analysis.md](00-gap-analysis.md). Browser check: [scripts/partner_admin_panel_check](../../scripts/partner_admin_panel_check/README.md).

## Results

Only what actually ran, on the uncommitted working tree.

| Check | Result |
|---|---|
| Backend: new tests (`tests/partner_admin_panel`, 5 files; `tests/guest_login/test_guest_audit.py`; a White-Label-with-Reseller creation test) | **44 / 44** |
| Backend: the whole suite (bare `pytest`, real PostgreSQL) | **5,428 / 5,428 passed** (1 h 40 min, 0 failed) |
| Backend: the affected folders before the fixes | 2,606 passed, 20 failed. All 20 pinned the old rules on purpose (White Label holding the reseller set, a draft holding no permission, the dangerous-permission set, the route test forbidding a wallet route, two choices-only migrations); each was updated to the new rule, and the 266 tests involved then passed |
| Frontend: vitest, whole suite | **1,586 / 1,586** (91 files) before the last two fixes; the affected suites re-ran after them (partner panel 273, partner console 43) |
| Real browser, production build, five hosts, KYC required | **59 / 59**: 0 console errors, 0 CSP violations, 0 unexpected API errors, axe clean on every page checked |
| Static | `tsc` clean; `eslint` clean on every changed file; `ruff check` and `ruff format --check` clean; import contracts 18 / 18; `mypy` (CI scope) clean; `bandit -ll` clean on the new modules; `makemigrations --check` no changes; `detect-secrets` nothing new beyond the baseline (one test reference name allowlisted, as the earlier seeds do) |

## A. Existing features preserved

Login, invitation and accept-invitation, forgot and reset password, sessions; the Partner Email form; KYC (six states, review, corrections, approval, rejection, reviewer, decision log, document access log, fail-closed scanning); two-step activation; the shared wallet ledger; referral links, codes, campaigns, capture and conversion; commissions, clearance, payouts and withdrawals with verified bank/UPI details; customers, owner invitations, partner orders; tickets and the customer desk; staff, roles, password, sessions; white-label branding and domains; the upgrade flow; Login as Guest's records, ticket and read-only middleware. **Every page the earlier menus served keeps its address** (a test walks the old addresses of all three workspaces).

## B. New features, by menu

| Menu | What is new |
|---|---|
| Dashboard | KYC / Account status card (separate facts, permissions held); Referral Performance with clicks, registrations, eligible conversions and paid renewals; the Platform Wallet card beside - not added to - My earnings (pending, approved, available, paid); Quick Actions (Copy Referral Link, Activate Customer Plan, Add Money, Contact Support, Create Customer); before approval, an onboarding dashboard (status, the seven steps, the next step) |
| Products & Referrals | Eligible Products (the Store referral link, products to promote with their commission terms and a link generator, products to sell with list, purchase and customer prices); Sales Resources (listing images, video, approved description, partner sales guides); referral links, results, Product Store and Pricing & Margins as tabs |
| Customers | Products & Setup (plans and validity, members, each product's setup state and requirements - never the answers - and product website / app links); Plan Activation (customer, product, plan, cycle, seats, the approved purchase price, the wallet deduction, confirm, the order) |
| Billing & Subscriptions | Customer Subscriptions with Active / Pending Setup / Expiring / Expired filters and counts, search, who pays, the order and its payment state, View (history), Renew and Change Plan (wallet-paid); Billing Records (payments to Workeasy360 with status, wallet payments for plans); Customer Orders |
| Platform Wallet | Balance & Recharge (balance per currency, Add Money online, top-up status, recent movements); Transactions (every movement with its order and customer, filter by type) |
| Earnings & Payouts | Commissions, Payouts, Disputes as tabs (existing pages) |
| Help | Help Content (partner FAQs, setup instructions, sales guides); Support Tickets and Customer Desk as tabs |
| Settings | Business & KYC (business profile, the KYC application with its decision and history); My Account & Security (password, sessions, Sign out all other sessions); Staff; White Label (only with branding or domain permission); Communication; Sign-in & API |
| Super Admin | Partner list columns and row actions; Login as Guest asks for the reason; "Also allow" base permissions on New Partner, KYC approval and White Label upgrade approval; Settings → Partner policies; View Partner Wallet; the `sales_guide` help kind |

## C. The specification, requirement by requirement

| § | Requirement | Where it is, and its evidence |
|---|---|---|
| 1.1 | New Partner form, Partner Email, types, invitation or password | Existing form; **White Label offers explicit base permissions** ("Also allow"). `test_create_partner.py`, `add-partner.test.tsx`, browser S7-S8 |
| 1.1 | Creating does not approve KYC or activate | `TestNewPartnerForm` (draft, no commercial access); seed shows a created draft stays draft |
| 1.2 | Listing columns and actions | `PartnerConsole.tsx`; `partner-console.test.tsx`; browser S1-S2. Earnings are not brought back |
| 1.3 | Login as Guest: authorized only, scoped, banner, exit, no password, audited (reason, start, end, id, actions), read-only, server-side, revoke on exit, expiry | Existing ADR-062 session plus the reason dialog, the banner wording and per-request audit. `tests/guest_login` (50 + the new audit test), `partner-console.test.tsx`, `controlplane.test.tsx`; browser S3-S6 |
| 2.1 | Invitation, acceptance, verification, password, sign-in, reset, sessions | Existing (ADR-059/061); browser signs in on `partner.` and `admin.` |
| 2.2 | KYC states; account states separate; before approval only onboarding; blocked operations enforced on the backend | `kyc.cleared`, the resolver's onboarding tier, `require_transacting`, referral capture. `test_onboarding_and_permissions.py`; browser D1-D7 |
| 2.3 | KYC review; no auto-approval; restricted documents; scanning; legacy handled explicitly | Existing review; legacy = Admin policy (`partner_legacy_kyc_policy`), never a forged approval. `test_a_legacy_partner_trades_unless_the_admin_restricts_it` |
| 3 | Exactly eight menus; tabs; filters; actions; deep links; only permitted modules | `experiences.ts`; `experiences.test.ts` (25), `workspace.test.tsx` (42); browser R2, A1, W1, D2 |
| 4.1.1-4.1.3, 4.2 | Dashboard cards and quick actions, permission-gated | `UnifiedDashboard.tsx`, `partner_portal.dashboard`; `unified-dashboard.test.tsx`, `test_reads.py`; browser R3-R6 |
| 5.1 | Eligible products, plans, purchase rates, commission terms, referral link, Store referral link | `ProductsReferrals.tsx`; `referrals.LANDINGS`; browser A2-A4 |
| 5.2 | Referral results from platform events; no attribution unless KYC/account allow | Existing results; capture refuses an uncleared partner. `test_a_restricted_legacy_partner_earns_no_new_attribution` |
| 5.3 | Published, authorized sales resources | `partner_billing.sales_resources` (granted products only, published only) |
| 6.1 | Assigned organizations only; create, invite, view | Existing (customer list, creation, invitations); other partner's customer is a 404. `test_another_partners_customer_cannot_be_activated`, browser A7 |
| 6.2 | Products & setup, links without impersonation | `partner_billing.customer_products`, `ProductsSetup`; `test_a_customers_products_and_setup` |
| 6.3 | Plan Activation steps 1-8, all server checks, atomic, no duplicate charge, references, renewal and plan change | `orders.place_paid_by_partner_wallet`, `partner_wallet.activate/renew/change_plan`. `test_wallet_activation.py` (13), `test_wallet_renewal.py` (7); browser R7-R14 |
| 6.3 | Customer AI Credits and Consumption Wallet untouched | Only the partner's Platform Wallet is debited (`owner_type=partner`, `kind=platform`); no code path touches organization wallets |
| 7.1 | Subscriptions with filters, separate states, actions | `partner_billing.subscriptions`, `CustomerSubscriptions`; `test_the_subscription_filters`; browser R12-R13 |
| 7.2 | Billing records, own only | `partner_billing.billing_records`; browser R16 (top-up listed) |
| 8.1-8.2 | Balance, Add Money, status, history; transactions with references; pays for plans; one ledger; no overdraft; idempotent; audited; reversible | `partner_wallet`, `partner_fees` purpose; `test_an_online_top_up_credits_the_wallet_once`, `test_insufficient_balance_charges_nothing_and_grants_nothing`; browser R14-R17 |
| 9.1-9.2 | Commissions and payouts; never above available; no earnings-to-wallet | Existing (withdrawals take no amount); a wallet-paid sale earns no margin (`test_a_wallet_paid_sale_earns_the_reseller_no_margin`) |
| 9 | Suspended partner policy | `partner_suspended_commission_policy`; `test_a_suspended_partner_stops_earning_when_the_admin_says_so` |
| 10.1-10.2 | Tickets in the shared system; help content | Existing tickets (open before approval: browser D6); `HelpContent`; browser R22 |
| 11.1 | Business & KYC with decision, reason and history | `BusinessKyc.tsx`, `kyc.application` history; `partner-admin-panel.test.tsx`; browser R20, D5 |
| 11.2 | Staff, roles never above the partner's grants, security, sign out others | Existing staff; `test_a_role_never_exceeds_the_partners_grants`; browser R21 |
| 11.3 | White Label settings only when approved | Tab needs branding/domain view; browser R19, W2; `workspace.test.tsx` |
| 12 | Permission matrix; White Label grants no reseller/affiliate | `EXPERIENCE_BUNDLES[white_label]`; `test_white_label_alone_sells_refers_and_earns_nothing`; browser W1, W3 |
| 13 | Shared canonical records | Wallet-paid sales write the same Order, Subscription, SubscriptionItem and WalletEntry rows |
| 14 | RBAC, isolation, CSRF, rate limits, audit, idempotency | `partner_view` on every new route; `test_csrf_is_enforced_on_the_new_writes`; new `partner_wallet` rate-limit bucket; browser A6-A7 |
| 15 | Tests | All of the above |
| 16 | Responsive, accessible, all states | Browser R18 (390 / 768 px), axe on every checked page; loading/empty/error states via `LoadGate`/`EmptyState` |

## D. Bugs fixed

| Bug | Root cause | Fix |
|---|---|---|
| A partner's Platform Wallet could not pay for anything | No code debited a partner wallet; partner orders either invoiced the customer or were fulfilled with nothing charged (`partner_billed`) | The `partner_wallet` collection mode and the wallet-paid order (ADR-063 §6) |
| A partner could not see or top up its wallet | No partner route; tests forbade one | Partner wallet reads and online top-up through the existing partner checkout |
| White Label partners could sell without being approved to | The White Label bundle included the reseller's set | White Label is branding and a domain; selling is granted explicitly |
| A draft partner could not even raise a support ticket | A draft held no permission at all | The onboarding tier |
| The guest reason was never the Admin's | `autoReason` filled it in | A reason dialog |
| A partner's online top-up would have returned to a missing page | The return address pointed at the generic panel | It returns to the partner's own workspace wallet page (found by the browser check) |
| The Country and KYC columns vanished on lists where every row was blank | The table hides an all-blank column | Their values are never blank |

## E. Permission evidence

| Who | Evidence |
|---|---|
| Pending KYC / draft | Onboarding tier only (`test_a_draft_holds_the_onboarding_tier_and_nothing_commercial`); support works, trading refused (`test_a_draft_can_reach_support_but_not_trade`); browser D2-D7 |
| Active Affiliate | Six menus; no customers, wallet or pricing (`experiences.test.ts`, browser A1, A5, A6) |
| Active Reseller | Eight menus; activation, wallet, renewal, plan change (browser R; `test_wallet_*`) |
| Approved White Label | Branding and domain only, no customer creation (browser W1-W3, `test_base_permissions_beside_white_label_are_explicit`) |
| Suspended partner | Cannot activate (`test_a_suspended_partner_cannot_activate`); read tier unchanged; commission policy as chosen |
| Legacy (active before KYC) | Trades by default; restricted when the Admin chooses (`test_a_legacy_partner_trades_unless_the_admin_restricts_it`) |
| Cross-partner | Another partner's customer or wallet is a 404 (`test_another_partners_customer_cannot_be_activated`, `test_nobody_else_reads_them`, browser A7) |

## F. Login as Guest

- **Entry point**: the Login button in the Guest Login column and the row menu of every list, and the partner's own page; both open the reason dialog first.
- **Scoped session**: unchanged from ADR-062 - a database record, 30 minutes, one-time 256-bit ticket posted across hosts, exactly one partner, the viewer tier, redactions.
- **Read-only**: every unsafe method refused before any view (browser S5, `test_every_write_is_refused_on_every_endpoint`).
- **Audit**: start and end (existing), and now every API request of the view with the reason, the staff member, the path with ids masked and the answer (`test_each_request_of_a_guest_view_is_audited_with_the_reason`).
- **Exit and expiry**: "Exit Guest Session" ends it and returns to the Admin page, still signed in (browser S6); expiry and revocation unchanged (`tests/guest_login`).

## G. Tests

See Results above.

## H. Remaining work and open decisions

| Item | Why it is open |
|---|---|
| Grace on an unpaid wallet renewal | Today the lines end with the paid period if the wallet cannot pay. A grace period and reminders are an owner decision |
| GST on wallet purchases and top-ups | None is charged; the tax treatment of partner prepayment is for the accountant |
| Online top-ups need a live payment provider for the partner's currency | Verified with the test provider only; no live gateway is configured |
| Legacy partners' policy | Default `allow` keeps every live partner trading. Switching to `restrict` needs a malware scanner in production first, or no KYC can be approved (ADR-061) |
| Suspended partners' commission | Default `accrue` (today's behaviour) |
| Moving a customer that already pays directly onto the wallet | Refused with an explanation; a migration of billing terms is a staff decision |
| Quantity changes on a wallet-paid line | Refused (use Change Plan); per-seat price models would need a seat-change charge rule |
| The partner levels matrix and upgrade card still describe White Label as the top selling level | Presentation from ADR-057; approval now asks explicitly whether to grant Reseller beside it |
| Development database | Not migrated: three migrations (administration.0004, billing.0002, subscriptions.0006) wait for the owner's word |

## I. Deployment readiness

**Not deployed; ready for review, not for production without the owner's decisions above.** The code is complete and tested end to end on a production build. Before a deployment: approve the three migrations (all additive or choices-only, `overlap_safety` declared), confirm the two partner policies (their defaults keep production's behaviour), and decide renewal grace and GST. A live payment provider is needed for online top-ups; offline top-ups by staff keep working.

## Addendum, 11 November 2026: Partner sign-in and sign-up to the owner's reference image

Same branch, also **not committed, not pushed, not deployed**. No migration.

- **Partner Login** (`partner.` `/login`, single-host `/login?mode=partner`): the Admin sign-in's two-sided frame (`AuthSplitFrame`, shared) with the partner's story - Partner Portal pill, "Grow Your Business with WorkEasy360", the four features beside an abstract laptop (no figures), Our Product Ecosystem (OneClickAPI, SailoAI, VyniAI, RCS / SMS, Website Builder, and More Products - the names from the image) - and the card: Partner Portal badge, Email Address, Password with show / hide, Remember me, Forgot Password?, Login to Partner Portal, New to WorkEasy360?, Create Partner Account, Partner Benefits. Continue as Guest (2026-11-10) is kept under the sign-up button. `features/auth/PartnerLogin.tsx`.
- **Create Partner Account** (`/partner/apply`): the same frame and field style; the same fields, checks and server call as before; the partner type as three tiles.
- **Password pages** on `partner.` (forgotten password, reset, a partner's invitation) wear the same frame.
- **Remember me** now works for any sign-in that sends it (the Admin's and the Partner's): ticked, a 7-day session cookie; unticked, a browser-session cookie; the customer and product sign-ins send nothing and are unchanged (`auth_views._apply_remember_me`).
- A product's or a partner's branded sign-in on `partner.` (`?resume=`, `?brand=`) keeps the standard page.

| Check | Result |
|---|---|
| Backend: sign-in tests (`admin_panel/test_login.py`, `partner_center/test_architecture.py`) | 20 / 20 |
| Frontend: vitest, whole suite | 1,613 / 1,615; the 2 others timed out under the parallel production build and pass alone (`form-popup`, `partner-screens`: 31 / 31). The affected suites re-ran after the last change: 260 / 260 |
| Real browser, production build ([scripts/partner_login_check](../../scripts/partner_login_check/README.md)) | **26 / 26**: one screen at the image's 1672 x 941, 1440 / 1366 / 768 / 390 px, Remember me 7 days vs browser session, a real application sent, axe clean, no console error |
| Static | `tsc`, `eslint`, `ruff check` / `format --check` clean |

Open for the owner: confirm the product names in Our Product Ecosystem (taken from the image; they are words only, not linked to the catalogue).

## Addendum, 11 November 2026: the Partner panel in the Admin console's colours

At the owner's request (his Admin Dashboard screenshot): the partner workspace shell uses the console look the Super Admin has - `SidebarShell variant="admin"` (deep teal top bar and sidebar, the current page a green pill, the light sheet with its rounded corner), a "Partner Portal" badge, "Collapse menu", the account button with the name; the workspace label, the Partner Level badge and "Upgrade Plan" restyled for the dark bar. Billing Records no longer shows the wallet section to a partner without a Platform Wallet. Not committed. Checked: partner panel, sidebar, control plane and token suites 476 / 476; on the dev server the reseller and White Label dashboards are axe clean with no console error (1536 px and 390 px).
