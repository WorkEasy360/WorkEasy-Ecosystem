# Console design system: Admin and Partner (2026-11-07; shell 2026-11-09; Partner panel 2026-11-11)

The Admin console (`admin.workeasy360.com`) and, since 11 November 2026, the Partner panel (`partner.workeasy360.com`) are built from one set of shared components, so buttons, tabs, tables, badges, filters, KPI cards and empty states look and behave the same on every page. This document is the reference for anyone adding or changing a console page.

Code: `frontend/src/features/controlplane/kit/` (import from `./kit`). Tests: `kit/kit.test.tsx`.

## Where the rules apply

`AdminShell` sets `<html data-console="admin">` and the Partner workspace shell (`partnerpanel/WorkspaceShell.tsx`) sets `<html data-console="partner">`. The console base rules in `globals.css` (`:root[data-console] …`) hang off that attribute:

- body text at 14px;
- headings on the type scale;
- `.w360-btn` and `.w360-input` at 36px;
- card radius and shadow;
- `scrollbar-gutter: stable`.

Because the attribute is on `<html>`, the rules reach dialogs drawn on `<body>` too. They never apply to the customer portal, the sign-in pages or the public site. The kit components look the same wherever they are used.

**In the Partner panel** (2026-11-11 brief, "Professional Partner Panel UI/UX"):

- `PanelKit.PageHeader` draws the kit `PageHeader` (title, one-line subtitle, actions, browser title "… · WorkEasy360 Partner").
- A workspace entry's tabs (`EntryTabs`, "Sections") and a page's own tabs (`SubNav`, "Pages") use the Admin's `ModuleNav` styles: the pill row with the open section filled, then the underlined pages row.
- `PartnerPanel.module.css` uses the type tokens only (12 / 14 / 16 / 20 / 24 / 28px), `--w360-radius-card` and `--w360-shadow-card` for cards, weight 700 at most.
- The Dashboard opens with `StatGrid` / `StatCard` (six overview cards, each drawn only when the partner may see its figure); Commissions opens with the four earnings cards.
- The top bar: the account's standing (Active / KYC pending / KYC required / Suspended / Ended), the level and Upgrade, Notifications, and the account button with the person's name and the partner.
- Account notices (draft, suspended, KYC required) are kit `Alert`s.
- Onboarding progress is `BusinessKyc.KycProgress`: six steps, each state in words.
- The partner Store keeps the storefront layout with its header on the console scale (`StoreBrowser.module.css`, `:root[data-console="partner"]`).
- The audit that measures all of this: `scripts/partner_ui_check/audit.js` (and `summarize.js` to compare two runs).

## Tokens (globals.css `:root`)

| What | Tokens | Values |
|---|---|---|
| Type | `--w360-fs-meta / body / section / subhead / title / kpi` | 12 / 14 / 16 / 20 / 24 / 28px |
| Page title | `h1` in the console | 24px, semibold (600) |
| Section title | `h2` in the console | 16px, semibold |
| Radius | `--w360-radius-control / card / pill` | 6px controls, 12px cards and dialogs, 999px badges and avatars only |
| Control height | `--w360-control-h / -h-sm` | 36px; 32px in tables and toolbars |
| Shadows | `--w360-shadow-card`, `--w360-shadow-popover` | one card shadow, one popover shadow |
| Neutrals | `--w360-gray-50 … 900` | borders, fills, muted text |
| Spacing | `--w360-space-1 … 7` | 4px grid (4, 8, 12, 16, 24, 32, 48) |

Colours follow `docs/brand/partner-brand-alignment.md`:

- The primary button is `#02C065` with navy text (7.1:1). White on it is 2.4:1, so never use white.
- Green text is `#047A44`.
- Semantic colours come in bg + text pairs: success, warning, danger, info and neutral.
- `theme-tokens.test.ts` checks every pair against WCAG AA.
- CSS modules use tokens only. A hex or `rgba()` fails the colour test.

## Components

