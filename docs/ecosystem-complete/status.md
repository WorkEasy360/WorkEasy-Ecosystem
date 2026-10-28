# Complete Store + Partner Ecosystem: status

Date: 2026-10-28. Brief: the owner's *Workeasy360 - Complete Store + Partner Ecosystem* (source of truth `Workeasy360_Store_Working_Flow.pdf`, 10 pages, already traced in [store-flow/README.md](../store-flow/README.md)). Decisions: [ADR-051](../architecture/adrs/ADR-051-product-manager-central-catalog.md) (Product Manager), [ADR-052](../architecture/adrs/ADR-052-store-cart-eligibility-recurring-commission.md) (cart, eligibility, recurring commission), [ADR-053](../architecture/adrs/ADR-053-complete-store-partner-ecosystem.md) (this work). Browser check: [scripts/ecosystem_browser_check](../../scripts/ecosystem_browser_check/README.md).

## Verdict

**WORKEASY360 COMPLETE STORE + PARTNER ECOSYSTEM: FAIL** - because Phase 10 (production deployment and verification on the canonical domains) could not run: **production is down** (every portal answers Cloudflare 522, SSH to the instance times out; observed 2026-10-28 12:00-14:00 IST, before any deploy of this work). Everything up to the deployment **passes** on the platform's own code, a migration rehearsal, the full test suites, a production build and a real browser. Payment through a live gateway remains **BLOCKED** on the owner's provider credentials (the test provider proves the path).

## Owner decisions (2026-10-28)

1. **Margin**: discount off list - partner cost = list - discount (default per Reseller / White-label, override per partner, optionally per product, effective-dated); customer price = partner book else list; margin = customer price - cost; earned **instead of** commission, never both.
2. **Branded store**: a path on the customer portal (`app.workeasy360.com/store/<partner>`); central sign-in, checkout and My Products; sign-in on partner domains stays unsupported (D-035).
3. **Set-up fees**: optional per target, set by Super Admin, settled offline - approval needs the payment reference.
4. **Deploy** when all gates pass.

## What was built (by phase)

