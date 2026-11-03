# Unified Partner Dashboard (2026-11-03)

Status: built and verified locally, **uncommitted and not deployed** (branch `fix/ci-run-1`).

Source: the owner's specification *"Workeasy360 – Unified Partner Dashboard: Complete Features & Functional Requirements"* (DOC-ID WE360-PRD-2026-V4.2, 6 pages).

The brief:
- Audit the software against the specification.
- Keep every feature that is already built.
- Build what is missing.
- Redesign the partner section to match the specification.

Decision record: [ADR-057](../architecture/adrs/ADR-057-unified-partner-dashboard.md).

## 1. Audit: the specification against the code

Status key:
- **Built before**: already in the platform before this work; kept unchanged.
- **Built now**: added by this work.
- **Partial**: some of it exists; the gap is stated.
- **Not built**: absent; the reason is stated.

### Section 1: one unified dashboard

| Requirement | Status | Where / why |
|---|---|---|
| One dashboard and codebase for all three levels | **Built now** | Before, three workspace menus (9, 11 and 12 entries) existed over one panel. Now all levels share one 14-entry menu (`partnerpanel/experiences.ts`), computed at runtime from the partner's level and the member's permissions. |
| Upgrade without account migration | Built before | ADR-049: same user, same `PartnerAccount`. |
| The old address of a lower level opens the same page at the partner's level | **Built now** | `WorkspaceShell`: for example, an upgraded affiliate's `/partner/affiliate/...` opens `/partner/reseller/...`. |

### Section 2: levels and set-up fees

| Requirement | Status | Where / why |
|---|---|---|
| Level 1 Affiliate (free), Level 2 Reseller, Level 3 White Label | Built before / **Built now** | The level was implicit in the grants. It is now `capabilities.partner_level()`, returned as `level` by `/partner/portal`, and shown as a badge. |
| Set-up fees of ₹20,000 and ₹1,00,000, one time | Partial | The fee engine exists (Super Admin → Partners → Upgrade Fees; offline or online payment, ADR-049 addendum). **The amounts are not seeded**: Super Admin enters ₹20,000 and ₹1,00,000. The dashboard and upgrade dialog show whatever is set, or "No set-up fee". |

### Section 3: role-based feature access matrix

Every row is enforced by the backend. The dashboard draws the matrix itself (`levels.tsx` `FEATURE_MATRIX`).

| Row | Affiliate / Reseller / White Label | Status |
|---|---|---|
| Dashboard KPIs | Referral metrics / sales and margin / network KPIs | **Built now** (a ribbon per level) |
| Affiliate module | ✓ / ✓ / ✓ | **Partial.** The Affiliate entry is open at every level. A Reseller or White-label partner sees it only when it holds the referral rights: upgraded affiliates keep them (ADR-049), and staff can grant them to any partner. The default bundles are **unchanged**. Adding referral rights to them changes the upgrade and transition rules, which 15 tests pin, so it is an owner decision (section 4). |
| Customer management | Referred only / ✓ / ✓ | Built before. An affiliate sees anonymised leads; the Customers entry is locked for affiliates. |
| Product Store and orders | Browse / wholesale / ✓ | Built before (Store and Store Console, ADR-055/056) |
| Staff | Disabled / up to 5 / unlimited | **Built now**: seat limits enforced on invite (`services.staff_seats`, `W360_PARTNER_STAFF_SEATS`); the Staff entry is locked for affiliates. |
| Helpdesk | Upward tickets / customer desk / customer desk | **Built now** |
| Custom domain and white labelling | ✕ / ✕ / ✓ | Built before, now locked for the lower levels |
| Downline reseller management | ✕ / ✕ / ✓ | Built before (sub-partners). In the Reseller bundle, as before; the specification marks it ✕ for Reseller, and removing it would take away a feature that already exists. A White-label partner gets it when staff grant `subpartner.manage`; the default bundle is unchanged (section 4). |

### Section 4: the 14-entry sidebar

**Built now.** The entries are the same for every level:

1. Dashboard
2. Affiliate
3. Reseller
4. White Label
5. Product Store
6. Customers
7. Sales & Orders
8. Commission & Earnings
9. Wallet & Payouts
10. Communication
11. Staff
12. Helpdesk
13. Reports & Analytics
14. Settings

