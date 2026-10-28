# Store Working Flow - audit, traceability and status

Source: the owner's *Workeasy360_Store_Working_Flow.pdf* (10 pages, 2026-10-28). Decisions: [ADR-051](../architecture/adrs/ADR-051-product-manager-central-catalog.md) (Product Manager), [ADR-052](../architecture/adrs/ADR-052-store-cart-eligibility-recurring-commission.md) (cart, eligibility, recurring commission, referral cookie). Nothing here is committed or deployed.

## 1. Page ledger

All ten pages were read in full (text and diagrams). The document contains **no AI prompts, prompt templates or prompt chains**, so there is no prompt library to extract; it is a screen-to-screen and backend flow description.

| Page | Title | Content | Extracted |
|---|---|---|---|
| 1 | Working Flow (cover) | Store -> Plan -> Checkout -> Entitlement -> My Products | yes |
| 2 | Customer Store end-to-end | Login, open store (`app.workeasy360.com/store`), product, plan, trial or buy; checkout, payment, subscription, entitlement, My Products; open, SSO hand-off, product session. Rule: *Store sells -> Subscription records -> Entitlement grants -> Product runs* | yes |
| 3 | Multi-product cart | Add CRM, EasyBook, Project -> cart (3 lines) -> price engine -> one order, one payment -> paid -> three subscriptions -> three entitlements. Rule: *payment fails -> no paid entitlement* | yes |
| 4 | Trial | Eligibility, trial record, trial entitlement; before/at/after expiry; buying during a trial replaces it | yes |
| 5 | Reseller store | Granted products only, customer, allowed price-book entries, server price/margin, approved sale, subscription, entitlement, partner ledger. Rule: *only products granted by Super Admin* | yes |
| 6 | Affiliate store | Link, click (attribution ID/cookie), registration, purchase, payment, attribution match, immutable accrual, dashboard; renewal -> recurring rule -> new accrual. Rule: *no commission from a browser-only event* | yes |
| 7 | White-label store | Brand, DNS-verified domain, granted products, allowed pricing; branded store over the central catalog, central checkout and entitlement. Rule: *branding changes presentation, not authorization* | yes |
| 8 | Super Admin store control | Add product, plans, pricing, trial, publish; partner eligibility, grant, commercial rules (margin/commission/setup fee), effective dates; customer store, partner store, reporting. Rule: *one catalog, different views* | yes |
| 9 | Backend sequence | Validate user/org, product/plan, resolve price, order, payment intent, verify, subscription, entitlement, events, My Products; payment failure, duplicate webhook, product launch revalidation | yes |
| 10 | Example: CRM + EasyBook | One checkout, two line items, two subscriptions, two entitlements, two SSO launches | yes |

## 2. Traceability

