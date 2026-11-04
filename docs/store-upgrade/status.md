# Full-stack Store upgrade: audit, changes and status

Date: 2026-11-01. Brief: the owner's *Full-stack Store upgrade* (one central catalog, one pricing engine, one commercial rule engine; Super Admin, customer, affiliate, reseller and white-label). Decision record: [ADR-056](../architecture/adrs/ADR-056-full-stack-store-one-commercial-engine.md). Builds on ADR-051 to ADR-055. Not committed, not deployed.

## 1. Existing architecture (audit)

| Area | What existed before this work | Source of truth |
|---|---|---|
| Catalog | One `catalog.Product` per product with listing fields, channel flags (customer store, affiliate, reseller, white-label), media (logo, banner, screenshots), trial config; `Plan` -> immutable `PlanVersion` (seats, trial, features); bundles | `commercial/catalog` |
| Prices | `PriceBook` (channel x region x currency, partner books) and `PlanPrice` rows immutable once published (DB trigger), effective-dated, superseded never edited | `commercial/pricing` |
| Price resolution | `resolve_price` - contract, partner book, regional, default, else raise; the result is snapshotted on the subscription line | `pricing/resolution.py` |
| Orders | Single plan, bundle, activation and multi-product cart; server re-quotes on placement; browser sends identifiers only | `payments/orders.py` |
| Payment | Webhook-verified (HMAC, dedupe); fulfilment only after a verified payment; Stripe/Razorpay adapters + test provider | `payments/webhooks.py` |
| Subscriptions / entitlements | One line + one entitlement per product; launch re-checks entitlement | `subscriptions`, `provisioning`, `launcher` |
| Commission | Versioned `CommissionRule` (rate, product, recurring), accrual only from a paid invoice line, immutable ledger, refunds reversed | `commercial/commissions` |
| Margin | Versioned `MarginRule` (discount off list per Reseller/White-label, partner override, product) earned instead of commission | `commissions/margins.py` |
| Partner eligibility | Product grants (sell), promotion grants (promote), Store Console requests | `commercial/partners` |
| Stores | Shared `StoreBrowser` page over one browse engine for Super Admin, customer, affiliate, reseller and white-label; branded store `/store/<partner>` | `controlplane/store.py`, `portal_store.py` |

## 2. Problems found

| # | Problem | Fixed by |
|---|---|---|
| P-1 | No promotional discount anywhere (brief section 3) | discount on the price row, one `apply_discount` |
| P-2 | No product set-up fee (only partner programme fees) | set-up fee on the price row, charged once at checkout |
| P-3 | Price changes possible only by publishing a whole new plan version in the plan builder; no "effective until", no way to withdraw a scheduled change | Plans & Pricing tab; `valid_to`; `cancel_scheduled_price` |
| P-4 | Partner prices settable only by staff; no admin-bounded partner pricing (W-5 gap in store-flow) | `SellingPriceRange` + `set_own_price` |
| P-5 | Commission: no fixed amount, no plan-specific rule, no duration | `CommissionRule.plan_code / fixed_amount_minor / recurring_months` |
| P-6 | **Duplicate pricing logic**: the partner "Pricing & Margins" read computed cost and margin itself, and read raw amounts (would ignore any discount) | rebuilt on `channel_pricing` |
| P-7 | Duplicate "which programme does this partner sell under" rule inside the accrual | `partners.selectors.selling_experience_at`, used by accrual, engine, ranges |
| P-8 | No product-wise view of what each Store level gets; commission rules API-only | Store -> Commercial rules |
| P-9 | No Store dashboard | Store -> Overview |
| P-10 | No subcategory, tags, features/benefits/use-case lists, featured/popular/new flags, recommended plan | merchandising fields |
| P-11 | Customer cards linked to the public marketing page; no in-store product page or plan comparison | `/o/<org>/store/<product>` + shared `PlanComparison` |
| P-12 | `commissions_rule.recurring` (ADR-052) was not frozen by the rule trigger | migration `commissions.0007` |
| P-13 | `tenancy.0001` granted on `django_session` without depending on `sessions` (latent ordering bug) | declared dependency |
| P-14 | Test harness write guard crashed for any model with a field named `model` (`PlanPrice.model`) | `tests/write_ownership.py` |
| P-15 | The product page's Plans tab was a link-only panel (dead code once the board exists) | removed |

No hardcoded prices or product lists were found in frontend source (searched for currency literals and product codes).

## 3. Architecture changes

