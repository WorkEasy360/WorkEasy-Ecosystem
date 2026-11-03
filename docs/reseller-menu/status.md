# Reseller menu consolidation (2026-11-02)

Status: built and verified locally, **uncommitted, not deployed** (branch `fix/ci-run-1`).

This change touches navigation and presentation only. No API, model, migration, permission, pricing, order, commission, payout or ledger code changed. Every reseller page keeps its address, its component and its permission. It uses the same tab mechanism as the affiliate consolidation ([../affiliate-menu/status.md](../affiliate-menu/status.md)).

## Audit: the reseller sidebar in the code

`experiences.ts` defines **15** reseller entries. A member holding every permission sees all 15. **Referrals** needs `referral.view`, which the reseller grant bundle does not include, so a typical reseller sees **14**, which matches the screenshot.

| # | Entry | Pages (addresses) | Permission |
|---|---|---|---|
| 1 | Dashboard | `dashboard`, `upgrade/*` | `dashboard.view` |
| 2 | Customers | `users` | `customer.view` |
| 3 | Store | `store` (marketplace; request to sell) | `product.view` |
| 4 | Store Console | `store-console` (My Products), `/explore`, `/requests`, + `partner-products`, `product-manager`, `product-billing` | `product.view` |
| 5 | Pricing & Margins | `plans`, `plans/add-ons`, `pricing/currency`, `pricing/packages`, `pricing/rules` | `pricing.view` |
| 6 | Sales & Orders | `payments` (orders), `payments/balance` | `customer.view` |
| 7 | Subscriptions | `users/subscriptions` | `customer.view` |
| 8 | Commissions | `payments/commissions`, `payments/balance` | `commission.view` |
| 9 | Payouts | `payouts`, `affiliates/payouts` | `payout.view` |
| 10 | Sub-Resellers | `agency` | `subpartner.view` |
| 11 | Referrals | `affiliates`, `affiliates/conversions`, `affiliates/settings` | `referral.view` |
| 12 | Team & Permissions | `team`, `roles` | `member.view` |
| 13 | Reports & Analytics | `reports` | `dashboard.view` |
| 14 | Support | `support-manager`, `notifications` | `dashboard.view` |
| 15 | Settings | `account` + 10 settings pages | `profile.view` |

The pages already had their own rows of links:
- The pricing pages link to each other: Currency, Package & Pricing, Pricing Rule.
- The orders and commission pages share one row: Orders, Commissions, Balance.
- The Store Console has My Products, Explore Products and My Requests.

The backend serves the balance only with `commission.view`.

## Final sidebar

| Group | Entry | Tabs |
|---|---|---|
| Menu | Dashboard | - |
| Customers and sales | Customers | - |
| | **Product Store** | Explore Products · Approved / Reselling · Request Access · My Requests |
| | **Sales Management** | Pricing & Margins · Sales & Orders |
| | Subscriptions | - |
| Earnings | **Earnings & Payouts** | Commissions · Payouts |
| Network | Sub-Resellers | - |
| | Referrals *(only with `referral.view`, unchanged)* | - |
| Account | Team & Permissions · Reports & Analytics · Support · Settings | - |

A typical reseller sees **11** entries, exactly the requested structure. A member who also holds `referral.view` sees 12, because **Referrals** stays: it exists in the code, the brief does not list it, and removing it would remove a feature.

## Original-to-new mapping

| Original entry | New entry › tab | Pages (unchanged addresses) |
|---|---|---|
| Dashboard | Dashboard | `dashboard` |
| Customers | Customers | `users` |
| Store | Product Store › Explore Products | `store` |
| Store Console › My Products | Product Store › Approved / Reselling | `store-console`, and the pages it links to: `partner-products`, `product-manager`, `product-billing` |
| Store Console › Explore Products | Product Store › Request Access | `store-console/explore` |
| Store Console › My Requests | Product Store › My Requests | `store-console/requests` |
| Pricing & Margins | Sales Management › Pricing & Margins | `plans`, `plans/add-ons`, `pricing/currency`, `pricing/packages`, `pricing/rules` (their own Currency / Package & Pricing / Pricing Rule row stays) |
| Sales & Orders | Sales Management › Sales & Orders | `payments` |
| Subscriptions | Subscriptions | `users/subscriptions` |
| Commissions | Earnings & Payouts › Commissions | `payments/commissions`, `payments/balance` (its own Commissions / Balance row stays) |
| Payouts | Earnings & Payouts › Payouts | `payouts`, `affiliates/payouts` |
| Sub-Resellers | Sub-Resellers | `agency` |
| Referrals | Referrals | `affiliates`, `affiliates/conversions`, `affiliates/settings` |
| Team & Permissions · Reports & Analytics · Support · Settings | unchanged | unchanged |