| Req | Page | Expected | Implementation (evidence) | Verified by | Status |
|---|---|---|---|---|---|
| S-1 | 2 | One account signs in | central sign-in, `/login` | existing suites | done |
| S-2 | 2 | Store at `/store` | `app/store/page.tsx` -> `/o/<org>/explore`; `lib/portals.ts` | build route list | **added** |
| S-3 | 2 | Choose product + plan, trial or buy | `ExploreScreen`, `trials.start_product_trial`, `payments/orders.py` | Phase 9/11 suites | done |
| S-4 | 2, 9 | Order on server, server price | `orders._quote` -> `pricing.resolve_price`; snapshot frozen | `tests/phase9/test_orders.py` | done |
| S-5 | 2, 9 | Payment verified server-side | `payments/webhooks.process` (HMAC, dedupe `uq_webhook_event_once`) | Phase 8/9 suites | done |
| S-6 | 2, 9 | Subscription, then entitlement, then My Products | `orders.fulfil` -> `system.add_paid_line` -> provisioning | `test_orders`, `test_cart` | done |
| S-7 | 2, 9 | SSO launch re-checks entitlement | `launcher/access.can_launch_product` | Phase 11 suites | done |
| C-1 | 3, 10 | **Cart: several products, one order, one payment** | `orders._quote_cart`, `Order.lines`, itemised invoice, all-or-nothing fulfilment; `cart.ts`, Explore "Add to cart", checkout table | `tests/store/test_cart.py` (14), `cart.test.tsx`, `commerce.test.tsx` | **added** |
| C-2 | 3 | Payment fails -> no paid entitlement | order stays pending, expires | `test_a_failed_payment_grants_nothing` | done |
| C-3 | 3, 10 | One subscription line + entitlement per product | per-line `add_paid_line` | `test_two_products_one_order...` | **added** |
| T-1..4 | 4 | Trial eligibility, expiry, deny after, convert on purchase | `trials.py`, `system.settle_product_trials`, `access.py` | `tests/phase11/test_11b_trials.py` | done |
| R-1 | 5 | Only granted products | `may_sell` (grant + switch) | Phase 7 suites | done |
| R-2 | 5, 8 | Product must allow resellers | `selectors.product_open_to_partner` at grant and sale | `test_channel_eligibility.py` | **added** |
| R-3 | 5 | Server price / margin | partner price book via `resolve_price(partner_id)`; no margin model | Phase 7 | partial (no margin model) |
| R-4 | 5 | Customer payment / approved sale | reseller sale is an approved sale (no payment step) - "according to configured commercial flow" | Phase 7 | as designed |
| A-1 | 6 | Link, click, attribution | `referrals.capture_referral` / `convert_referral` | Phase 7 | done |
| A-2 | 6 | **Attribution ID/cookie survives to registration** | HttpOnly `__Host-w360_ref` cookie; convert falls back to it | `test_referral_cookie.py` | **added (fixed new-tab loss)** |
| A-3 | 6 | Accrual only after verified payment, immutable | accrual on activation, which follows payment; ledger append-only | Phase 7 | done |
| A-4 | 6 | **Recurring commission on renewal** | `CommissionRule.recurring`, `accrual.evaluate_renewal`, refund reversal | `test_recurring_commission.py` (6) | **added (opt-in)** |
| A-5 | 6, 8 | Product must allow affiliates | `evaluate_sale` / `evaluate_renewal` skip | `test_channel_eligibility.py` | **added** |
| W-1..2 | 7 | Brand, DNS-verified domain | `partners/branding.py`, `domains.py` | Phase 10 | done |
| W-3 | 7, 8 | Product must allow white-label | branded partner needs `white_label` | `test_channel_eligibility.py` | **added** |
| W-4 | 7 | Branded store on the partner's domain | public catalog ignores the host | - | **gap (owner decision)** |
| W-5 | 7 | Partner-set pricing within rules | staff set partner books | - | **gap** |
| M-1..5 | 8 | Product, plans, price, trial, publish | Product Manager (ADR-051), Plans, Pricing | `tests/product_manager` | done |
| M-6 | 8 | Setup fee | none | - | **gap (owner decision)** |
| M-7 | 8 | Effective-dated rules | versioned `CommissionRule` | Phase 7 | done |
| M-8 | 8 | Reporting | admin orders, payments, accruals, payouts, dashboard | Phase 8/9 | done |
| B-1 | 9 | Duplicate webhook -> no duplicates | webhook dedupe; accrual keys | Phase 8/9, `test_fulfilment_runs_once` | done |
| B-2 | 9 | Live payment gateway | Stripe / Razorpay adapters; needs credentials | - | **blocked (credentials)** |

## 3. Defects found and fixed in this pass

| # | Severity | Defect | Fix |
|---|---|---|---|
| D-1 | High | Referral lost when the verification email opens a new tab (token only in `sessionStorage`) | server cookie fallback (A-2) |
| D-2 | High (latent) | A recurring rule would have made the commission listener fail for every customer (invoice read without tenant scope) - caught by the new tests before release | `payments.selectors.paid_item_lines` binds both isolation layers |
| D-3 | Medium | Product-code examples in source broke the "no product-specific code" architecture test | neutral examples |

## 4. Risks and debt

- **Recurring commission is money logic.** Default off; enable per rule after reviewing the holdback (30 days) and payout process. A renewal refunded *after* its holdback is not reversed automatically (staff reversal, as today).
- **Reseller sales have no payment step** (approved-sale model). If the owner wants partner-collected payment, that is a new flow.
- **No margin model** for resellers; partner earnings are commission only.
- **pip-audit**: transitive `filelock 4.1.0` advisories (fixed in 4.2.1) - pre-existing, no dependency file changed here.
- **Debt**: no console screen for commission rules (API only).

## 5. Roadmap (needs owner input)

1. Live gateway: provide Stripe or Razorpay credentials -> configure provider in the console (no code change).
2. Setup fees: amount per partner type, who pays, when; then a one-time order kind reusing the cart/order path.
3. Branded storefront: which products, which prices (partner book vs list), sign-up on the partner domain (also D-035).
4. Recurring commission switch in the console's partner commission tab.

## 6. Verification (2026-10-28, local)

See the session's final report for the exact counts: backend targeted suites (store 28, Product Manager 75+), full backend suite, frontend vitest 954/954, tsc, eslint, production build, npm audit 0, migration rehearsal PASS, real-browser check.