| Component | Use it for | Notes |
|---|---|---|
| `Page` | every page's outer container | max 1440px, same gap everywhere |
| `PageHeader` | the top of every page | breadcrumb (detail pages), title, one-line subtitle, `info` (i) for longer copy, `primary` action on the right, `secondary` actions in a "More actions" menu, `tabs` below; sets the browser title |
| `Button`, `ButtonLink`, `IconButton` | every action | `primary / secondary / ghost / danger / link`, `sm / md`, `icon` (never a "+" character), `loading`; labels never wrap |
| `Tabs` (state) / `LinkTabs` (pages) | sections of a page | one underline style |
| `StatusChips` | a list's status filter | "All 9 · Subscribed 2 · Trial 0"; scrolls sideways when narrow |
| `FilterBar`, `SearchInput`, `Select` | the row above a table | search (icon, clear, 300 ms debounce, no Search button), then filters, "Clear filters", Export/Columns on the right; `Select` is the styled select (never full width in a filter row) |
| `DataTable` | every list | sentence-case headings, sorting, sticky header and first column, row hover, row click to detail, one "⋯" `rowMenu`, bulk select, pagination with "Showing 1–25 of 132", column picker, skeleton, empty state, hides columns empty in every row |
| `PersonCell`, `IdCell`, `UrlCell`, `Trunc`, `Stack` | table cells | one line, ellipsis and tooltip; IDs in mono with a copy button |
| `StatusBadge`, `BoolBadge`, `Badge` | status and yes/no facts | one status-to-colour map (`kit/Badge.tsx`), Title Case; booleans as "Verified / Unverified", never "Yes / No" |
| `StatCard`, `StatGrid` | KPIs | icon, label, value, delta (0% is grey); the grid sizes to the number of cards, so no orphan |
| `EmptyState` | anything empty | icon, title, one-line hint, CTA |
| `Card`, `SectionHeader`, `DescriptionList`, `Alert` | page structure | the section's action on the right; label/value grid; banners |
| `ConfirmDialog` / `useConfirm` | every destructive action | optional reason sent to the audit log; `typeToConfirm` for Terminate or Remove Super Admin |
| `ToastProvider` / `useToast` | results of an action | provided by `AdminShell` |
| `Tooltip`, `InfoTip`, `Skeleton`, `LoadingBlock`, `Menu`, `RowMenu`, `CopyButton` | supporting pieces | keyboard-operable, Escape closes |

The older helpers in `ui.tsx` now draw with the kit, so pages not rebuilt yet still match:

- `Table` renders in the kit's table card, with the caption read by screen readers only.
- `Status` renders a `StatusBadge`.
- `when` writes dates through `formatDateTime`.
- `LoadGate` shows a skeleton or an `EmptyState`.
- `ReasonAction` with `danger` opens a `ConfirmDialog`.

## Writing values (`kit/format.ts`)

| Value | Written as | Helper |
|---|---|---|
| Date | 24 Oct 2026 | `formatDate` |
| Date and time | 24 Oct 2026, 6:45 AM | `formatDateTime`; tooltip with `exactTime` |
| Last sign-in or activity | 5 min ago, Yesterday, 4 days ago | `formatRelative` |
| Money | ₹1,250.00 | `formatMoney(minor, currency)` |
| Empty | — | `EMPTY`, `orEmpty` (folds "None", "None recorded" and "Not configured") |
| System key | Invoice issued, Staff signed in, Long context | `eventLabel`, `sentence`; keep the raw key only as small mono text or a tooltip (`KeyText`) |
| Product label | Easy CRM (not "Easy CRM (easy_crm)") | `productName` |

## App shell

Changed by the Admin Panel programme (2026-11-09). `adminModules.ts` is the single source for the sidebar, the rows inside a module and the search palette. The UI says "Admin"; the internal key stays `SUPER_ADMIN`.

- **Sidebar.** Exactly nine main menus: Dashboard, Customers, Partners, Products & Pricing, Billing & Subscriptions, Wallets & Credits, Support, Reports, Settings. A module is drawn only if the person may see something in it.
  - "Collapse menu" folds the sidebar to a 64px icon rail. Names appear on hover and focus. The choice is remembered in `localStorage` under `w360.admin.rail`.
  - Below 1024px the sidebar becomes a drawer behind the Menu button.
- **Inside a module.** `ModuleNav` draws the module's sections, then the current section's pages, above the page. A row with one entry is not drawn. Rows wrap on a narrow screen, so no entry is cut off at the edge.
- **Names.** A page's menu label is also its title, its breadcrumb and its browser tab: "Users · WorkEasy360 Admin". Titles live in `adminNav.ts` `ADMIN_PAGE_TITLES`. A record's own page sets its name through `PageHeader`.
- **Top bar.**
  - **Search** (the box, or Ctrl/⌘K). It finds pages by name and asks the server for customers, organizations, partners, products, subscriptions, orders and invoices the person may see.
  - **Create menu.** New customer, New partner, New product, New plan, New credit pack, New help article, New Admin, each shown only with the permission behind it.
  - **Notifications.** A count of what is really waiting. Which ones are read is remembered per browser.
  - **Help and settings.** Help opens the help content; the gear opens Settings.
  - **Account menu.** Shows the avatar, full name and role. The role is worked out from the person's grants.
- **Responsive.** Header actions, tab rows and filter chips wrap on a phone. Only tables and charts scroll sideways, each inside its own focusable region, and a chart opens on its latest month. The page itself never scrolls sideways at 1920, 1366, 1024, 768, 390 or 360px.

## Adding a page

1. Wrap the page in `Page` and open it with `PageHeader`. The title must match its menu label.
2. Put lists in `DataTable`, behind a `FilterBar` (with `StatusChips` when the list has statuses).
3. Put sections in `Card` with a title and actions.
4. Run destructive actions through `ConfirmDialog` (`ReasonAction danger` does this for you).
5. Write every value through `kit/format.ts`.
6. Don't use a `style={{}}` attribute in controlplane `.tsx` files. Set values through the CSSOM with a ref instead.
