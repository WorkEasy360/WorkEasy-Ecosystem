# Product-aware Affiliate Panel: status

Brief: *Integrate WorkEasy360 Affiliate Panel with Admin Product Catalog, Permissions, Product Updates, and Commission Tracking* (2026-10-28). Decisions: `docs/architecture/adrs/ADR-054-product-aware-affiliate-links.md`.

## Verdict

**Built and verified locally; not production-ready.** All 11 brief flows pass as automated tests through the real referral, order, signed-payment-webhook, refund, accrual, ledger and payout code (test payment provider). Not done: a real-browser run, deployment, and a live payment gateway (owner credentials). Uncommitted on `fix/ci-run-1`; the development database is not migrated (needs the owner's go-ahead).

## 1. Existing architecture (inspected before changing anything)

| Area | What already existed | Kept as is |
|---|---|---|
| Catalog | `catalog.Product` is the single source (code, name, slug, status, listing, store text, `marketing_url`, channel flags incl. `affiliate_enabled`), `ProductMedia` (approved logo/banner/screenshots), public `Feature`s | yes - extended with `ProductUpdate` |
| Partner permissions | `PartnerCapabilityGrant` (`referral.create`, ...), `PartnerProductGrant` (may **sell**), `PartnerMember` roles, `StaffGrant` + named staff roles | yes - promote added beside them |
| Referral links | `ReferralCode` (partner, code, label, product, plan, campaign), `ReferralCampaign`, public `/r/<code>` -> server-derived path, `Referral` (hashed token, visitor hash, registered/converted) | yes - kind + update added |
| Ledger | `CommissionAccrual` append-only by trigger, `idempotency_key` unique, rule version/rate/basis snapshotted, reversals capped, `PayoutLine` unique while live, payout approval re-derives totals, `reconcile()` | yes - one column added |
| Payments | orders -> invoice -> signed provider webhook; refunds update `Invoice.amount_refunded_minor` | yes |

## 2. Gaps found

| # | Gap | Fixed by |
|---|---|---|
| G1 | Any affiliate could link **any** affiliate-open product: no per-affiliate product permission | `PartnerPromotionGrant`, checked in `create_referral_code` and at capture |
| G2 | No feature or update targets, no approved update content | `catalog.ProductUpdate`, public update page, link kinds `feature`/`update` |
| G3 | The capture endpoint **overwrote** the referral cookie: last click won, while the code and the Affiliate settings page said first touch | live held token kept; click still counted |
| G4 | Referral commission accrued on subscription activation, not on a paid transaction | referral sales accrue only from a paid invoice line; `payment_ref` stored |
| G5 | A refunded first sale was never reversed automatically (only renewals and immediate cancellations) | proportional refund reversal inside the holdback |
| G6 | No Trial step in the funnel | `Referral.trial_started_at` |
| G7 | No rename / switch back on; 20 active links per partner | `PATCH referral-codes/<id>`; limit 100 |
| G8 | No per-conversion attribution trail; partner ledger rows had no status | trail (partner + staff); `pending/approved/reversed/paid` per row |
| G9 | Window fixed in settings; referral cookie 30 days vs 90-day window | `ReferralPolicy` (Super Admin); cookie = window |
| G10 | Chargebacks are not modelled by `payments` | not built - staff reverse with reason `chargeback` (see Remaining) |

## 3. Changes

Backend: `commercial/partners/promotion.py` (new), `commercial/catalog/updates.py` (new), `commercial/commissions/policy.py` (new), `commissions/referrals.py`, `commissions/accrual.py`, `commissions/selectors.py`, `commissions/domain.py` (`refund_share`), `catalog/selectors.py`, `payments/selectors.py` (refunded amounts), `subscriptions/selectors.py` (`first_trial_started_at`), `controlplane/partner_workspaces.py` (`my_products`, `referral_trail`), `controlplane/partner_detail.py` (staff trail), views and routes. Removed: `REFERRAL_COOKIE_MAX_AGE` (dead once the cookie follows the policy); `affiliate_overview` and `my_products` share one link-row builder.

Frontend: `partnerpanel/AffiliateProducts.tsx` (My Products, link generator, link table, trail panel), sidebar entry under Promote (`experiences.ts`), Conversions trail, `controlplane/ResellerTabs.tsx` (promotion grants, admin trail), `productmanager/UpdatesTab.tsx`, `PartnerProgramme.tsx` (policy), public `/products/<slug>/updates/<update>`, `?view=pricing` scroll.

Migrations: `catalog.0006_product_updates`, `partners.0011_promotion_grants`, `commissions.0006_affiliate_products` (see ADR-054 Consequences).

## 4. API

| Method | Path | Who | What |
|---|---|---|---|
| GET | `partner/<id>/affiliate/products?range=` | partner `referral.view` | My Products (money with `commission.view`) |
| POST | `partner/<id>/referrals` | `referral.create` + promotion grant | body adds `link_kind`, `update_id` |
| PATCH | `partner/<id>/referral-codes/<code>` | `referral.create` | `{label}` and/or `{active}` |
| GET | `partner/<id>/referrals/<referral>/trail` | `referral.view` | attribution trail |
| GET/POST | `admin/partners/<id>/promotions` | staff `partner.view` / `partner.manage` | list / grant `{product_code}` |
| POST | `admin/partners/<id>/promotions/<grant>/revoke` | `partner.manage` | revoke |
| GET/PUT | `admin/affiliate-policy` | `partner.view` / `partner.manage` | `{attribution_days, revoked_links}` |
| GET | `admin/partners/<id>/referrals/<referral>/trail` | `partner.view` + `commission.view` | staff trail |
| GET/POST | `admin/products/<id>/updates` | `catalog.view` / `catalog.manage` | list / draft |
| PATCH | `admin/products/<id>/updates/<update>` | `catalog.manage` | edit (address fixed once published) |
| POST | `admin/products/<id>/updates/<update>/{publish,archive}` | `catalog.manage` | lifecycle |
| GET | `public/products/<slug>/updates/<update>` | public, rate-limited | published update of a Store product |

## 5. Permission and attribution rules

- Promote needs: partner active, `referral.create`, product published on the Store and open to affiliates, active promotion grant. Sell and use are separate permissions.
- First touch inside the window; later clicks counted, never re-attributed; a converted or expired token is replaced by the next click.
- A referral earns on a sale only if: converted inside the window, the customer was new (30 days) and not partner-managed, the referrer is not one of its own people, the sale is within 90 days of conversion (or a renewal under a recurring rule), the product is open to affiliates, a product link's product matches, and the sale's invoice line is **paid**.
- Amount: rule selected and snapshotted at the sale (partner > programme, product > all), integer minor units, half-up. Later rule versions never change a recorded row.
- Reversal: immediate cancellation or refund inside the holdback (proportional for a refund); otherwise staff. A payout never includes an entry in another live payout and refuses approval if an entry was reversed after preparation (unchanged).

## 6. Tests

| Gate | Result |
|---|---|
| Full backend suite (`pytest -q`, no path) | 4,925 passed, 5 failed: four deliberate model/migration pins and one settings grep that matched the new host default. All fixed; those files re-run: 68 passed |
| New `backend/tests/affiliate_products` (the brief's 11 flows) | 18 passed |
| Earlier regression set (ecosystem, store, phase7, partner*, product_manager, architecture, performance, extension, security) | 1,607/1,621 at first; the 14 were intended rule changes or pins, updated and re-run green |
| Migration rehearsal `scripts/affiliate_products_check/prepare.py` (HEAD data -> working tree, reverse, re-apply) | PASS 24/24: every existing row byte-identical, product link -> `product`, grant only for that partner/product, ledger UPDATE still refused |
| ruff, ruff format, mypy, lint-imports (18 kept), bandit `-ll`, `makemigrations --check`, `manage.py check` | clean |
| Frontend tsc, eslint, vitest | clean; 971/971 (5 new) |
| `next build`, dynamic-rendering assert, `npm audit` | pass; 0 vulnerabilities |
| Real browser journeys | **not run** |

Brief flow -> test (`backend/tests/affiliate_products/test_affiliate_products.py`):
1 `test_a_granted_product_gets_many_links_each_its_own_tracking_id` · 2 `test_an_ungranted_product_can_be_neither_linked_nor_seen`, `test_only_staff_holding_partner_manage_assign_promotions` · 3 `test_a_product_update_gets_its_own_trackable_link`, `test_a_draft_update_cannot_be_linked_and_its_landing_page_is_approved` · 4 `test_a_referred_customer_who_pays_earns_exactly_one_commission`, `test_a_product_link_earns_only_on_the_product_it_promoted`, `test_an_unpaid_referred_sale_earns_nothing_until_it_is_paid` · 5 `test_the_referral_survives_checkout_and_a_replayed_payment_webhook`, `test_first_touch_a_later_click_never_steals_the_referral` · 6 same (replayed webhook, repeated accrual + sweep) · 7 `test_a_refund_reverses_its_share_of_the_commission` · 8 `test_changing_the_rule_leaves_earned_commission_as_it_was` · 9 `test_revoking_a_promotion_stops_new_links_and_keeps_what_was_earned` · 10 `test_partners_cannot_touch_each_others_links_or_trails`, `test_the_trail_tells_the_story_without_naming_the_customer` · 11 `test_general_links_and_the_dashboard_work_as_before` + the regression suites.

## Remaining

- **Chargebacks** are not modelled by `payments` (no dispute webhook). Staff record them as a ledger reversal with reason `chargeback`.
- **Refunds after the holdback** are not reversed automatically. That stays a staff decision, as for renewals.
- **Earlier referral accruals on unpaid sales**: any made before this change (on an invoiced or staff-entered sale) would now be reported by `reconcile()` as `unexpected (awaiting_payment)`. It is a report only; nothing is rewritten.
- **Staff scoping is role-based.** There is no per-partner staff scope on the platform.
- **Production**: a live payment gateway still needs the owner's credentials; the release needs deploying with the three migrations; the dev DB needs `migrate` (owner approval). Real-browser journeys are still to run.
- `pip-audit` was not re-run: no dependency changed. The earlier `dnspython` advisory stands.