How entries are drawn:
- **Locked:** an entry the partner's level does not include is drawn locked (a lock icon). Choosing it opens the upgrade dialog, and its pages show an upgrade prompt.
- **Hidden:** an entry the member's role cannot use is hidden, because upgrading would not help.
- **Locked entries by level:**
  - Affiliate: Reseller, White Label, Customers, Sales & Orders, Communication, Staff.
  - Reseller: White Label.
  - White-label: none.

The mapping of every old page to the new entries is in section 3.

### Section 5: analytics and telemetry

| Requirement | Status |
|---|---|
| Total and active customers with period-over-period change | **Built now** (`previous` period, `customers.new`) |
| Product inventory count | **Built now** (`products.active`: products granted plus products approved for promotion) |
| Gross sales and revenue | Built before; change badge **built now** |
| Commission: total, approved and pending | **Built now** (`money.commission`). "Approved" means cleared the holding period. The ledger has no separate approval step. |
| Available balance and withdrawn amount | Built before (balance); **now** on the dashboard with Withdraw |
| Open support tickets | **Built now** (`tickets.open`, `tickets.breached`) |
| Presets: today, yesterday, last 7 days, last 30 days, this month, last month, custom | **Built now**, in the partner's own time zone. 90 days is kept. |
| Sales and commission trend | **Built now** as one money axis with two lines, a hover crosshair and a table view |
| Product-wise sales distribution | **Built now** as a segmented bar with a labelled legend (see ADR-057 for why not a donut). The server folds the tail into "Others" and computes each share (`product_split`), so the browser does no money arithmetic. |
| Recent customers, orders and alerts | Recent customers built before; orders are on Sales & Orders; alerts come from Notifications |

### Section 6: affiliate

| Requirement | Status |
|---|---|
| Link and code generation | Built before. The link format is `/r/<code>`, not `?ref=`. |
| UTM campaign tagging | **Built now**: a UTM builder on Referral Links; `/r/<code>` forwards the five standard `utm_` tags to the page it opens (`lib/utm.ts`). |
| Click logs and conversions | Built before |
| Impressions | Not built: no ad-serving surface exists to count them |
| First-click or last-click, configurable | Partial: first touch is fixed (ADR-054); the window (1-365 days, default 90) is configurable by Super Admin |
| Fixed and percentage commission, recurring | Built before (ADR-056) |
| Marketing assets | Built before (read-only, published by Workeasy360) |
| Commission lifecycle: pending, then approved or reversed | Built before: a 30-day hold by default (`W360_COMMISSION_HOLDBACK_DAYS`; the specification says 14, which is a setting), with reversal on refund. |

### Section 7: reseller

