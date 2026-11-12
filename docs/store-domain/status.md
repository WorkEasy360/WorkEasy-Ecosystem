# Digital Store at store.workeasy360.com (brief of 12 November 2026): status

Branch `feat/partner-admin-panel` (PR #3): commits `084c684`, `b3b78b4` (the frontend image with an empty `public/`) and `249d8d8` (`/sitemap.xml` only through the edge), pushed. **Deployed on 12 November 2026 as release `2026.11.12-249d8d8`**, by way of `2026.11.12-b3b78b4`, which applied the four migrations (backups `20261112T055646Z` and `20261112T060645Z`). The Store serves at **https://app.workeasy360.com/store** with the new design. **`store.workeasy360.com` is not live**: on 12 November 2026 the name does not resolve (NXDOMAIN at 1.1.1.1 and 8.8.8.8), so the Store host stays off until the owner adds the DNS record. Decisions: [ADR-065](../architecture/adrs/ADR-065-digital-store-own-host-checkout-wallet-split.md). Browser check: [scripts/store_domain_check](../../scripts/store_domain_check/README.md).

The two documents the brief names as authoritative, `08_Credits_Wallets_System.txt` and `07_Product_Web_Mobile_Flow.txt`, are not on this machine or in git (as on 9 November, ADR-060). This work follows the brief, and what those documents would decide is listed as open below.

## Results

Only what actually ran, 12 November 2026.

| Check | Result |
|---|---|
| Backend: the new Store suites (`tests/store_domain`: wallet payments, the Store host, public data) | **75 / 75** |
| Backend: the suites the change touches (portals, security, architecture, portal_store, store_checkout, user_admin_panel, admin_panel, phase9, extension, store, store_upgrade, guest_login, product_manager, partner_center, partner_admin_panel, isolation) | First run 87 failed / 1,716 passed: one cause, the pay view parsing an empty form body as JSON. Fixed; the 87 re-run **87 / 87**. Second run of architecture, portals, security, Store, guest login, partner centre, isolation, self-service and admin workflows: **1,064 / 1,065**, the one an architecture pin on the new `app/robots.txt` folder (reviewed and added; 11 / 11) |
| Backend: the whole suite (bare `pytest`) | **5,564 / 5,565** in 2 h 07 min. The one was the Phase 10 tripwire that lists the catalog's migrations, which did not know `0013_product_setup_requirements` yet (one presentation column with a database default). Added with its reason; that file re-run **28 / 28** |
| Frontend: vitest, whole suite | **1,689 / 1,689** (94 files). An earlier run had 2 load-timeouts in files this work does not touch; both passed alone |
| Real browser: production build, six hosts behind the edge stand-in, real backend, KYC required | **72 / 72**: 0 console errors, 0 CSP violations (besides the check's own deliberate cross-origin probe, which CSP blocks), 0 API 5xx, axe WCAG 2.1 A/AA clean on every page checked, one `h1` each, no sideways scroll at 390 and 320 px |
| Edge | `caddy validate` passes with the Store host off, on and unset; the Store site and its sitemap route exist only when on |
| Production, 12 November (`2026.11.12-249d8d8`) | Migrations `catalog.0013`, `payments.0009`, `wallets.0002`, `tenancy.0018` applied, 0 unapplied; api healthy; 0 error lines in api, web and worker logs. A read-only headless Chrome pass over `/store`, products, a product page, Recharge, Partners, Help and a policy: all 200, one `h1` each, 0 console errors, 0 CSP violations, 0 failed API calls, no sideways scroll at 390 px. `workeasy360.com/store` still 302s to `app.workeasy360.com/store`. The first release (`b3b78b4`) answered **400** on `app.../sitemap.xml` (a Next rewrite reached the API as `api:8000`); fixed in `249d8d8` (404 off the Store host; the Store host's sitemap is the edge's). No purchase was made in production |
| Static | `tsc` and `eslint` clean; `ruff check` and `ruff format --check` clean; import contracts 18 / 18; `mypy` (CI scope) clean; `bandit -ll` no issues; `makemigrations --check` no changes; production `next build` succeeds |

The browser run found and fixed, before passing: the Store host's root redirected away (a proxy rewrite Next treats as external - replaced by `app/page.tsx` serving the marketplace on that host); cards and the plan box led with a free plan's "₹0"; white-on-dark headings that a global heading colour turned dark; the account button's accessible name not containing its visible initials; and a plan that cannot join an organization's existing billing period being reported as a "catalog defect" (now explained in the buyer's words).

## What the owner sees

- **https://store.workeasy360.com/** - the marketplace of the second image: the WorkEasy360 logo and its Store badge, **Products, Recharge, Partners, Help**, the product search, Sign In / Get Started (or the bell, My Account and the account menu with the organization and Sign out); the hero with three slides (no autoplay); the departments as tabs; Popular Products as cards (logo, name, description, category, three features, the cheapest paid price per user or per account, Free plan / trial, Available / Coming Soon, View Details and Buy Now - Coming Soon never sells); Recharge & Buy Credits and Become a Partner beside them; the benefits, How It Works in five steps, Need Help? and the footer (Products, Support, Partners, Legal, the company). **No cart**: one product, one plan, one organization per order.
- **The checkout** of the first image: the Order - Payment - Confirmation band; 1 Account & Organization (Existing Account / Create New Account, then the organization - chosen by itself when there is one); 2 Billing Details (Business / Individual, the name, GST number, billing email); 3 Order Summary (product, plan, period, seats, included usage, key features, validity); 4 Payment (the server's plan amount, GST and total; **Use Platform Wallet** with the amount to use; Amount Payable Now; Pay via the configured provider; "Pay ₹18,320 & Subscribe"); and the trust strip.
- **The confirmation**: order and payment references, the invoice, what the wallet paid and what was paid online, the subscription and set-up status, Open Product, Open User Admin, and the way back to a product that sent the buyer.

## The brief, part by part

| Part | Requirement | Where it is, and its evidence |
|---|---|---|
| 1 | The Store's own domain | Optional sixth portal `store` (`contracts/values/portals.py`, `lib/portals.ts`, settings and production assertions); its own sign-in, sign-up, verification, reset and referral pages; emailed links open where asked; old `app.../store/...` addresses redirect with the query; canonical URLs, `robots.txt` per host, `sitemap.xml` from the API; Caddyfile site behind `W360_STORE_EDGE`; `make_env.py enable-store`. `tests/store_domain/test_store_host.py`, `src/store-portal.test.ts`; browser: root, legacy redirect, robots, sitemap |
| 1 | Approved return destinations only | `next` allowlists (`safe_next`, `safeStorePath`); a product's `return_to` shown only after `public/store/return-check` approves it against the product's registration. `test_store_public.py` |
| 1 | Payment return / webhook | The provider returns a Store payer to `/store/checkout/complete` (`PaymentAttempt.return_portal`); webhooks unchanged (API host, signed) |
| 2 | Four menus, header actions, mobile menu | `features/store/StoreShell.tsx`. `store.test.tsx`; browser: menus, mobile menu |
| 3 | Products, filters only where data exists, statuses, details | `StoreHome.tsx`, `StoreCatalog.tsx`, `ProductCard.tsx`, `ProductDetails.tsx` (overview, plans and pricing with renewal, seats, included usage and usage rates, app availability with official links only, setup requirements - `catalog.0013`). Backend purchasability unchanged (`marketplace_offers`) |
| 4 | One-product checkout, wallet + gateway split, idempotent, verified | `Checkout.tsx`, `payments.wallet_orders.member_pay_invoice`, `wallets.WalletHold` (`wallets.0002`). `test_wallet_payments.py` (21: full, split, failure releases, retry holds once, cancel and expiry release, late success, held money cannot be spent elsewhere, top-ups never from a wallet, permissions, isolation); browser: ₹10,000 held during the provider step, taken only once confirmed |
| 4 | Confirmation | `CheckoutComplete.tsx` - asks the server, never trusts the return |
| 5 | Three separate recharges | `Recharge.tsx`; `public/store/recharge` (only the rates and packs an Admin entered); credit packs and top-ups paid online only. Browser: ₹500 to the Consumption Wallet only |
| 6 | My Account into User Admin | The single-use pass (`auth/account-pass`, `auth/account-pass/enter`). `test_store_host.py` (8 pass tests: spent once, Store origin only, customer-portal pages only, never a hand-off code, voided by sign-out); browser: opens User Admin signed in |
| 7 | Partners on the shared application and KYC | `Partners.tsx`, `public/store/partner-programme` (Affiliate and Reseller, the Admin's KYC requirements, the stages); Apply / Status on the Partner portal; reserved partner codes |
| 8 | Help and policies | `Help.tsx` (topics, the Admin's published FAQs, a ticket from My Account, the support address); `Policy.tsx` - a policy is the Super Admin's published Help content (new kind `policy`, eight fixed keys); unpublished says so |
| 9 | UI | One design system (the platform's tokens), responsive, accessible (browser axe on every page) |
| 10 | From a product's site | `/store/buy?product=&plan=&interval=&ref=&return_to=` (codes only; referral recorded on the Store host) |
| 11-12 | Backend data, security | Store host API narrowed to the endpoints it uses (`STORE_UNDER_ORGS`); cross-origin writes refused; rate limits on every new public endpoint and the pass; holds and their outcomes audited |

## Owner steps (not mine to do)

1. **DNS**: in Cloudflare, add an `A` record `store` → `18.143.87.44` (the same server as the other portal hosts), **DNS only (grey cloud) at first**, so the edge can obtain the certificate directly; it can be switched to Proxied afterwards like the others.
2. ~~Deploy~~ - done 12 November 2026 (`2026.11.12-249d8d8`). Until step 3 the Store serves at `app.workeasy360.com/store`, with the new design.
3. **Switch the Store host on** once the name resolves: `sudo python3 make_env.py enable-store --env /opt/workeasy360/env/production.env`, then restart `api`, `worker-critical`, `web` and `edge`; the edge obtains the certificate. Check `https://store.workeasy360.com/` and that `app.workeasy360.com/store` redirects there.
4. **Cloudflare redirect**: point `workeasy360.com/store` at `store.workeasy360.com/store` (today it goes to `app.`, which will redirect once more).
5. **Live payments**: the Razorpay credentials (unchanged blocker); until then the checkout says online payment is not available and only a full Platform Wallet payment completes.
6. **Content**: publish the eight policies (Super Admin → Support → Help content → Store policy), add products' setup requirements and app links in the Product Manager, and the usage rates and credit packs in the Admin's wallets.

## Open

- Decided by the two missing documents: GST on wallet top-ups and credit packs (none is charged), credit expiry, minimum top-ups, refunds to wallet or to source, renewal paid from the wallet, and any rule for purchases inside the mobile apps (the Store sells on the web only; app links are links).
- An organization holds one subscription with one billing period: a product added later renews with it, so a plan sold only yearly cannot join a monthly subscription (the checkout explains this).
- A wallet top-up refund is refused at reversal (not up front) when the wallet's money is held for a payment in progress.