**Old addresses and bookmarks.** Every page keeps its address, so old links open the same page with its new entry highlighted. The earlier short addresses (`products`, `pricing`, `orders`, `sales`, `commissions`, `customers`, ...) are unchanged. New short addresses also open the merged entries: `product-store`, `sales-management`, `earnings`, `earnings-payouts`.

**Balance.** Balance was listed under both Sales & Orders and Commissions. It now sits only under Earnings & Payouts › Commissions, because it is the commission ledger's sum and the backend requires `commission.view` for it. A member without that permission could open the page before but only got refused, so nothing they could actually use was lost.

## How it works

- `experiences.ts`: the reseller uses `tabbed()` for Product Store and Sales Management, and the shared `earnings()` for Earnings & Payouts. A tab may own several pages (`also`), which keeps a page's sub-pages under its tab.
- `pageAllowed` checks the tab's own permission. An orders-only role cannot open pricing and lands on Sales & Orders; a pricing-only role cannot open orders; a payouts-only role lands on Payouts and cannot open commissions or balance.
- `EntryTabs` (PanelKit) draws the entry's tabs. Inside it, a page's own `SubNav` keeps only its links to the open tab's pages and draws them as a second row named "Pages". Links to other tabs are dropped because the tab row already shows them, so nothing appears twice. The page's counts still go to the matching tab.
- Wording in the reseller Store and Store Console: "My Products" becomes "Approved / Reselling" (`WORDS.sell.mine`).
- Removed the now-unused `storeConsole()` entry helper.
- White-label is unchanged. The affiliate menu is unchanged; its behaviour is identical under the second-row rule, and its tests pass.

## Verification

- Frontend: `vitest` 1,039 / 1,039 (partner panel 184). New tests:
  - Each original reseller page has exactly one owning entry and the expected tab.
  - The reseller workspace serves exactly the same set of pages as before.
  - Old and new short addresses.
  - Per-tab permissions (orders-only, pricing-only, payouts-only).
  - Rendering: Sales Management's tabs with the pricing pages' own row under them; Earnings & Payouts with Commissions / Balance under the Commissions tab.
- `tsc` (src) and `eslint` are clean.
- Backend: 945 passed (`architecture/test_frontend_image_runtime`, `partner_experience`, `partner_menu`, `partner_panel`, `partner_upgrade`, `store_console` incl. the reseller Store Console, `store_upgrade` (prices, margins, channel pricing, commissions), `phase10` (reseller, sub-resellers, tenant isolation, orders, payouts)).
- Browser: the reseller workspace on the dev server, with API answers mocked because no reseller login exists for the dev database. It shows 11 entries, Sales Management current, and the tab row with the page row under it.
- Data: no migration and no backend change, so no record, price, order, commission or payout can change, and nothing can be credited or paid twice.

## Not consolidated

Nothing was dropped. **Referrals** stays a separate, permission-gated entry. **Balance** moved under Commissions for the reason above.

## Files changed

- `frontend/src/features/partnerpanel/experiences.ts` - reseller entries, `earnings()`, tab `also` for sub-pages, new aliases, `storeConsole()` removed
- `frontend/src/features/partnerpanel/PanelKit.tsx` - `EntryTabs` passes the open tab's pages; `SubNav` keeps those as a second row
- `frontend/src/features/partnerpanel/StoreConsole.tsx`, `workspacePages.tsx` - "Approved / Reselling" wording
- Tests: `experiences.test.ts`, `workspace.test.tsx`
