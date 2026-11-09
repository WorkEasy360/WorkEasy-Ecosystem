# Admin Panel: repository audit and implementation plan

Phase 1 of the Admin Panel programme (owner's brief of 2026-11-09). Status per
phase lives in [status.md](status.md).

## 1. What the audit found

Stack: Django + PostgreSQL (RLS, PgBouncer) behind Next.js (App Router, CSS
modules, no chart or UI library). One server-side session per portal host. Layered
modules enforced by import-linter (`contracts → platform → integration →
commercial | intelligence → interfaces`). Money is integer minor units per
currency, never converted or summed across currencies. Append-only ledgers
(commissions, audit) already exist and are the model to follow.

### Decisions the owner made on 2026-11-09

| Question | Decision |
|---|---|
| `08_Credits_Wallets_System.txt` and `07_Product_Web_Mobile_Flow.txt` are not on this machine or in git, and no wallet exists in code | Build from the brief's own rules. Every rate stays blank until an Admin enters it, so nothing is charged before then. Whatever those documents would have decided is listed as open in `status.md`. |
| Partners can only be created by an Admin today | Add a public, rate-limited application form on the partner portal. It creates a draft partner with no rights until KYC is approved. |

### Decisions taken as defaults (say so if any is wrong)

- **Existing active partners stay active.** They get KYC status `draft`, and the
  gate applies to any later move to active. Marking them "Approved" would forge a
  decision nobody made, because every decision must record reviewer, date and reason.
- **"Pending Setup" is not a subscription status.** The brief keeps payment,
  subscription, setup and member-access states separate, so it is a filter over a
  new setup state.
- **"Super Admin" becomes "Admin" in the console's words.** The internal role key
  `SUPER_ADMIN` and the permission keys are unchanged.
- **Partner commission views stop being called "Wallet"** (the brief: earnings,
  not a fourth wallet).
- **Existing admin URLs keep working.** Modules group them; sidebar highlighting
  follows a route-to-module map, so emailed links and tests stay valid.
- **No deployment.** Everything is local and on branch `feat/admin-panel` until
  the owner asks.

### Gap matrix

`Exists` = reuse. `Extend` = real backend needing more. `New` = nothing in code.

| Module | Item | State | Notes |
|---|---|---|---|
| Login | Credentials, sessions, reset, lockout, rate limits, logout | Exists | `auth_views.py`, `sessions.py`; 8 h idle, 30 d absolute |
| Login | Remember me | New | Browser-session cookie unless ticked, admin host only |
| Login | Split-screen design | New | `features/auth` |
| Shell | Nine flat items, in-module sub-navigation | Extend | Accordion today; palette walks the sidebar tree |
| Shell | Global search | New | Client palette only; no customers, subscriptions or invoices |
| Shell | Notifications with read state | Extend | `admin/attention` counts; read state is localStorage |
| Dashboard | Six cards with previous-period change | Extend | Month-only; partners and products have no previous figure |
| Dashboard | Date range, monthly trends, Collections vs Sales | New | "Sales" has no amount definition today |
| Dashboard | Pending Actions, Recent Activities, Top Products | New | |
| Customers | Org list, profile, suspend, overrides | Exists | `controlplane/customers.py` |
| Customers | Org member list, create org, assign plan, assignment history | New | |
| Customers | Setup requirements, submissions, corrections | New | |
| Partners | Status, grants, products, links, suspend | Exists | |
| Partners | KYC states, documents, decisions, access audit | New | No document upload of any kind |
| Partners | Public application | New | Owner decision above |
| Partners | Accrual while suspended | Fix | `accrual.py:343` keeps earning on new sales |
| Partners | Commission rules | Extend | Backend exists; `rules` API has no screen |
| Products | Catalog, plans, prices, add-ons | Exists | |
| Products | App links, approved return destinations view | Extend | Destinations are in the integration manifest |
| Products | Included usage, renewal and plan-change rules, partner rates view | Extend | |
| Products | Usage pricing, credit packs | New | `usage` price model is refused by design |
| Billing | Orders, refunds, offline payment, webhooks log | Exists | Per customer only |
| Billing | Global invoices, failed payments, refunds queue | New | |
| Billing | Credit notes | New | |
| Billing | Activation retry, subscription history | New | Clock re-drive exists; no staff action |
| Billing | Commission entries, payouts, withdrawals | Exists | Backend whole; UI partly orphaned |
| Wallets | All of it | New | No balance anywhere; tests ban partner balances |
| Support | Partner tickets | Extend | No assignment, resolution or customer side |
| Support | Customer tickets, help content | New | |
| Reports | All eight | New | No admin report endpoint or server CSV |
| Settings | Sender, templates, gateways | Exists | |
| Settings | Company info, tax, currency, admin notifications | Extend | Tax is env only |
| Settings | Domains & Apps overview | New | Parts exist per partner or product |
| Settings | Staff grants and roles | Exists | No way to create an admin |
| Settings | Sign-in audit, audit filters, login clients view | Extend | |

### Constraints a redesign must respect

- `intelligence` and `commercial` may not import each other; `platform` and
  `integration` may not import `commercial`. So AI, messaging and webhook usage
  reach wallets as **events or a contract call**, never an import.
- `backend/tests/partner_panel/test_architecture.py` bans `PartnerWallet*`,
  balance-named fields and wallet routes. The brief now asks for partner balances
  in the Platform Wallet, so those tests narrow to "commission models never hold
  a balance" rather than being deleted.
- Frontend source is scanned by architecture tests: no `style={{` in `controlplane/*.tsx`,
  no `Math.round` on money, every `/admin/...` literal must be a real page, theme
  token contrast pairs, no partner-type words.
- No SMS or WhatsApp sender exists. Those charges can only come from products
  reporting usage; the platform does not start sending messages in this programme.
- `platform/files` has a scanner that always answers clean and no audited reads.
  KYC documents get their own audited, staff-only read path, and the absence of
  a malware scanner is reported, not hidden.

## 2. Target structure

Nine sidebar items, each opening its first permitted sub-page. Sub-pages are tabs
inside the module (`controlplane/adminModules.ts` is the one list; the sidebar,
tabs, palette, page titles and permissions all derive from it).

| Module | Sub-pages (route) |
|---|---|
| Dashboard | `/admin` |
| Customers | Organizations `/admin/customers`, Users `/admin/users`, Products & Setup `/admin/customers/setup`, Entitlements `/admin/entitlements` |
| Partners | Applications & KYC `/admin/partners/applications`, Approved Partners `/admin/partners/all`, Overview `/admin/partners`, Product requests, Upgrade fees, Commission Rules `/admin/partners/commission-rules` |
| Products & Pricing | Product Catalog `/admin/products`, Store `/admin/store`, Plans `/admin/plans`, Price books `/admin/pricing`, Usage Pricing `/admin/usage-pricing` |
| Billing & Subscriptions | Orders `/admin/orders`, Payments `/admin/payments`, Invoices `/admin/invoices`, Refunds `/admin/refunds`, Subscriptions `/admin/subscriptions`, Earnings `/admin/commissions`, Payouts `/admin/payouts`, Withdrawals `/admin/partners/withdrawals` |
| Wallets & Credits | Platform Wallet `/admin/wallets`, AI Credits `/admin/wallets/ai-credits`, Consumption Wallet `/admin/wallets/consumption`, Transaction Controls `/admin/wallets/transactions` |
| Support | Tickets `/admin/support`, Help Content `/admin/support/help`, Diagnostics `/admin/diagnostics` |
| Reports | `/admin/reports/<kind>` for the eight kinds |
| Settings | Platform `/admin/settings`, Email `/admin/communication`, Domains & Apps `/admin/settings/domains`, Staff access `/admin/security`, Sign-in audit `/admin/security/sign-ins`, Audit log `/admin/audit`, AI platform `/admin/ai` |

## 3. Wallet design (from the brief only)

New module `commercial/wallets`, below `payments` in the commercial layers.

- **`Wallet`**: one row per `(owner, kind)`, kind `platform | ai_credits | consumption`.
  Owner is an organization, or a partner for `platform` only. Commissions never
  touch it.
- **`WalletEntry`**: append-only ledger. Signed amount, `balance_after`, type,
  reason, actor, source reference, `reverses` link, unique idempotency key. The
  wallet's balance column is only ever changed in the same transaction as a new
  entry, under a row lock, and a reconcile check proves the two agree.
- **`WalletEvent`**: every usage event as received, with outcome `charged | failed |
  duplicate`, so failed and duplicate events can be reviewed.
- **`UsageRate`** and **`CreditPack`**: admin-entered, versioned. No rate means no
  charge and the event is recorded as `failed: no_rate`.
- **Insufficient balance refuses the charge.** The brief says nothing of overdraft,
  so none is invented.
- Top-ups and credit packs settle through a new order kind, so they reuse checkout,
  webhooks, invoices and refunds rather than a second payment path.
- Usage reaches wallets through one authenticated product endpoint and an AI usage
  event, keeping the layering rules intact.

Open until the documents arrive: credit expiry, minimum top-up, refund-to-wallet
versus refund-to-source, whether a wallet may pay for a subscription automatically
at renewal, and low-balance thresholds beyond an Admin-entered value.

## 4. Phases

Each phase ends with its tests green and its own commit(s). Order follows the brief,
except that models other phases depend on are built when first needed.

1. Audit and plan (this document).
2. Admin login redesign, remember me, sign-in audit endpoint.
3. Module navigation, shell restyle, responsive layout.
4. Dashboard API and page.
5. Customers and Partners, including KYC and public application.
6. Products & Pricing, including usage rates and credit packs.
7. Billing & Subscriptions.
8. Wallets & Credits.
9. Support and Reports.
10. Settings, access control and security.
11. Integration, the ten workflow tests, responsive checks at six widths, full suites.

Testing never touches production. Backend suite is bare `pytest` (about 5,150 tests,
about 2 h, one shared test database, so never two runs at once).