```
Super Admin (Store: Overview, Browse, Commercial rules; Product -> Plans & Pricing)
        |
Central catalog (catalog)  ->  Central prices (pricing: PlanPrice + discount + set-up fee, resolve_price)
        |
Central commercial rules (commissions.channel_pricing: margins, commissions, price ranges)
        |
Customer Store  |  Affiliate Store  |  Reseller Store  |  White-label Store   (one StoreBrowser, one browse engine)
```

Every channel figure comes from `channel_pricing`, which only composes the functions checkout and the ledger already use. See ADR-056 decisions 1-10.

## 4. Backend changes

- `pricing`: `PlanPrice.discount_bp`, `setup_fee_minor`; `domain.apply_discount`; `resolve_price` returns list total, discount, set-up fee (snapshotted); `publish_price(valid_to, discount_bp, setup_fee_minor)` with previous terms in the audit; `cancel_scheduled_price`; `publish_own_partner_price`; `price_schedule`; list reads carry net/list amounts.
- `payments.orders`: set-up fee in quotes, cart lines and a separate unlinked invoice line; `paid_orders_by_channel`.
- `catalog`: merchandising fields and validators; `Product.recommended_plan` (one per product by construction, exposed as `recommended` per plan); `store_counts`, `plan_exists`.
- `commissions`: rule extensions (model, domain, rules writer, selectors, accrual); `channel_pricing` engine; `referral_sales`.
- `partners`: `SellingPriceRange`, `price_ranges` service, `selling_experience_at`; `promotion_requests.pending_request_count`.
- `controlplane`: `store_admin` (dashboard, commercial rules, product commission, plan board, duplicate plan, quote preview); `portal_store.customer_product`; partner `plans` on the engine; store cards carry badges, collections and discount.

## 5. Frontend changes

- Super Admin: Store area tabs (Overview, Browse, Commercial rules); `StoreDashboard`, `CommercialRules` (channel switches, commission dialog with % / fixed / recurring / months / effective date, margin dialogs, price-range dialogs, per-plan engine breakdown); Product -> **Plans & Pricing** (`PlansPricingTab`: per-book prices current and scheduled, Edit price with monthly/yearly, discount, set-up fee, effective from/until; End price; Cancel change; Set recommended; Enable / Disable / Archive; Duplicate; Publish draft version); Listing tab -> **Store merchandising** form.
- Customer: product page `/o/<org>/store/<product>` (`StoreProductScreen`: content, screenshots, key features, benefits, use cases, plan comparison with per-plan Start trial / Buy / Add to cart / Your plan, related products); cards link to it.
- Shared: `PlanComparison`; `StoreBrowser` gains Collections, collection badges and the struck-through list price.
- Partner: Pricing & Margins shows the allowed range, expected earning, and **Set my price** (white-label and reseller, when allowed).
- API clients: `lib/api/store.ts`; types extended in `console.ts`, `controlPlane.ts`, `partnerPanel.ts`.

## 6. Flows

**Admin pricing workflow.** Products -> a product -> Plans & Pricing -> Edit price (monthly and yearly in one form; discount %; set-up fee; effective from/until) -> a new immutable price row; the old one is closed at the start date; a scheduled change can be withdrawn until it starts. Store -> Commercial rules -> per product: who may sell, affiliate commission, reseller and white-label margins, partner price ranges.

**Customer.** Store -> product page -> compare plans -> Start trial / Buy / Add to cart -> checkout (server re-prices: discount, set-up fee) -> payment (verified) -> subscription line + entitlement -> My Products -> Open (launch re-checks entitlement).

**Affiliate.** Store (Approved / Explore via Store Console) -> request -> admin approves -> links and campaigns -> commission accrues only after a verified payment, under the product's (or plan's) rule: rate or fixed, first payment or recurring for N months.

**Reseller.** Granted products only; Pricing & Margins shows list, cost, customer price, margin, expected earning; may set its own price inside a Reseller range when Super Admin allows; orders priced by the server from its book.

**White-label.** Same as reseller under the White-label programme and range; branded store `/store/<partner>` sells through central checkout; revenue split = margin (partner) and cost (platform), server-side.

## 7. Database migrations

