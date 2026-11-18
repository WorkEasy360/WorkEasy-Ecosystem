# Admin UX simplification — status

The owner's brief of 2026-11-18 asks for a simpler Super Admin console (`/admin`) in five phases. It is a frontend change only: no backend, database, API or RBAC changes. No feature is removed, every old address keeps working, and every page and action keeps its permission key.

| Phase | State |
|---|---|
| Step 0 — discovery | Reported 2026-11-18. The owner accepted the recommended answers to the 7 decisions. |
| Phase 1 — flatten navigation | **Done 2026-11-18** (this commit). |
| Phases 2–5 | Not started. Each waits for the owner's approval of the previous phase. |

## Phase 1 — what changed

**Navigation shape.** There are at most two levels: a sidebar entry, then ONE row of tabs.
- The map is still the one list in [adminModules.ts](../../frontend/src/features/controlplane/adminModules.ts). A *section* is now a tab.
- A tab's first page is the tab itself. Its other pages open from buttons in that page's header, with a breadcrumb back (decision 2):
  - Applications › KYC requirements
  - Commissions › Commission entries
  - Payouts › Withdrawals
  - Requests › Upgrade fees
  - Plans › Currency & pricing
  - Usage pricing › Credit packs
  - Store › Customer preview, Commercial rules, Coupons
- Settings draws its sections as a menu down the left (`layout: "menu"`), with Security's four pages under one heading.
- A record's own page draws no tab row.
- Breadcrumbs come from the shell: `PagePlaceContext` and `usePageTrail` in `kit/Layout.tsx`.

**The sidebar's nine entries and their tabs:**

| Entry | Tabs |
|---|---|
| Dashboard | — |
| Customers | Organizations, Users, Product setup, Product access |
| Partners | Overview, Partners, Applications, Commissions, Payouts, Requests |
| Products & Pricing | Products, Plans, Usage pricing, Store |
| Billing | Needs attention (with count), Orders, Subscriptions, Invoices, Payments, Refunds, Credit notes |
| Wallets & Credits | Platform wallet, Consumption wallet, AI credits, Controls |
| Support | Tickets, Diagnostics, Help articles |
| Reports | One page with a grouped selector |
| Settings | Left menu: Platform settings, Email templates, AI platform, Domains & apps, Team & roles; then under Security: Connected apps, Sign-in audit, Audit log, KYC document access |

**Changes inside the pages:**
- **Partners › Partners:** the partner type is a row of chips (`?type=affiliate|reseller|white_label`). The Affiliate / Reseller / White Label sidebar submenu, and its per-browser reorder, are gone.
- **Support › Tickets:** "Who asked" is kept in the address (`?audience=`). A partners-only view shows the SLA column the old Partner tickets list had. Staff with `partner.view` but not `support.view` get the partner desk's list under Tickets, with Who asked fixed at Partners (decision 4).
- **Reports:** `/admin/reports?report=<kind>`. The selector is the kit Select with option groups: Money, Partners, Usage.
- **Team & roles:** Roles and Individual permissions are two sections of one page; the inner tabs are gone.

**Top bar:**
- The settings gear is gone.
- "?" opens a right-side help drawer: published articles, read-only and searchable.
  - Staff with `support.view` read the library's published articles (`?status=published`).
  - Everyone else reads `/api/v1/public/help` (decision 6).
- Ctrl+K lists each page with its place ("Partners › Payouts"), plus shortcuts: the three partner types, Partner tickets and the eight reports.
- The Create menu is unchanged: every address it opens still works in its new home.

**Billing count:**
- Needs attention shows the bell's `failed_payments` number, which counts failed invoices, stuck activations and refunds waiting. It is not the table's row count.
- The bell publishes what it reads (`attentionStore.ts`), so there is no extra request.

## Old → new addresses

Pages that only moved keep their address (decision 1). These redirect, as server `page.tsx` files; `admin-redirects.test.ts` visits each one:

| Old | New |
|---|---|
| `/admin/partners/affiliate`, `/reseller`, `/white-label` | `/admin/partners/all?type=affiliate\|reseller\|white_label`. Keeps `q`, `status`, `profile`, `kyc`, and `guest` = ended/expired/denied. |
| `/admin/partners/affiliates`, `/resellers` | Same as the row above, in one hop. |
| `/admin/partners/tickets` | `/admin/tickets?audience=partner` |
| `/admin/reports/<kind>` (8) | `/admin/reports?report=<kind>` |
| `/admin/partners/resellers/new`, `/white-label/new`, `/white-label/:id`, `/commissions`, `/payouts`, `/products`, `/customers` | Unchanged older redirects. Those that opened a type page now open the chip. |

The old type pages stay the paths a guest session may return to, because the server's `_RETURN_RE` accepts only those four paths, with no query. The list's tab memory (`lastPartnerList`) now carries `type`.

## Permissions

Every page keeps its own key:
- Commission entries and Payouts: `commission.view`.
- Withdrawals: `payout.manage`.
- Tickets: `support.view` **or** `partner.view`. Each grant reads its own endpoint, as before.

A sidebar entry shows when any of its tabs is open to the staff member. Example: Billing Admin sees Partners with Commissions and Payouts only.

## Not done, and why

- **Dashboard breadcrumb:** none. It would only repeat "Dashboard".
- **Page headings:** they keep their current wording ("Failed & stuck", "Staff access"). Copy is Phase 4.
