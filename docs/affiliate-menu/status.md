# Affiliate menu consolidation (2026-11-02): 14 entries become 9

Status: built and verified locally, **uncommitted, not deployed** (branch `fix/ci-run-1`).

This change touches navigation and presentation only. No API, model, migration, permission, approval workflow, attribution or commission code changed. Every affiliate page keeps its address, its component and its permission.

## Audit (what each original entry is)

| # | Original entry | Address(es) | Component | Permission |
|---|---|---|---|---|
| 1 | Dashboard | `dashboard` (+ `upgrade/*`) | `AffiliateDashboard` | `dashboard.view` |
| 2 | Store | `store` | `PartnerStore kind=promote` (marketplace, request access) | `referral.view` |
| 3 | Store Console | `store-console`, `/explore`, `/requests` | `StoreConsole` (My Products, product list with requests, request history) | `referral.view` |
| 4 | Referral Links | `referral-links` | `ReferralLinks` | `referral.view` |
| 5 | Referral Codes | `referral-codes` | `ReferralCodes` | `referral.view` |
| 6 | Campaigns | `campaigns` | `Campaigns` | `referral.view` |
| 7 | Marketing Materials | `marketing-materials` | `MarketingMaterials` | `referral.view` |
| 8 | Leads & Referrals | `leads` | `Leads` | `referral.view` |
| 9 | Conversions | `conversions` (+ `affiliates`, `affiliates/conversions`, `affiliates/settings` through its own tabs) | `AffiliateConversions`, `Affiliates`, `AffiliateSettings` | `referral.view` |
| 10 | Commissions | `payments/commissions` (+ `payments/balance` through its own tabs) | `PaymentCommissions`, `Balance` | `commission.view` |
| 11 | Payouts | `payouts` (+ `affiliates/payouts`) | `AffiliatePayouts` | `payout.view` |
| 12 | Reports & Analytics | `reports` | `Reports` | `dashboard.view` |
| 13 | Profile & Settings | `account` | `Account` | `profile.view` |
| 14 | Support | `support-manager` (+ `notifications`) | `Support`, `Notifications` | `dashboard.view` |

Store and Store Console are **not** the same page. Store is the marketplace view. Store Console holds the approved list (with each product's links), a table of products to request, and the request history. All of them stay.

## Old-to-new mapping

| New entry (sidebar) | Tab | Page address (unchanged) | Was |
|---|---|---|---|
| Dashboard | - | `dashboard` | Dashboard |
| **Product Store** | Explore Products | `store` | Store |
| | Approved / Promoting | `store-console` | Store Console › My Products |
| | Request Access | `store-console/explore` | Store Console › Explore Products |
| | My Requests | `store-console/requests` | Store Console › My Requests |
| **Referral Tools** | Referral Links | `referral-links` | Referral Links |
| | Referral Codes | `referral-codes` | Referral Codes |
| **Marketing Hub** | Campaigns | `campaigns` | Campaigns |
| | Marketing Materials | `marketing-materials` | Marketing Materials |
| **Referral Performance** | Leads & Referrals | `leads` | Leads & Referrals |
| | Conversions | `conversions`, `affiliates/conversions` | Conversions |
| | Link Overview | `affiliates` | Conversions › Overview |
| | Programme Settings | `affiliates/settings` | Conversions › Settings |
| **Earnings & Payouts** | Commissions | `payments/commissions` | Commissions |
| | Payouts | `payouts`, `affiliates/payouts` | Payouts |
| | Balance | `payments/balance` | Commissions › Balance |
| Reports & Analytics | - | `reports` | Reports & Analytics |
| Profile & Settings | - | `account` | Profile & Settings |
| Support | - | `support-manager` | Support |

The brief listed eight entries. The ninth is **Support**, kept unchanged because it was in the original 14 and fits none of the merged groups.

The brief asked for two tabs on Product Store, Referral Performance and Earnings & Payouts. Each of those entries has extra tabs (Request Access, My Requests, Link Overview, Programme Settings, Balance). Those pages already existed as sub-tabs of the original pages; they are kept rather than deleted.

**Old addresses and bookmarks.** Every page keeps its address, so old links open the same page with its new entry highlighted. The earlier short addresses (`my-products`, `links`, `codes`, `materials`, `referrals`, `commissions`, ...) are unchanged. New short addresses also open the merged entries: `product-store`, `referral-tools`, `marketing-hub`, `referral-performance`, `earnings`, `earnings-payouts`.

## How it works

- `experiences.ts`: an entry may have `tabs`. Each tab may have its own `needs` and older addresses (`also`). `tabbed()` builds the entry: it opens the first tab, and every tab's pages belong to it.
- `pageAllowed` checks the tab's own permission as well as the entry's. A payouts-only member still cannot open Commissions or Balance, and lands on Payouts.
- `WorkspaceShell` wraps a page whose entry has tabs in `EntryTabs`, which draws one row of tabs (only the ones the person may open). A page's own `SubNav` draws nothing inside `EntryTabs`; it only passes its counts to the matching tab, e.g. "My Requests (2 pending)". No page shows two rows of tabs.
- The reseller and white-label workspaces have no tabbed entries, so nothing changes for them. The generic `/partner/<id>` panel is unchanged too.
- Affiliate-only wording follows the new names: the Store says approved products are under "Approved / Promoting", and the Store Console shows "Open in Approved / Promoting". Resellers still see "My Products" (`WORDS[kind].mine`).

## Verification

- Frontend: `vitest` 1,035 / 1,035 (partner panel 180). New tests cover the 14-to-9 mapping (each original page has exactly one owning entry and the expected tab, and no page was added or dropped), per-tab permissions, the old and new addresses, one row of tabs with counts, and an old address opening under its new entry. `tsc` (src) and `eslint` are clean.
- Backend tests that read these frontend files or exercise the affiliate flows: 496 passed (`architecture/test_frontend_image_runtime`, `partner_experience`, `partner_menu`, `partner_panel`, `partner_upgrade`, `store_console`, `affiliate_products`). These cover product approval, link and code creation, attribution, conversions, commissions and payouts through the real API.
- Browser: the affiliate workspace on the dev server, with API answers mocked because no affiliate login exists for the dev database. It shows 9 sidebar entries, Product Store highlighted, and one tab row with counts.
- Data: no migration and no backend change, so no record or permission can change.

## Not consolidated

Nothing was dropped. The extra tabs listed above are kept as named tabs. Support stays a separate entry.

## Files changed

- `frontend/src/features/partnerpanel/experiences.ts` - tabs, `tabbed()`, tab-aware `pageAllowed`, `tabOf`, `tabbedEntryFor`, the nine affiliate entries, new aliases
- `frontend/src/features/partnerpanel/PanelKit.tsx` - `EntryTabs`; `SubNav` gives way to it and takes a `note`
- `frontend/src/features/partnerpanel/WorkspaceShell.tsx` - draws `EntryTabs` for a tabbed entry's page
- `frontend/src/features/partnerpanel/StoreConsole.tsx` - counts as notes; `WORDS[kind].mine`
- `frontend/src/features/partnerpanel/PartnerStore.tsx`, `workspacePages.tsx` - affiliate wording
- Tests: `experiences.test.ts`, `workspace.test.tsx`, `store-console.test.tsx`