`catalog.0007_store_merchandising`, `pricing.0004_price_discount_setup_fee`, `commissions.0007_rule_plan_fixed_duration`, `partners.0014_selling_price_range`; `tenancy.0001` dependency declaration. Additive, defaults on every new NOT NULL column, reversible. **Dev database not migrated** (needs the owner's approval).

## 8. APIs changed / added

| Method | Path | Purpose |
|---|---|---|
| GET | `admin/store/dashboard?days=` | Store overview |
| GET | `admin/store/commercial-rules?currency=` | product-wise rules with every channel's engine figures |
| POST | `admin/store/commission` | product / plan commission (rate or fixed, recurring, months, effective from) |
| GET | `admin/store/quote` | engine preview for one plan, cycle, channel (and partner) |
| GET | `admin/store/products/<id>/plans` | Plans & Pricing board |
| POST | `admin/store/plans/<id>/duplicate` | draft copy with terms and prices |
| POST | `admin/store/prices/<id>/close`, `.../cancel` | end a price; withdraw a scheduled change |
| GET, POST | `admin/store/price-ranges`; DELETE `admin/store/price-ranges/<id>` | partner price ranges |
| POST | `partner/<id>/own-price` | partner's own price inside its range |
| GET | `orgs/<slug>/store/<product>` | customer product page |
| changed | `admin/catalog/prices` | + `discount_bp`, `setup_fee_minor`, `valid_from`, `valid_to` |
| changed | `admin/catalog/plans/<id>` (PATCH) | + `recommended` |
| changed | `admin/commission/rules` (POST) | + `plan_code`, `fixed_amount_minor`, `fixed_currency`, `recurring_months` |
| changed | `orgs/<slug>/store`, `partner/<id>/{affiliate,reseller}/marketplace`, `admin/products/store` | + `collection` filter, badges, tags, discount on the price |
| changed | `orgs/<slug>/orders/quote` and order placement | + set-up fee |
| changed | `partner/<id>/plans` | engine figures, range, `can_set_price` |

## 9. Tests added

| Suite | Tests | Covers |
|---|---|---|
| `backend/tests/store_upgrade/test_pricing_engine.py` | 15 | discount half-up and bounds; resolve + snapshot; card price equals checkout price; future change keeps history; cancel scheduled; a started price cannot be withdrawn; effective-until; bad terms; no backdating; the DB trigger freezes discount and fee; audit carries previous terms |
| `.../test_checkout_and_commission.py` | 11 | checkout = discounted price + first set-up fee; the browser cannot lower a price; set-up fee is an unlinked, non-commissionable invoice line; a cart charges each fee once; an existing subscription keeps its price after a rise; plan > product > default rule; fixed amount per payment and currency; recurring duration; rule validation; a plan-specific fixed commission accrues on a sale |
| `.../test_partner_pricing_and_engine.py` | 11 | own price inside the range is charged at checkout and audited; outside the range refused (4 amounts); no range / partner pricing off; the range of the wrong programme; plan not granted; staff-only, validated, audited ranges; an affiliate cannot price; engine per channel (direct, affiliate, reseller margin, white-label commission fallback, tax treatment); engine reflects own price and range, bulk equals exact; the partner screen reads the engine |
| `.../test_store_admin_api.py` | 11 | grants on every admin store endpoint; dashboard; commercial rules from one price; product commission from the screen; engine preview; plan board edit, schedule, cancel, duplicate; one recommended plan per product; price ranges over HTTP; customer product page, comparison, 404; a price change reaches card, page and checkout; collections and tag search |
| `frontend/src/features/storefront/store-upgrade.test.tsx` | 12 | exact percent parsing; plan comparison (discount, set-up fee, seats, unlimited, included, recommended, cycles); product page (content, axe, per-plan trial/buy/cart, held product, non-owner); partner Set my price (range, refusal, request body); Commercial rules (engine figures, axe, fixed recurring commission, price range); Store overview |
| Updated | 6 | product-manager tabs and a new Plans & Pricing test; store-screen card link; store card price shape; model-set pins (3) and migration pins (2) |

## 10. Test results (2026-11-01, local)

| Gate | Result |
|---|---|
| Full backend suite (bare `pytest`, 5,037 tests) | **5,029 passed, 8 failed** (1 h 44 min). The 8: five pins (model sets, migration lists) extended for the new table and migrations; the audit-coverage guard (`publish_price` is now a wrapper; its core was renamed `_publish_price`, the convention the guard accepts); the catalog guard against booleans on plan tables (`Plan.recommended` replaced by `Product.recommended_plan`); the frontend money-arithmetic guard (percent parsing moved to an exact `percentToBp`). Re-run of those suites plus every suite touched by the fixes: **387 passed**. The full suite was not re-run end to end after these fixes. |
| New backend tests `tests/store_upgrade` | 48 passed |
| Targeted commercial suites before the full run | 505 passed |
| ruff, ruff format, mypy (CI), lint-imports (18 contracts), `manage.py check`, `makemigrations --check` | clean |
| Migration rehearsal (`scripts/store_upgrade_check/prepare.py`) | **PASS, 29 checks**: HEAD code builds history; four migrations applied; 9 tables fingerprinted identical; new columns at defaults; triggers freeze discount and `recurring`; reversed and re-applied with rows intact |
| Frontend eslint, tsc | clean |
| vitest | **1,020 passed** (59 files) |
| `next build` (scratch copy, clean `npm ci`), dynamic-rendering assertion, `npm audit --omit=dev` | clean; 0 vulnerabilities |
| Real browser (`scripts/store_upgrade_check/browser_check.js`: production build, real backend, headless Chrome) | **PASS: 6/6 journeys; 9 pages audited: axe WCAG 2.1 A/AA 0, CSP 0, console errors 0, visible focus, one h1 each; 30/30 reflow checks 1920-390 px** |
| Manual screen reader | NOT EXECUTED |

Browser journeys: A Super Admin overview and commercial rules, Easy CRM commission 30% recurring for 12 months (an affiliate earns INR 299.70 on 999), white-label range INR 500-900; B Plans & Pricing: Basic INR 999 less 10% = 899.10 with an INR 250 set-up fee; C the customer card shows 899.10 struck from 999, the product page compares plans, checkout charges INR 1,149.10, test payment, plan active, "Your plan" shown; D white-label sets INR 699 (cost 674.33, margin 24.67), 100 refused by the form, 1,500 refused by the server (400); E customer and partner refused on admin store APIs (403), an affiliate cannot set a price; R responsive. The browser check found one defect, fixed: the product page scrolled sideways at 390 px (the comparison table escaped its scroll frame).

## 11. Acceptance (brief section 32)

| Criterion | Result |
|---|---|
| Super Admin adds a product from the UI | PASS (existing wizard, ADR-051) |
| Creates plans from the UI | PASS (plan builder; Duplicate on Plans & Pricing) |
| Changes pricing, monthly and yearly, from the UI | PASS (Plans & Pricing -> Edit price) |
| Controls trials | PASS (Availability trial configuration; plan trial days in the plan builder) |
| Affiliate eligibility + commission | PASS (channel switch; commission % or fixed, recurring, months, plan) |
| Reseller eligibility + margin | PASS (channel switch; margin; price range) |
| White-label eligibility + commercial rules | PASS (channel switch; margin; price range with partner pricing on/off) |
| Customer Store receives correct catalog and pricing | PASS (one list read with the discount applied; browser C) |
| Affiliate approved / explore view | PASS (Store Console, ADR-055, unchanged; now with collections and badges) |
| Reseller sees only granted products, correct pricing and margin | PASS (grants; Pricing & Margins from the engine) |
| White-label granted products and allowed price controls | PASS (browser D) |
| All frontend prices come from the backend | PASS (no price arithmetic or literals in the browser; guard test) |
| Checkout revalidates server-side | PASS (`test_the_browser_cannot_lower_the_price`) |
| Verified payment before a paid entitlement | PASS (existing webhook path; browser C via the test provider) |
| Commission, margin, revenue share server-side | PASS (engine + accrual) |
| Historical transactions keep their original values | PASS (immutable rows, snapshots, `test_an_existing_subscription_keeps_its_price_after_a_rise`) |
| One central catalog and commercial architecture | PASS |
| No duplicated pricing engine | PASS (partner margin arithmetic removed; one discount function) |
| Scales to 20-25+ products | PASS (no product-specific code; architecture guard) |

## 12. Remaining risks

- **Not committed, not deployed; the local dev database is not migrated** (four migrations). The dev backend will error on the new columns until it is migrated (owner approval needed).
- **The full backend suite was not re-run end to end** after the last three fixes (their suites and every touched suite: 387 passed).
- **No tax engine**: prices carry exclusive/inclusive treatment; `tax_minor` is null and amounts are before tax.
- **Live payment gateway** still needs the owner's credentials (unchanged).
- **Partner cost follows the discounted price**: a promotional discount on the direct price lowers a reseller's or white-label partner's cost by the same share (the margin rule is a discount off the price a direct customer pays). If promotions should come out of the platform's share only, that is a rule change.
- **Discounts are grandfathered**: a subscription bought during a promotion renews at the promotional price (its snapshot), like any price.
- **Set-up fees** are charged only through automatic checkout; partner-billed approved sales (no payment step) do not charge them.
- **Campaign-specific commission rules** are not built (campaigns are partner-owned labels).
- A commission set on Commercial rules applies to every partner earning commission on that product (affiliates, and selling partners without a margin rule) - the existing programme-default model.