| Phase | Result |
|---|---|
| 0 Audit | Every affiliate / reseller / white-label menu traced to route, component, API, service; gaps listed (no `/r/` link, no product/plan/campaign links, random codes only, campaigns = labels, no marketing library, no visitors/registrations, conversions without product/amount, reseller sale without an Order, no margin, change-plan/cancel with no UI, Reseller workspace hidden for White-label, no branded store, no set-up fee, Support = mailto). |
| 1 Product Manager, plans, pricing, trial, eligibility | ADR-051/052 (earlier today, same tree) + **partner margins and set-up fees** on Currency & Pricing (`/admin/margins`, `/admin/partner-programme/fees`). |
| 2-3 Store, cart, checkout, payment, subscription, entitlement, My Products | ADR-052 + the **seller** concept on orders (`direct` / `partner` / `storefront`, `Order.partner_id`), and storefront checkout (`storefront=<partner>`). |
| 4 Affiliate | Own links `/r/<code>` for a product/plan/campaign, chosen codes (`AMAN360`), campaigns (create/archive, results), marketing materials (the Product Manager's approved listing, public images, copy, download, one-click link), unique visitors, registrations, conversions with product/plan/sale, sales attributed, top products, approved commission. |
| 5 Reseller | Pricing & Margins (list, cost, customer price, margin, margin %), **central Order for a customer** (quote -> order -> customer pays, or approved sale for partner-billed), margin on the ledger, orders with plan/earning/cost/payment status, Manage subscription (change plan, cancel at period end). |
| 6 White-label | Branded store over the central catalog (only granted products, partner prices), purchase links the buyer to the partner at fulfilment, dashboard card with the store address, terms/privacy URLs. |
| 7 Upgrades + switching | Additive workspaces (Reseller beside White-label), switcher order Affiliate, Reseller, White-label; landing falls back to the largest workspace; set-up fee shown and enforced at approval. |
| 8 Menus | 41 menu pages opened in a real browser, all working; decorative tabs removed (Communication Alerts/Logs, Plans Add-ons); Support sends a real request. |
| 9 Regression + security | See Gates. |
| 10 Deployment | **BLOCKED** - production unreachable (section 6). |

## Gates

| Gate | Result |
|---|---|
| Full backend suite (bare `pytest`, 4,912 tests) | **4,909 passed, 3 failed** (1 h 35 min). The 3 were allow-list pins (two privacy tests listing the referral row's fields, one listing the white-label status keys); the new fields (`product_code`, `registered_at`, `storefront`) identify nobody, so the lists were extended; their files then **112 passed**. After two final code fixes (partner order visibility, sell-form message) the affected suites ran again: **241 passed**. The full suite was not re-run end to end after those last edits. |
| New backend tests `backend/tests/ecosystem/` | 6 files: margins (domain, versioning, ledger instead of commission, partner price), partner orders (quote, pay-first, approved sale, not-granted, other partner's customer, browser price), storefront (granted only, brand/capability/status gates, central order + link, other partner refused), referral links (chosen codes, product destination, closed product, campaigns, visitors/registrations, public capture, clicks never earn), workspaces/fees/support - all pass |
| ruff, ruff format | clean (798 files) |
| mypy (CI definition) | no issues (33 files) |
| lint-imports | 18 kept, 0 broken |
| bandit (medium+) | 0 |
| pip-audit `--strict` | **FAIL - 1 finding not introduced here**: `dnspython 2.7.0` GHSA-45c3-73f7-pv4m, fixed in 2.9.0, which this machine's package index does not offer yet (latest 2.8.0). Used by custom-domain DNS verification. |
| `manage.py check --deploy --fail-level ERROR` (CI's command) | pass (development-settings warnings only); production settings: not run (host unreachable) |
| `makemigrations --check` | no changes |
| Migration rehearsal (`scripts/ecosystem_browser_check/prepare.py`) | **PASS**: HEAD code migrates and writes history (partners, members, 13 grants, a referral code + 2 visits, a provisioned customer, a sale, a ledger row, a rule); the working tree applies `catalog.0005`, `payments.0003/0004`, `commissions.0004/0005`, `partners.0010`; **15 tables fingerprinted identical**, new columns at their defaults, ledger still append-only (UPDATE refused), reverse to the previous migrations and forward again with rows intact |
| Frontend: clean `npm ci`, eslint, `tsc --noEmit` | clean |
| vitest | **966 passed** (52 files) |
| `next build` (scratch copy) | clean; new routes `/r/[code]`, `/store/[partner]` |
| npm audit (production / all) | 0 / 0 |
| Real browser (production build, real backend, headless Chrome) | **PASS: 11/11 journeys, 66 pages audited: axe WCAG 2.1 A/AA 0, CSP 0, console errors 0, visible focus everywhere, one h1 each; reflow 84/84 at 1920, 1440, 1366, 1024, 768, 390 px** |
| Manual screen reader | NOT EXECUTED |

Defects the real browser found and that were fixed: `/r/<code>` posted before the CSRF cookie existed (403 on a first visit) - the page now reads the session first (+3 tests); a partner could not see its own storefront sale (orders were matched by the customer link, which a storefront buyer gets at fulfilment) - orders the partner sold are now included; the reseller's "Order created" message vanished with the form - kept; the branded store's Buy could be clicked before the buyer's organizations loaded and did nothing - Buy waits for them.

## Acceptance matrix

| Capability | Admin | Affiliate | Reseller | White-label | Customer |
|---|---|---|---|---|---|
| Product Catalog | PASS | PASS | PASS | PASS | PASS |
| Plans | PASS | PASS | PASS | PASS | PASS |
| Pricing | PASS | NOT APPLICABLE | PASS | PASS | PASS |
| Trial | PASS | NOT APPLICABLE | PASS | PASS | PASS |
| Store | PASS | PASS | NOT APPLICABLE | PASS | PASS |
| Cart | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | PASS |
| Checkout | NOT APPLICABLE | NOT APPLICABLE | PASS | PASS | PASS |
| Orders | PASS | NOT APPLICABLE | PASS | PASS | PASS |
| Payments | PASS (test provider); live gateway BLOCKED | NOT APPLICABLE | PASS | PASS | PASS (test provider); live BLOCKED |
| Subscription | PASS | NOT APPLICABLE | PASS | PASS | PASS |
| Entitlement | PASS | NOT APPLICABLE | PASS | PASS | PASS |
| My Products | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | PASS |
| SSO | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | PASS (launch target issued; product sessions are the products' own) |
| Referral Links | PASS | PASS | NOT APPLICABLE | NOT APPLICABLE | PASS (followed) |
| Referral Codes | PASS | PASS | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE |
| Campaigns | NOT APPLICABLE | PASS | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE |
| Commission | PASS | PASS | PASS | PASS | NOT APPLICABLE |
| Payout | PASS | PASS | PASS | PASS | NOT APPLICABLE |
| Customer Management | PASS | NOT APPLICABLE | PASS | PASS | NOT APPLICABLE |
| Reseller Margin | PASS | NOT APPLICABLE | PASS | PASS | NOT APPLICABLE |
| Branding | PASS | NOT APPLICABLE | NOT APPLICABLE | PASS | PASS |
| Domain | PASS | NOT APPLICABLE | NOT APPLICABLE | PASS (lifecycle; branded landing only) | NOT APPLICABLE |
| Upgrade | PASS | PASS | PASS | NOT APPLICABLE | NOT APPLICABLE |
| Workspace Switching | NOT APPLICABLE | PASS | PASS | PASS | NOT APPLICABLE |
| Reports | PASS | PASS | PASS | PASS | NOT APPLICABLE |
| Support | PASS | PASS | PASS | PASS | PASS |

Every PASS above is local (scratch database, production build, real browser). **Production: BLOCKED** for all of them (section 6).

## Product acceptance matrix

| Product | Catalog | Plan | Price | Trial | Store | Reseller | Affiliate | White-label | Entitlement | SSO |
|---|---|---|---|---|---|---|---|---|---|---|
| Easy CRM | PASS | PASS | PASS | PASS | PASS | NOT APPLICABLE (not granted - correctly hidden) | PASS | PASS | PASS | PASS |
| EasyBook | PASS | PASS | PASS | PASS (offered) | PASS | PASS | PASS (linkable) | PASS | PASS | PASS (launch needs a seat; per-seat product) |
| Easy Project | PASS | PASS | PASS | PASS (offered) | PASS | NOT APPLICABLE (not granted) | PASS (linkable) | NOT APPLICABLE (not granted) | PASS | PASS (target registered) |
| Easy Help Desk | PASS | PASS | PASS | PASS (offered) | PASS | PASS (granted, sellable) | PASS (linkable) | NOT APPLICABLE (not granted) | NOT EXERCISED in a purchase | PASS (target registered) |

Plans and prices exist only in the scratch database (TEST Basic/Advanced, INR): production has the four products but staff must create their plans and prices in the Product Manager before anything can be sold there.

## Menu audit (real browser, partner's own login)

Affiliate (`aman@eco.test`), Reseller (`res@eco.test`), White-label (`wl@eco.test`); each row opened, audited (axe, h1, CSP, console) and found working. API column: `pp = /api/v1/partner/<id>`.

| Workspace | Menu | Route | Frontend | Backend API | Permission | Result |
|---|---|---|---|---|---|---|
| Affiliate | Dashboard | /partner/affiliate/dashboard | AffiliateDashboard | pp/affiliate/overview | dashboard.view + referral.view | PASS |
| Affiliate | Referral Links | .../referral-links | ReferralLinks | pp/affiliate/overview, POST pp/referrals, pp/referral-codes/<id>/disable | referral.create | PASS |
| Affiliate | Referral Codes | .../referral-codes | ReferralCodes | POST pp/referrals {code} | referral.create | PASS |
| Affiliate | Campaigns | .../campaigns | Campaigns | pp/campaigns, pp/campaigns/<id>/status | referral.create | PASS |
| Affiliate | Marketing Materials | .../marketing-materials | MarketingMaterials | pp/affiliate/overview (products), public/products/<slug>/media/<id> | referral.view | PASS |
| Affiliate | Leads & Referrals | .../leads | Leads | pp/referrals | referral.view | PASS |
| Affiliate | Conversions | .../conversions | AffiliateConversions | pp/affiliates/conversions | referral.view | PASS |
| Affiliate | Commissions | .../payments/commissions | PartnerCommissions | pp/commissions | commission.view | PASS |
| Affiliate | Payouts | .../payouts | PartnerPayouts | pp/payouts | payout.view | PASS |
| Affiliate | Reports & Analytics | .../reports | Reports | pp/reports | dashboard.view | PASS |
| Affiliate | Profile & Settings | .../account | Account | pp/profile | profile.view | PASS |
| Affiliate | Support | .../support-manager | Support | GET/POST pp/support | dashboard.view | PASS |
| Reseller | Dashboard | /partner/reseller/dashboard | PartnerDashboard | pp/dashboard | dashboard.view | PASS |
| Reseller | Customers | .../users | PartnerCustomers | pp/customers, pp/customers/<org>/orders[/quote], .../subscription, change-plan, cancel | customer.view, subscription.sell/manage | PASS |
| Reseller | Products & Plans | .../partner-products | PartnerProducts | pp/products | product.view | PASS |
| Reseller | Pricing & Margins | .../plans | Plans | pp/plans | pricing.view | PASS |
| Reseller | Sales & Orders | .../payments | Payments | pp/orders | customer.view | PASS |
| Reseller | Subscriptions | .../users/subscriptions | Subscriptions | pp/customers | customer.view | PASS |
| Reseller | Commissions | .../payments/commissions | PartnerCommissions | pp/commissions | commission.view | PASS |
| Reseller | Payouts | .../payouts | PartnerPayouts | pp/payouts | payout.view | PASS |
| Reseller | Sub-Resellers | .../agency | Agency | pp/sub-partners | subpartner.view | PASS |
| Reseller | Team & Permissions | .../team | Team | pp/members, pp/roles | member.view | PASS |
| Reseller | Reports & Analytics | .../reports | Reports | pp/reports | dashboard.view | PASS |
| Reseller | Support | .../support-manager | Support | GET/POST pp/support | dashboard.view | PASS |
| Reseller | Settings | .../account | Account | pp/profile | profile.view | PASS |
| Reseller | (tab) Balance | .../payments/balance | Balance | pp/balance | commission.view | PASS |
| White-label | Dashboard | /partner/white-label/dashboard | WhiteLabelDashboard | pp/dashboard, pp/white-label/status | dashboard.view | PASS |
| White-label | Brand Settings | .../brand-settings | BrandSettings | pp/branding | branding.view | PASS |
| White-label | Custom Domain | .../brand-settings/url-manager | UrlManager | pp/domains | domain.view | PASS |
| White-label | Website Branding | .../brand-settings/white-label-setup | PortalWhiteLabelScreen | pp/branding, pp/domains | branding.view | PASS |
| White-label | Products & Plans | .../partner-products | PartnerProducts | pp/products | product.view | PASS |
| White-label | Pricing & Plans | .../plans | Plans | pp/plans | pricing.view | PASS |
| White-label | Customers | .../users | PartnerCustomers | pp/customers (+ orders) | customer.view | PASS |
| White-label | Subscriptions | .../users/subscriptions | Subscriptions | pp/customers | customer.view | PASS |
| White-label | Orders & Billing | .../payments | Payments | pp/orders | customer.view | PASS |
| White-label | Commissions | .../payments/commissions | PartnerCommissions | pp/commissions | commission.view | PASS |
| White-label | Payouts | .../payouts | PartnerPayouts | pp/payouts | payout.view | PASS |
| White-label | Sub-Resellers | .../agency | Agency | pp/sub-partners | subpartner.view | NOT APPLICABLE (not granted to this partner; hidden) |
| White-label | Referrals | .../affiliates | Affiliates | pp/affiliates | referral.view | NOT APPLICABLE (shown only with the Affiliate capability; the upgraded partner's affiliate workspace passed) |
| White-label | Team & Permissions | .../team | Team | pp/members | member.view | PASS |
| White-label | Reports & Analytics | .../reports | Reports | pp/reports | dashboard.view | PASS |
| White-label | Support | .../support-manager | Support | GET/POST pp/support | dashboard.view | PASS |
| White-label | Settings | .../account | Account | pp/profile | profile.view | PASS |
| Super Admin | Product Manager | /admin/products | ProductManager | admin/products* | staff_catalog.* | PASS |
| Super Admin | Plans & Add-ons | /admin/plans | PlansScreen | admin/catalog/plans* | staff_catalog.* | PASS |
| Super Admin | Currency & Pricing | /admin/pricing | PricingScreen + PartnerProgramme | admin/catalog/*, admin/margins, admin/partner-programme/fees | staff_pricing / staff_commission / staff_partner | PASS |
| Super Admin | Orders | /admin/orders | StaffOrdersScreen | admin/orders | staff_payment.view | PASS |
| Super Admin | Payments | /admin/payments | PaymentsScreen | admin/payments/* | staff_payment.* | PASS |

Removed as decorative (no capability behind them): the Communication **Alerts** and **Logs** tabs and the Plans **Add-ons** tab (the addresses still answer and say why).

## Journeys (real browser, `scripts/ecosystem_browser_check`)

| Test | Result |
|---|---|
| A Super Admin | four products; INR plans and prices; margins (Reseller 20%, White-label 25%) and a Reseller set-up fee (INR 25,000) on Currency & Pricing |
| B Customer | `/store` -> Explore -> Easy CRM 7-day trial -> My Products TRIAL -> seat -> launch issued (to `crm.workeasy360.com`) -> EasyBook checkout -> test payment -> paid line |
| C Cart | Easy CRM Basic + EasyBook Advanced + Easy Project Basic: **one order (3 lines), one payment, three subscription lines**, all three in My Products |
| D Affiliate | campaign "CRM October Campaign"; link `AMAN360` for Easy CRM; a clean browser follows `/r/AMAN360` -> product page (referral + visitor cookies) -> signs up in another tab -> verifies -> buys; **no commission before payment; then 1 accrual of INR 99.90 (10%)**; dashboard: 1 click, 1 visitor, 1 registration, 1 conversion |
| E Reseller | sells only EasyBook and Easy Help Desk; creates a customer (owner activates by emailed link, sets own password); central order (quote INR 799.00) -> nothing granted -> owner pays -> EasyBook line; **margin INR 159.80, cost INR 639.20** on the reseller's order list |
| F White-label | `/store/acme-cloud` lists only Easy CRM and EasyBook; buyer pays through central checkout (`storefront`); buyer becomes Acme's customer; **margin INR 249.75 (25%)** |
| G Upgrade | Affiliate -> Reseller: fee shown (INR 25,000), approval refused without the payment reference, approved with it; switcher **Affiliate, Reseller**; -> White-label: switcher **Affiliate, Reseller, White-label**; same partner id and login; referral code `GROW360` kept |
| H Menus | 41 menu pages, all PASS |
| I Wrong access | affiliate -> reseller API 403; reseller -> brand / domain 403; partner -> other partner 404; partner -> admin margins / fees 403; not-granted order 404; a quote with a browser price, currency, partner and channel came back at the server's INR 599.00, `direct` |
| S Support | request sent through the platform |
| R Responsive | 84 viewport checks, no sideways scroll |

## Final questions

1. Can Affiliate generate its own Product/Campaign referral link? **YES**
2. Is referral attribution preserved through signup and checkout? **YES** (across a new tab, by the HttpOnly cookie)
3. Does Affiliate commission occur only after verified purchase/payment? **YES**
4. Can Reseller see and sell ONLY Admin-granted products? **YES**
5. Does Reseller sale create customer Subscription + Entitlement? **YES** (after the customer pays, or at once for a partner-billed account)
6. Can White-label use the same central catalog/order/subscription/entitlement system under its approved branding/domain? **YES** for the branding (branded store path); the custom domain serves the branded landing only (sign-in on partner domains unsupported by decision, D-035)
7. Can Affiliate upgrade to Reseller using the SAME account? **YES**
8. After upgrade, are both Affiliate and Reseller workspaces available? **YES**
9. After White-label upgrade, can the same account access all granted Affiliate, Reseller and White-label workspaces? **YES**
10. Are old referrals, orders, commissions, payouts and other historical records preserved across upgrades? **YES** (upgrades are additive; the rehearsal proves the migrations keep every row)
11. Does workspace switching grant zero new permissions? **YES**
12. Does multi-product checkout create separate subscriptions and entitlements per product? **YES** (one subscription with one line and one entitlement per product - ADR-052)
13. Do all four current products use the same generic Store architecture? **YES**
14. Is every visible Partner/Admin Store menu item functional? **YES**
15. Does the complete customer journey work (Store -> Plan -> Cart -> Checkout -> Payment -> Subscription -> Entitlement -> My Products -> SSO)? **YES** locally with the test payment provider; live payment BLOCKED on credentials
16. Is the complete system deployed and verified on the canonical production domains? **NO** - production is down (section 6)

## 6. Production

Observed 2026-10-28 from 12:00 to 14:00 IST: `admin.`, `partner.`, `app.`, `auth.`, `api.workeasy360.com` answer **Cloudflare 522** (origin unreachable); SSH to `18.143.87.44` times out; `ops.workeasy360.com` (another host) answers. Nothing from this work was deployed. Needed from the owner: check EC2 `i-048217cdd868c2867` (ap-southeast-1) - status checks, reboot or stop/start (a stop/start may change the public IP unless an Elastic IP is attached), security group (80/443 and SSH from the operator's IP). Then the release can be built and deployed (`deploy/single-host/README.md`): record the running release, `w360-backup`, unpack `deploy/` with `git -c core.autocrlf=false archive`, load the images, set `W360_RELEASE`, `w360 up -d` (migrate runs first), health checks, smoke, browser, logs. Rollback target: the release running before.

After deploying, staff must still create plans and prices for the four products, enable a live payment provider, and set the margins and fees they want - none is created by a migration.

## Remaining findings

- **P0** Production down (Cloudflare 522, SSH timeout) - owner/infrastructure; blocks Phase 10.
- **P1** Live payment gateway not configured (Stripe/Razorpay adapters exist; credentials are the owner's). Paid checkout cannot complete in production until then.
- **P1** Production catalog has no plans or prices for the four products (staff configuration).
- **P2** `dnspython 2.7.0` advisory GHSA-45c3-73f7-pv4m; fix 2.9.0 not yet obtainable here - upgrade when available.
- **P2** Sign-in on a partner's custom domain stays unsupported (D-035, by decision); the branded store lives at `app.workeasy360.com/store/<partner>`.
- **P3** No separate favicon upload (branded pages use the logo); no separate product icon upload (icon token + logo image).
- **P3** A storefront order that owes nothing (a trial) links the buyer before the line is added outside one transaction with it; a failure in between leaves a link without a purchase.
- **P3** Captured referrals never converted are pruned after 180 days (retention), so very old click counts shrink.
- **P3** Full backend suite not re-run end to end after the last two code fixes (their suites were: 241 passed).
