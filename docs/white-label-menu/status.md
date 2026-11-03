# White-label menu consolidation (2026-11-02): 18 entries become 12

Status: built and verified locally, **uncommitted, not deployed** (branch `fix/ci-run-1`).

This change touches navigation and presentation only. No API, model, migration, permission, branding, domain (DNS proof, certificate, lifecycle), pricing, order, billing, ledger, payout or network code changed. Every white-label page keeps its address, its component and its permission. It uses the same tab mechanism as the affiliate and reseller consolidations ([../affiliate-menu/status.md](../affiliate-menu/status.md), [../reseller-menu/status.md](../reseller-menu/status.md)).

## Initial audit: the white-label sidebar in the code

`experiences.ts` defines **18** white-label entries. A member sees each one only if they hold its permission.

| # | Entry | Pages (addresses) | Permission |
|---|---|---|---|
| 1 | Dashboard | `dashboard`, `upgrade/*` | `dashboard.view` |
| 2 | Brand Settings | `brand-settings`, `appearance` | `branding.view` |
| 3 | Custom Domain | `brand-settings/url-manager` | `domain.view` |
| 4 | Website Branding | `brand-settings/white-label-setup` | `branding.view` |
| 5 | Store | `store` (marketplace; request to sell) | `product.view` |
| 6 | Products & Plans | `partner-products`, `product-manager`, `product-billing` | `product.view` |
| 7 | Pricing & Plans | `plans`, `plans/add-ons`, `pricing/currency`, `pricing/packages`, `pricing/rules` | `pricing.view` |
| 8 | Customers | `users` | `customer.view` |
| 9 | Subscriptions | `users/subscriptions` | `customer.view` |
| 10 | Orders & Billing | `payments`, `payments/balance` | `customer.view` |
| 11 | Commissions | `payments/commissions`, `payments/balance` | `commission.view` |
| 12 | Payouts | `payouts`, `affiliates/payouts` | `payout.view` |
| 13 | Sub-Resellers | `agency` | `subpartner.view` |
| 14 | Referrals | `affiliates`, `affiliates/conversions`, `affiliates/settings` | `referral.view` |
| 15 | Team & Permissions | `team`, `roles` | `member.view` |
| 16 | Reports & Analytics | `reports` | `dashboard.view` |
| 17 | Support | `support-manager`, `notifications` | `dashboard.view` |
| 18 | Settings | `account` + 10 settings pages | `profile.view` |

The pages listed are exhaustive. The brief's target lists 8 entries; **Team & Permissions, Reports & Analytics, Support and Settings** were missing from it and are kept, which gives 12.

**A bug found during the audit.** The sidebar matched by address prefix, and `brand-settings` is a prefix of `brand-settings/url-manager` and `brand-settings/white-label-setup`. So on Custom Domain or Website Branding, **two** entries showed as current. This is now fixed for every partner workspace: an entry is current on exactly its own pages (`exactPaths`).

The pages already had their own rows of links:
- Brand pages: Branding, URL Manager, White-label setup (the last only for members who can manage both branding and domains).
- The Users page: Customers, Subscriptions.
- The pricing pages: Currency, Package & Pricing, Pricing Rule.
- The Orders and Commissions pages: Orders, Commissions, Balance.
- Referrals: Overview, Conversions, Payouts, Settings.

## Final sidebar

| Group | Entry | Tabs |
|---|---|---|
| Menu | Dashboard | - |
| Brand | **Brand Studio** | Brand Settings · Website Branding |
| | Custom Domain *(unchanged)* | - |
| Customers and sales | **Product Store** | Store · Products & Plans |
| | **Sales & Billing** | Pricing & Plans · Orders & Billing |
| | **Customer Management** | Customers · Subscriptions |
| Earnings | **Earnings & Payouts** | Commissions · Payouts |
| Network | **Partner Network** | Sub-Resellers · Referrals |
| Account | Team & Permissions · Reports & Analytics · Support · Settings *(unchanged)* | - |

## Old-to-new mapping

