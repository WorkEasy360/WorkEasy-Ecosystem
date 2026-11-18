# User Admin Panel (specification of 08 November 2026): status

Branch `feat/partner-admin-panel`. **Committed as `1989f60`, pushed (PR #3), and live as release `2026.11.12-1989f60` since 12 November 2026** (patch build from the `3a1289b` images; pre-deploy backup `20261111T235220Z`; `billing.0003_billing_profile` applied by the release's migrate task, 0 unapplied; API healthy, 0 error lines; `app.workeasy360.com/login` serves the User Admin Login). The migration adds seven text columns with database defaults, so **rollback is the release switch alone**: `W360_RELEASE=2026.11.11-3a1289b`, `current` to that release, `w360 up -d api worker-critical web`. The local development database was migrated on 12 November 2026 at the owner's request, after a backup. Decisions: [ADR-064](../architecture/adrs/ADR-064-user-admin-panel-seven-menus-wallets-and-guest-actions.md). Browser check: [scripts/user_admin_check](../../scripts/user_admin_check/README.md).

## Results

Only what actually ran, on the uncommitted working tree, 11-12 November 2026.

| Check | Result |
|---|---|
| Backend: the whole suite (bare `pytest`, real PostgreSQL) | **5,488 / 5,489** passed in 1 h 44 min. The one failure was an architecture guard: the new guest-session audit read the partner's landing preference (`default_experience`) in `guest.py`, which only presentation code may read. Fixed with a selector (`selectors.category_of`); the guard and its neighbours (`partner_experience`, `guest_login`, `architecture`) then passed 299 / 299 |
| Backend: User Admin Panel tests (`tests/user_admin_panel`) after the last change (invoice wording) | **61 / 61** |
| Frontend: vitest, whole suite | **1,677 / 1,678** (94 files). The one failure, `form-popup.test.tsx` (focus returns to the button after the popup closes), is in a file and component this work does not touch; it passed in the previous full run (1,676 / 1,676) and 3 / 3 times on its own: a timing flake on a machine that had just restarted |
| Real browser: production build, five hosts behind the edge, real backend, KYC required | **86 / 86**: 0 console errors, 0 CSP violations, 0 unexpected API errors, axe clean on every page checked, no sideways scroll from 1920 to 360 px |
| Static | `tsc` clean; `eslint` 0 errors (1 old warning in `PublicStoreProduct.tsx`, a file this work does not touch); `ruff check` and `ruff format --check` clean; import contracts 18 / 18; `mypy` (CI scope) clean; `bandit -ll` clean; `makemigrations --check` no changes; production `next build` succeeds |

The browser check signs in as the seeded owner, a Billing Manager, an Administrator, a Member and the Super Admin. It ran twice: the first run found two faults, both fixed (below), and the second passed 86 / 86.

## What the owner sees

- **Sign-in** at `app.workeasy360.com/login` is the User Admin Login of the first reference image: left panel (User Admin Panel pill, "Manage Your Business with WorkEasy360", the four features, the laptop, Our Product Ecosystem), right card (badge, Email Address, Password, Remember me, Forgot Password?, Login to User Admin Panel, New to WorkEasy360?, Create Organization Account, the WorkEasy360 Platform strip). Sign-up, Forgot Password and Reset Password use the same frame. A product sign-in (`?resume=`), a partner's branded sign-in (`?brand=`) and the central sign-in host keep the plain page.
- **The dashboard** follows the second reference image. It has the teal sidebar with seven menus, the Explore More Business Apps card, the organization switcher, search (Ctrl/⌘K), the bell, Help and the account menu. Below the welcome line and the This Month selector come pending actions and five summary cards, then My Products, Wallets & Credits, Subscriptions, Upcoming Renewals, Quick Actions and Recent Activity, and finally the Store banner. **Every figure comes from the organization's own data.** A section the member may not see is left out, never shown as zero.
- **Super Admin → Partners** (All, Affiliate, Reseller, White Label): a "View / Actions" column, fourth, holding **View** then **Login as Guest**, with the row's other actions beside them; no Earnings column. The partner's page has Back to Partners, Edit Partner and Login as Guest in its header. The guest banner reads "Guest Session – Viewing Partner: <name>", gives the partner type, and offers Return to Super Admin and End Guest Session.

## The specification, part by part

| Part | Requirement | Where it is, and its evidence |
|---|---|---|
| 1 | Exactly seven menus, sub-menus, icon rail, phone drawer | `launcher/orgNav.ts`, `launcher/Shell.tsx`, `SidebarShell` (`navFooter`). `orgNav.test.ts`, `Shell.test.tsx`; browser O2, O17-O19, M1 |
| 2 | Dashboard after the image, real data only | `console/UserDashboard.tsx`. `user-dashboard.test.tsx` (including "a member without billing rights never asks for wallets, orders or subscriptions"); browser O3-O16, O20 |
| 3 | My Products: All / Active / Free / Trial / Pending Setup / Expired, details, setup | `console/MyProductsScreen.tsx`. Tests in `console`; browser O21 |
| 4 | Billing & Subscriptions: purchase source, Orders & Payments, Billing Profile | `BillingScreen.tsx` (source from the new `collection_mode`), `CommerceScreens.tsx`, `BillingProfileScreen.tsx`; `PATCH billing-account` (profile fields only). `test_subscription_source.py`, `test_alerts_and_profile.py`; browser P pages |
| 5 | Wallets & Credits: three wallets apart, transactions, usage, export, low balance, top-up | `commercial/wallets/customer.py`, `payments/wallet_orders.member_top_up` / `member_credit_pack`, `console/wallets/*`. `test_wallets.py` (35); browser O8-O9, M2-M4, B1. A top-up is an order: only the paid order's fulfilment credits the wallet |
| 6 | Organization & Team, Help & Support, Settings | Existing screens moved under their menus, every old address still served (`orgNav.test.ts` walks them). Help opens a linked ticket from the bell (`?ticket=`) |
| 7 | Top bar: switcher only with more than one organization, notifications, profile menu | `launcher/OrgTopbar.tsx`; the bell from `customer_alerts.py` and the member's in-app preferences. Browser O22-O24 |
| 8 | Super Admin: Login as Guest beside View in all three types, in the partner header, no Earnings | `PartnerConsole.tsx`, `AdminPartners.tsx`. Browser S1-S3 at 1366 and 1280 px and on a phone |
| 9 | Secure, audited guest session with its banner and two exits | `ControlShell.tsx` (`GuestViewBanner`), `guest.py` (the start audit now records the category). `test_guest_rules.py`, `workspace.test.tsx`; browser S4-S7 (writes refused with 403, both exits, a new sign-in needed afterwards), X1-X2 (a partner user and a customer cannot start one) |
| All | Design system | `<html data-console="user">` turns on the consoles' shared rules (type scale, 36 px controls, card radius) and kit; [design-system.md](../admin-ui/design-system.md) updated |

## Fixed during verification

- **White Label guest banner** said "Partner type: White-label" (the workspace's label). It now uses the Admin's own words for the list the partner is filed under ("White Label"), falling back to the workspace label. Test added in `workspace.test.tsx`.
- **Subscriptions table on the dashboard** scrolled sideways without keyboard access (axe `scrollable-region-focusable`). The scroll area is now a labelled, focusable region.
- **"Invoice … is overdue" seconds after it was issued.** An invoice on immediate terms is due when it is issued, so it is now "due today" until its due day has passed. Test added.
- **Layout against the images**:
  - the spend figure broke as "₹10,000 / .00": whole amounts on the cards now drop the paise ("₹10,000"), and long figures are set smaller rather than broken;
  - card labels wrapped: narrow cards stack the icon above the figures;
  - the Subscriptions table was cut off at 1440 px and now gets the widest column;
  - "Subscription / s" broke in the sign-in's platform strip: each column is now at least as wide as its longest word;
  - activity lines now read "Payment received", "Invoice issued" and "Order fulfilled" instead of the module's record names.

## Open, by design or for later

- **No invoice PDF endpoint.** The invoice prints from the browser.
- **No customer refund endpoint.** "Request refund" opens a billing ticket.
- **Customers cannot close a ticket on the Workeasy360 desk.**
- **App-store purchases.** The console would show the store's own management link for an app-store subscription, but the backend does not emit that source yet.
- **AI allowance units.** The free AI allowance is counted in AI tokens (the usage rate's unit). Purchased credits do not expire in the model.
- **Who can pay.** An Administrator has no billing rights (`authz.SYSTEM_ROLES` keeps them separate on purpose). To pay or top up, the person needs the Billing Manager role; the UI says so. Opening a billing account still needs `commercial.subscription.manage` (the owner).
- **Guest return path.** The server keeps the guest view's return path without its query string. The list's filters come back from the browser tab (sessionStorage).
- **Dates follow UTC.** The bell's dates, including "due today", are server (UTC) days, so between midnight and 05:30 IST "today" is still the previous day.

## Owner steps

1. Merge PR #3 (`feat/partner-admin-panel` -> `main`) when ready, so `main` matches production.
2. Live customers see the new User Admin Login and console on their next visit; nothing needs configuring.
