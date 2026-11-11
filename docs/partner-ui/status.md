# Partner Panel UI/UX redesign (owner's brief of 11 November 2026): status

Branch `feat/partner-admin-panel`, on top of release `2026.11.11-0e58322`. **Not committed, not pushed, not deployed**, as the brief says. Frontend only: no backend change, no migration, no API contract change, no permission change. Design reference: [docs/admin-ui/design-system.md](../admin-ui/design-system.md) (now covers both consoles). Audit tool: [scripts/partner_ui_check](../../scripts/partner_ui_check/README.md).

## Approach

The Partner Panel had its own header, tabs, card and type styles beside the Admin console's design system. Instead of a second system it now uses the Admin's:

- `<html data-console="partner">`, so the console base rules apply: 14px body, 24px titles, 16px section headings, 36px buttons and inputs, 12px card radius, one card shadow.
- The kit `PageHeader`.
- The `ModuleNav` tab styles.
- `StatCard` / `StatGrid` and the kit `Alert`.

Fixes went in at the shared level (PanelKit, WorkspaceShell, PartnerPanel.module.css, Kit), so every page took them at once.

## Audit: before and after (67 pages, four kinds of partner)

Production build, real backend on the scratch database of `scripts/partner_admin_panel_check`.

- **Widths:** every page at 1440, 1024 and 390px; the main pages also at 1920, 1366, 768, 360 and 320px.
- **Partners:** Reseller, Affiliate, White Label, and a draft (KYC not sent).

| Measure | Before | After |
|---|---|---|
| Page title size | 26px (59 pages), 30px (6), 32px (2) | 24px on all 67 |
| Section heading sizes | 16, 18, 22.5px | 16px |
| Button heights | 22, 32, 36, 40, 44, 54px (40px on 48 pages) | 36px, and 32px in tables. The 40px left on 4 page types (the date-range buttons) was fixed after the run. 22/44/54px remain in the Product Store's marketplace layout |
| Input heights | 23, 41, 43, 44px | 36px (one 22px colour swatch) |
| Distinct font sizes | 16 | 10 |
| Page-level sideways scrolling | 17 pages at 320px | none at 360px and wider. 3 draft pages at 320px were fixed after the run and probed clean |
| Pages without exactly one h1 | 0 | 0 |
| axe (WCAG 2.2 AA) | 0 violations | 0 violations |
| Console errors | 0 | 0 |

## Changes by area of the brief

- **§1-2 Design system, colour:**
  - The console base rules apply to the Partner panel, and `PartnerPanel.module.css` uses the type tokens only.
  - Cards use `--w360-radius-card` and `--w360-shadow-card`; weights go up to 700.
  - 11 unused rules were removed.
  - New tokens were only added for the sign-in page earlier the same day.
- **§3 Sidebar:**
  - Eight menus unchanged, with a "Partner Portal" badge.
  - Collapse menu (icon rail, names on hover and focus), and the drawer below 1024px.
- **§4 Header and layout:**
  - **Top bar:** the account's standing, the level (hidden at 1100px and below), Upgrade (icon only at 420px and below), Notifications, and the account button with the name and "partner · role".
  - **Every page:** title, subtitle, actions, tabs.
  - **Titles come from the menu** (`PageTitleContext`): a module's landing tab is titled by the module, the other tabs by their label, and sub-pages keep their own title.
  - **Account notices** are kit `Alert`s.
- **§5 Dashboard:**
  - **Overview:** six StatCards (account status, referral conversions, customer renewals, Platform Wallet balance, pending earnings, available commissions). Each is drawn only when the server returns its figure.
  - **Sections:** Pending actions (real flags only), Quick actions, the trend and product split, Referral performance, Upcoming renewals (`subscriptions?status=expiring`), Recent transactions (the wallet's `recent`), Recent commissions, Recent customers, My earnings.
  - **At the bottom:** the level card, the matrix, upgrade, notifications.
  - A section alone in its row takes the full width.
- **§6 Tables:** the kit table, as before. Right-aligned amounts, entry kinds in words on the dashboard and Commissions, and each table scrolling inside its own region on a phone.
- **§7-8 Forms, modals, buttons:** 36px controls everywhere, dialogs included, because the rules sit on `<html>`. The date-range buttons were moved to the control height.
- **§9 KYC:**
  - `KycProgress` shows the six steps, with each state in words.
  - A correction names the documents to fix ("To correct: …"); a rejection gives the next step.
  - Backend enforcement unchanged.
- **§10 Experiences:** unchanged permission model. Cards and sections are drawn per permission.
- **§11 Wallet:** the Plan Activation confirmation names customer, product, plan, partner purchase price, available wallet balance, amount to deduct and remaining balance. Nothing is charged before Confirm, as before.
- **§12 Earnings:**
  - The Commissions page has its own header and four cards: pending, available, reversals & adjustments, paid out.
  - "Balance" is renamed "Earnings balance", with clear column names.
- **§13 Guest session:** the banner and model are unchanged (ADR-062/063).
- **§14 Responsive:**
  - The matrix wrapper is positioned, so its hidden caption no longer widens a phone page.
  - The top bar sheds parts by width.
  - Alerts wrap their actions.

## Tests

| Suite | Result |
|---|---|
| Frontend vitest, whole suite | **1,620 / 1,620** (92 files), after all changes but the last two CSS-only fixes; the affected suites re-ran after them (221 / 221) |
| Browser: `scripts/partner_admin_panel_check` (production build, KYC required) | **59 / 59** |
| Browser: `scripts/partner_login_check` | **26 / 26** |
| UI audit: `scripts/partner_ui_check` | 67 pages, 0 axe violations, 0 console errors (table above) |
| Backend tests that scan the frontend source (architecture, money handling, routes) | **613 / 613** |
| Static | `tsc`, `eslint` clean on every changed file |

The backend was not changed by this work; its full suite passed 5,428 / 5,428 earlier the same day.

## Remaining

- **Product Store:** keeps its marketplace layout (filter rows, category list), the look approved for the public store.
- **White Label pages:** Branding, URL Manager and White-label setup are shared screens. They follow the console scale but keep their own titles.
- **No pixel-diff baseline:** the comparison is the audit's measurements and screenshots.
- **Notifications:** the bell has no count, because no unread-count API is read by the top bar.
- **Filters** are not remembered between visits.