| Original entry | New entry › tab | Pages (unchanged addresses) |
|---|---|---|
| Dashboard | Dashboard | `dashboard` |
| Brand Settings | Brand Studio › Brand Settings | `brand-settings`, `appearance` |
| Website Branding | Brand Studio › Website Branding | `brand-settings/white-label-setup` |
| Custom Domain | Custom Domain | `brand-settings/url-manager` (its own row to Branding / setup stays) |
| Store | Product Store › Store | `store` |
| Products & Plans | Product Store › Products & Plans | `partner-products`, `product-manager`, `product-billing` |
| Pricing & Plans | Sales & Billing › Pricing & Plans | `plans`, `plans/add-ons`, `pricing/*` (their own Currency / Package & Pricing / Pricing Rule row stays) |
| Orders & Billing | Sales & Billing › Orders & Billing | `payments` |
| Customers | Customer Management › Customers | `users` |
| Subscriptions | Customer Management › Subscriptions | `users/subscriptions` |
| Commissions | Earnings & Payouts › Commissions | `payments/commissions`, `payments/balance` (Commissions / Balance row stays) |
| Payouts | Earnings & Payouts › Payouts | `payouts`, `affiliates/payouts` |
| Sub-Resellers | Partner Network › Sub-Resellers | `agency` |
| Referrals | Partner Network › Referrals | `affiliates`, `affiliates/conversions`, `affiliates/settings` (Overview / Conversions / Settings row stays) |
| Team & Permissions · Reports & Analytics · Support · Settings | unchanged | unchanged |

**Old addresses and bookmarks.** Every page keeps its address, so old links open the same page with its new entry highlighted. The earlier short addresses (`brand`, `domain`, `domains`, `branding`, `products`, `pricing`, `orders`, `customers`, `subscriptions`, `commissions`, `sub-resellers`, `referrals`, ...) are unchanged. New short addresses also open the merged entries: `brand-studio`, `product-store`, `sales-billing`, `customer-management`, `earnings`, `earnings-payouts`, `partner-network`.

**Balance.** Balance was listed under both Orders & Billing and Commissions. It now sits only under Earnings & Payouts › Commissions, because it is the commission ledger's sum and the backend serves it only with `commission.view`. This matches the reseller workspace.

**Custom Domain stays its own entry**, as the brief asks. Domain verification, DNS, the certificate and the domain lifecycle are a separate job with a separate permission (`domain.view` / `domain.manage`).

## How it works

- `experiences.ts`:
  - The white-label workspace is built with `tabbed()`.
  - `sales()` is shared with the reseller: Sales Management there, Sales & Billing here. The pricing and order pages and services stay separate; only the navigation is shared.
  - `earnings()` is shared with the reseller.
  - `asTab()` reuses the existing Customers, Subscriptions, Sub-Resellers and Referrals entries (page, sub-pages, permission) as tabs, so each definition exists once.
- **Each tab keeps its own permission.** Pricing and orders stay separate permissions. A referrals-only partner sees only Partner Network, lands on Referrals and cannot open Sub-Resellers. Branding and domain stay separate permissions.
- `EntryTabs`: a page's own row keeps only its links to the open tab's pages, as a second row named "Pages". Links to other tabs are dropped because the tab row already shows them.
- `SidebarShell` gained an optional `exactPaths`, used by the partner workspaces only. It fixes the double highlight described above.
- Removed six one-page entry helpers that nothing uses any more: `store`, `productsAndPlans`, `pricing`, `orders`, `commissions` and `payouts`.

## Verification

- Frontend: `vitest` 1,045 / 1,045 (partner panel 190). New tests:
  - Each of the 18 entries' pages has exactly one owning entry and the expected tab.
  - The white-label workspace serves exactly the same set of pages as before.
  - Old and new short addresses.
  - Per-tab permissions (domain vs branding, referrals-only, pricing vs orders).
  - Rendering:
    - Custom Domain is the only current entry on its page.
    - Brand Studio's tabs are current on the setup page.
    - Customers / Subscriptions are drawn once, not twice.
    - Partner Network keeps Referrals' Overview / Conversions / Settings row and leaves out Payouts, which lives under Earnings.
- `tsc` (src) and `eslint` are clean.
- Backend: 1,176 passed (`architecture/test_frontend_image_runtime`, `partner_experience`, `partner_menu`, `partner_panel`, `partner_upgrade`, `partner_setup` (branding, domains, white-label setup), `phase10` (reseller/white-label, custom domains, sub-resellers, tenant isolation, orders, payouts), `phase11/test_11g_reseller_whitelabel`).
- Browser: the white-label workspace on the dev server, with API answers mocked because no white-label login exists for the dev database. It shows 12 entries, only Brand Studio current, and its two tabs.
- Data: no migration and no backend change, so no record, brand, domain, price, order, commission or payout can change.

## Not consolidated

Nothing was dropped. Custom Domain stays a separate entry, as the brief asks. The Account group stays as it is. Balance moved under Commissions for the reason above.

## Files changed

- `frontend/src/features/partnerpanel/experiences.ts` - white-label entries, `sales()`, `asTab()`, new aliases, six helpers removed
- `frontend/src/features/partnerpanel/WorkspaceShell.tsx` - each sidebar entry is current on exactly its own pages
- `frontend/src/components/sidebar/SidebarShell.tsx` - optional `exactPaths`
- Tests: `experiences.test.ts`, `workspace.test.tsx`
