# Building an Admin console screen

Read `docs/admin-ui/design-system.md` first. This page adds what the 2026-11-09 Admin
Panel programme needs. Follow it so every screen behaves the same.

## Where things go

| What | Where |
|---|---|
| A route | `frontend/src/app/admin/<path>/page.tsx`: a thin client page rendering one screen |
| A screen | `frontend/src/features/controlplane/<Name>.tsx`, CSS in `<Name>.module.css` |
| Tests | `frontend/src/features/controlplane/<name>.test.tsx` |
| API calls | `frontend/src/lib/api/adminPanel.ts`: the typed client (already written) |
| Navigation | `adminModules.ts` already lists every page. Do not add a nav entry |

A route file looks like `frontend/src/app/admin/orders/page.tsx`. Use `Suspense` only when
the screen reads `useSearchParams`.

## The building blocks

Import from `./kit`: `Page`, `PageHeader`, `DataTable`, `StatusChips`, `FilterBar`,
`SearchInput`, `Select`, `Card`, `EmptyState`, `StatusBadge`, `Badge`, `Button`,
`ButtonLink`, `useConfirm`, `useToast`, `formatMoney`, `formatNumber`, `formatDate`,
`formatDateTime`, `DescriptionList`, `Alert`, `Stack`, `Trunc`, `IdCell`, `StatCard`,
`StatGrid`. Forms go in `FormPopup` or `FormDialog` from `./Modal`, with `dialog.grid`.
`TextField` is in `./widgets`. Loading goes through `useLoad` + `LoadGate` or `usePaged`
from `./ui`. A page and its list both have an error state and an empty state. `can(permissions, key)`
and `useAdmin()` are in `./ControlShell`.

`frontend/src/features/controlplane/AdminCommerce.tsx` (`StaffOrdersScreen`) is the pattern
for a list page, including `pagedProps`, `Column<Row>` and `DataTable`. `AdminDashboard.tsx`
is the newest full screen.

## Rules that are tested

- **No `style={{...}}`** in any `features/controlplane/*.tsx`; layout goes in CSS modules. No
  hex colours in CSS: tokens from `globals.css` only.
- **No `Math.round`, `Math.floor` or `Math.ceil`** on a line mentioning price, amount, minor,
  commission or rate. Use `toMinor` and `toBasisPoints` from `./amounts` to turn typed money
  into integers, `formatMoney` to show it.
- **Every `/admin/...` string must be a real page** (a test scans the source).
- **No `dangerouslySetInnerHTML`**. Help content and ticket text are plain text.
- **No partner-type wording in code or comments**: never `partner_type`, `reseller_type` and
  similar. Capabilities and "experience" are the words.
- **No product names or codes** in `features/controlplane/productmanager/`.
- The page's title is the menu label in `adminModules.ts`.
- One `h1` per page, from `PageHeader`.

## Behaviour every screen must have

- Loading, empty, and error states, each saying something useful.
- Filters and pagination where the API offers them. Never pretend to filter rows the API
  did not send.
- Every button does what it says. Nothing decorative, no dead links, no fake success.
- A form validates before sending, shows the server's error in the form, cannot be
  double-submitted, says when it worked, and refreshes what it changed.
- Hide or disable an action the staff member's grants do not allow, and keep the server's
  403 handled, because the server decides.
- A destructive action confirms first with `useConfirm`.
- Commands that move money send a fresh `crypto.randomUUID()` as the command id, created when
  the dialog opens, so a double-click or a retry is one movement.
- Money is integer minor units per currency. Never convert, never add across currencies.
- AI credits are whole numbers of credits, never money.

## Responsive

The console must work at 1920, 1366, 1024, 768, 390 and 360 px with no horizontal page
overflow. `DataTable` scrolls inside itself. Use `minmax(0, 1fr)` in grids, wrap filter bars,
and let dialogs fit the screen.

## Testing

`npx vitest run src/features/controlplane/<name>.test.tsx`, `npx tsc --noEmit -p .`
(ignore errors that start `.next/`), `npx eslint <your files>`. Use `mockApi`, `ok`,
`expectNoViolations` and `AdminContextForTest` from `./testSupport`. Mock `next/navigation`
like `admin-dashboard.test.tsx`. Each screen needs at least: it renders real rows, its empty
state, its error state, a successful action with the exact request body, a refused action
showing the server's message, a permission-hidden action, and axe.