| Requirement | Status |
|---|---|
| Create customer workspaces, assign plans | Built before |
| Wholesale margin | Built before (margin rules, ADR-053) |
| Margin calculator | **Built now** (Reseller → Margin Calculator, using the server's per-seat figures) |
| Subscription and renewal engine | Built before |
| Staff roles: sales, support, finance | Built before (role presets); seat limits **built now** |
| Activate, pause or suspend a customer | Partial: staff only; partners cannot suspend a customer organization (unchanged, deliberate) |

### Section 8: white label

| Requirement | Status |
|---|---|
| Logo and colours | Built before (one logo, six colour tokens) |
| Dark logo, favicon, web manifest | Not built |
| Custom domain with DNS verification and automatic TLS | Built before. Verification is a TXT record, not a CNAME; TLS uses Caddy on-demand. |
| Branded emails, invoices, login | Partial: sender name and reply-to, and a branded sign-in. No branded invoice PDF. |
| Downline affiliates and resellers | Built before (sub-partners, one level deep) |
| Product visibility control | Built before (Product Settings) |

### Section 9: store, orders and invoices

| Requirement | Status |
|---|---|
| Catalogue, order and provisioning | Built before |
| GST-compliant PDF invoice | **Not built**: there is no tax engine and no invoice PDF in the platform |
| UPI, net banking and card gateway | Partial: provider adapters exist; no live provider is configured |
| Wallet deduction | Not built: no prepaid partner credit (registry entry `recharge`) |

### Section 10: commission, wallet and payouts

| Requirement | Status |
|---|---|
| ₹1,000 minimum withdrawal | **Built now** (`W360_PARTNER_MIN_WITHDRAWAL_MINOR`, default 100000, which is ₹1,000) |
| TDS under Section 194H | **Built now**: worked out on INR withdrawals at `W360_PARTNER_TDS_RATE_BP` (default 200 = 2%) and recorded on the request. **Confirm the rate with the company's accountant.** |
| Verified PAN and bank or UPI details | **Built now**: the account number and PAN are encrypted. Verification is done **by staff**, because there is no penny-drop or PAN verification provider. |
| NEFT, RTGS, IMPS and UPI payout | Partial: the platform records where to pay and staff confirm "paid" with a reference. No money is moved automatically. |
| Ledger line with Order ID, customer, gross, TDS and net | Partial: the ledger keeps the order, product, gross basis and net commission. TDS is recorded on the withdrawal request, not on ledger rows. |

### Section 11: communication, helpdesk and upgrades

| Requirement | Status |
|---|---|
| Two-way helpdesk | **Built now**: tickets to Workeasy360 for every level, and a customer desk for Resellers and White-label partners. |
| SLA tracking | **Built now**: deadlines for first answer and resolution by priority; states on track, due soon, overdue and met. |
| Canned responses | **Built now** (Saved Replies) |
| Customer side | **Built now**: Support in the organization console, "Ask &lt;partner&gt;" |
| Staff side | **Built now**: Super Admin → Partners → Partner Tickets |
| Persistent "Upgrade Plan" header action | **Built now**: the top bar shows the level badge and Upgrade Plan; the dialog lists the levels, fees and matrix. |
| Broadcast by email, WhatsApp or SMS | Not built: there is no WhatsApp or SMS provider. Email is the platform's own mail, with the partner's sender name. |
| Branded email templates | Partial (registry `partner_templates`) |

## 2. What was built

### Backend

**One migration: `partners.0016`.** It adds:
- `PartnerTicket`
- `PartnerTicketMessage`
- `PartnerCannedResponse`
- `PartnerPayoutMethod`
- `PartnerWithdrawalRequest`

**Modules**
- `commercial/partners/tickets.py`: the helpdesk (partner, customer and staff sides; SLA; saved replies).
- `commercial/controlplane/partner_withdrawals.py`: payout details (sealed), withdrawals, TDS, and the staff queue. "Start payout" calls the existing `payouts.prepare_payout`.
- `partner_portal.dashboard`:
  - calendar presets in the partner's time zone;
  - the `previous` period;
  - `products.active` and the product-wise sales split;
  - `money.commission` (total, approved, pending, available);
  - `tickets`.
- Reports gained `sales.by_product`.
- `capabilities.partner_level()`; `level` in the portal answer.
- `services.staff_seats()` and the seat check on member invite and add. Staff are not limited.

**Permissions**
- `platform.partner_payout.request` (dangerous; owners and administrators).
- `platform.partner_desk.view` and `platform.partner_desk.manage`, under the `support.access` capability.

**Settings**
- `W360_PARTNER_MIN_WITHDRAWAL_MINOR`
- `W360_PARTNER_TDS_RATE_BP`
- `W360_PARTNER_STAFF_SEATS_*`
- `W360_PARTNER_PAYOUT_SEAL_KEYS`: optional in production. Without it, saving payout details is refused; nothing else changes.
- Rate limits `partner_ticket_*` and `partner_payout_*`.

**APIs**
- Partner:
  - `partner/<id>/tickets[/<id>]`
  - `canned-responses[/<id>]`
  - `withdrawals` (the wallet), `withdrawals/method`
  - `withdrawals/request`, `withdrawals/<id>/cancel`
- Staff:
  - `admin/partner-tickets[/<id>]`
  - `admin/partner-withdrawals[/<id>]`
  - `admin/partners/<id>/payout-method`
- Organization: `orgs/<slug>/provider-tickets[/<id>]`

**Registry and tenancy**
- The `partner_tickets` entry was removed from `NOT_IMPLEMENTED`, since tickets are now built.
- `partners.PartnerTicket` was added to `TENANT_SCOPE_EXEMPT`, with its reasoning (the same class as the ledger and partner links).

### Frontend

- `experiences.ts`: one 14-entry menu with levels and locks.
- `WorkspaceShell`:
  - locked entries open the upgrade dialog;
  - the level badge and Upgrade Plan in the top bar;
  - the redirect to the partner's level;
  - the locked-page prompt.
- `SidebarShell`: `locked` and `onSelect` on an entry.
- `levels.tsx`: the level badge, the upgrade dialog, the level list and the feature matrix.
- `UnifiedDashboard.tsx`: the dashboard of figure 1.0, used for all three levels.
- `Wallet.tsx`, `Helpdesk.tsx` (My Tickets, Customer Desk, Saved Replies, Disputes), `MarginCalculator.tsx`.
- Reports: product-wise revenue, CSV download, Print / Save as PDF.
- UTM builder on Referral Links; `lib/utm.ts`.
- Organization console Support: `ProviderSupport.tsx`.
- Super Admin: `PartnerDesk.tsx` (Partner Tickets, Withdrawals), and two Partners menu items.

## 3. Old pages and the new entries

Every page keeps its address. Each address of the older menus is either a page of one of the 14 entries or an alias that opens one, as the tests check.

| Entry | Pages (tabs) |
|---|---|
| Dashboard | `dashboard`; `upgrade/*` |
| Affiliate | `referral-links`, `referral-codes`, `campaigns`, `leads`, `conversions`, `affiliates` (Link Overview), `marketing-materials`, `affiliates/settings`. Higher levels also get `promote/store*`. |
| Reseller | `store-console` (and `partner-products`), `store-console/explore`, `store-console/requests`, `product-manager`, `plans` (and the `pricing/*` pages), `margin-calculator`, `agency` |
| White Label | `brand-settings` (and `appearance`), `brand-settings/white-label-setup`, `brand-settings/url-manager`, `translations` |
| Product Store | `store`. For an affiliate, also the promote console tabs. |
| Customers | `users`, `users/subscriptions` |
| Sales & Orders | `payments`, `product-billing` |
| Commission & Earnings | `payments/commissions`, `payments/balance`, `disputes` |
| Wallet & Payouts | `wallet`, `payouts` (and `affiliates/payouts`) |
| Communication | `communication/channel` (and `alerts`, `logs`), `communication/templates`, `channel-manager` |
| Staff | `team`, `roles` |
| Helpdesk | `support-manager` (My Tickets), `desk`, `desk/replies`, `notifications` |
| Reports & Analytics | `reports` |
| Settings | `account`, `integrations`, `api-keys`, `auth-configuration`, `shared-resources` |

## 4. Before it goes live: owner steps

1. **Approve migrating the dev database** to `partners.0016`, which adds 5 new tables and changes no existing data. It has not been run.
2. **Set the fees**: Super Admin → Partners → Upgrade Fees: Reseller ₹20,000; White-label ₹1,00,000.
3. **Production**: set `W360_PARTNER_PAYOUT_SEAL_KEYS` to a new random key of at least 32 characters, different from every other key. Until it is set, partners cannot save bank or PAN details.
4. **Confirm the TDS rate** (`W360_PARTNER_TDS_RATE_BP`, 2% by default) and the minimum withdrawal with the company's accountant.
5. **Affiliate module for Resellers and White-label partners, and downline for White-label** (specification section 3): decide whether every new partner should get these by default.
   - Today staff grant `referral.create` and `referral.view` (and `subpartner.manage` for White-label) on the partner's page, and the Affiliate entry appears.
   - Making them part of the default bundles changes the upgrade and transition rules (ADR-047/049), so it needs its own change and tests.

## 5. Verification (2026-11-03)

- **Backend:**
  - The new suite `tests/partner_dashboard` (33 tests): ranges, dashboard sections, product split, tickets and their isolation, wallet and withdrawals, sealing, seats.
  - The affected suites were re-run after the fixes.
  - The full bare `pytest` run: **5,089 passed**, 0 failed (1 h 14 min).
- **Frontend:** vitest 1,109/1,109. `eslint src` is clean. `tsc` reports only the 8 old errors in the stale `.next/types/validator.ts`. The production `next build` (scratch copy) succeeds.
- **Browser** (production build, headless Chrome, mocked API answers): 24/24.
  - 14 entries at each level, and the right locks.
  - The "Partner Dashboard" heading and the level badge.
  - No overflow at 1440px or 360px.
  - axe finds nothing on the three dashboards or the upgrade dialog.
  - A locked entry opens "Unlock …"; a locked page shows its prompt; the dialog shows both fees; the product split shows the server's shares and "Others".

## 6. Not built, and why

- **GST invoice PDF:** there is no tax engine.
- **Automatic NEFT/UPI payouts, penny-drop and PAN verification:** these need a payment or KYC provider.
- **WhatsApp and SMS broadcast:** there is no provider.
- **Impressions:** there is no ad surface to count them on.
- **Last-click attribution:** first touch is fixed by ADR-054.
- **Dark logo, favicon and web manifest uploads.**
- **Partner-initiated customer suspension:** staff only, by design.
