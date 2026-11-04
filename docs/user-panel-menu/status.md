# User Panel menu consolidation (2026-11-03): 22 entries become 13

Status: built and verified locally, **uncommitted, not deployed** (branch `fix/ci-run-1`).

This change touches navigation and presentation only. It changes no API, model, migration, permission, entitlement, subscription, payment, order, invitation, authentication, sign-in policy, network rule or audit code. Every organization-console page (`/o/<slug>/...`) keeps its address, its component and its permission. The tab look matches the partner workspaces ([../affiliate-menu/status.md](../affiliate-menu/status.md), [../reseller-menu/status.md](../reseller-menu/status.md), [../white-label-menu/status.md](../white-label-menu/status.md)).

## Initial audit: the User Panel sidebar in the code

The whole sidebar was one function, `organizationNav` in `frontend/src/features/launcher/Shell.tsx`. It held **22** entries, each linking to its own file route under `frontend/src/app/o/[orgSlug]/`. A member saw an entry only if they held its permission; the backend authorizes every page's requests on its own.

| # | Entry | Address (`/o/<slug>/...`) | Shown when |
|---|---|---|---|
| 1 | Dashboard | `dashboard` | always |
| 2 | My products | `apps` | always |
| 3 | Store | `store` (+ `store/<product>`) | always |
| 4 | Explore products | `explore` | always |
| 5 | Subscriptions & billing | `settings/billing` | `platform.billing.view` or `commercial.subscription.view` |
| 6 | Orders | `orders` | same as 5 |
| 7 | Organization | `settings/organization` | always |
| 8 | Members | `settings/members` | `platform.member.view` |
| 9 | Invitations | `settings/invitations` | `platform.member.invite` |
| 10 | Profile | `settings/profile` | always |
| 11 | Product access | `settings/products` | `platform.seat.assign` |
| 12 | Integrations | `settings/integrations` (+ `settings/webhooks`, linked from it) | `platform.connection.view` |
| 13 | Automations | `settings/automations` | `platform.automation.view` |
| 14 | AI | `settings/ai` | `platform.ai.view` |
| 15 | Analytics | `settings/analytics` | `platform.analytics.view` |
| 16 | Authentication | `settings/security/authentication` | `platform.security.view` |
| 17 | Network access | `settings/security/network` | `platform.security.view` |
| 18 | My security | `settings/security` (exact) | always |
| 19 | Sign-in preferences | `settings/sign-in-preferences` | always |
| 20 | Audit log | `settings/audit` | `platform.audit.view` |
| 21 | Sign-in activity | `settings/sign-ins` | `platform.audit.view` |
| 22 | Support | `help` | always |

Pages with no sidebar entry, unchanged: `/o/<slug>` (the front door: it acts on a sign-up intent, then opens the dashboard), `billing` (a short address that forwards to `settings/billing`), `checkout` (the purchase flow) and `settings/webhooks` (the delivery log).

The account menu (Profile, My security, Sign-in preferences, Switch organization, Sign out) is a second way to reach three of these pages. It is kept.

## Final sidebar

| Group | Entry | Tabs |
|---|---|---|
| Menu | Dashboard | - |
| | **Product Hub** | My Products · Store · Explore Products |
| | **Billing & Orders** | Subscriptions & Billing · Orders |
| Organization & team | **Organization & Team** | Organization · Members · Invitations |
| | Profile | - |
| App management | Product Access | - |
| | **Integrations & Automation** | Integrations · Automations |
| | AI | - |
| | Analytics | - |
| Security | **Login & Authentication** | Authentication · Sign-in Preferences |
| | **Security Center** | Network Access · My Security |
| Audit | **Activity & Audit** | Audit Log · Sign-in Activity |
| Help | Support | - |

## Old-to-new mapping

| # | Original entry | New entry › tab | Address (unchanged) |
|---|---|---|---|
| 1 | Dashboard | Dashboard | `dashboard` |
| 2 | My products | Product Hub › My Products | `apps` |
| 3 | Store | Product Hub › Store | `store`, `store/<product>` |
| 4 | Explore products | Product Hub › Explore Products | `explore` |
| 5 | Subscriptions & billing | Billing & Orders › Subscriptions & Billing | `settings/billing` |
| 6 | Orders | Billing & Orders › Orders | `orders` |
| 7 | Organization | Organization & Team › Organization | `settings/organization` |
| 8 | Members | Organization & Team › Members | `settings/members` |
| 9 | Invitations | Organization & Team › Invitations | `settings/invitations` |
| 10 | Profile | Profile | `settings/profile` |
| 11 | Product access | Product Access | `settings/products` |
| 12 | Integrations | Integrations & Automation › Integrations | `settings/integrations`, `settings/webhooks` |
| 13 | Automations | Integrations & Automation › Automations | `settings/automations` |
| 14 | AI | AI | `settings/ai` |
| 15 | Analytics | Analytics | `settings/analytics` |
| 16 | Authentication | Login & Authentication › Authentication | `settings/security/authentication` |
| 17 | Sign-in preferences | Login & Authentication › Sign-in Preferences | `settings/sign-in-preferences` |
| 18 | Network access | Security Center › Network Access | `settings/security/network` |
| 19 | My security | Security Center › My Security | `settings/security` |
| 20 | Audit log | Activity & Audit › Audit Log | `settings/audit` |
| 21 | Sign-in activity | Activity & Audit › Sign-in Activity | `settings/sign-ins` |
| 22 | Support | Support | `help` |

**Old addresses and bookmarks.** No address changed and no route was added or removed. An old link opens the same page, with its parent entry highlighted and the parent's tabs above it. `billing` still forwards to `settings/billing`, and `checkout` is still a page no entry owns: nothing is highlighted and no tabs are drawn there, as before.

## How it works

- `frontend/src/features/launcher/orgNav.ts` (new) holds the 13 entries.
  - Each entry is a list of tabs. Each tab is one page with the permission its old sidebar link had.
  - `organizationNav` builds the sidebar. Each entry opens the first page the member may open: for example, Security Center opens Network Access for a security administrator and My Security for everyone else.
  - An entry with no page the member may open is not shown, as before.
- `organizationTabs` gives the tabs above the open page: the owning entry's tabs that the member may open, when there are two or more. With only one, no tab strip is drawn (for example, a plain member on Organization).
- **Matching is exact.** `settings/security` (My Security) is no longer current on `settings/security/network`. Only `store/<product>` (under Store) and `settings/webhooks` (under Integrations) are matched beyond their own address.
- `Shell.tsx` draws the tab strip (`nav aria-label="Sections"`, the current tab with `aria-current="page"`) above the page. It passes the pathname to the sidebar. The account menu labels now match the tabs ("My Security", "Sign-in Preferences").
- `SidebarShell` gained an optional `current` on an entry: the caller decides whether the entry is current. Only the organization console uses it. The other consoles behave as before.
- `Console.module.css` gained `.tabs`, which uses the same rules as the partner workspaces' tabs.
- **Duplicate links removed.** The Members page header linked to Invitations, and the Invitations page header linked back to Members. Each link had the same permission as the matching tab, so both were removed (along with the now-unused `Link` imports). Contextual links inside page text are kept, for example "Subscription & billing" on the Dashboard and "Password and sessions" on Profile.
- **Code removed.** The 22-entry `organizationNav` in `Shell.tsx` (replaced) and the `SidebarGroup` import it needed.

**Security boundaries.** The sidebar and tabs only decide what to draw. Every page, guard and API call is unchanged:
- Organization membership, the org access policy (`PolicyGate`) and the read-only banner are still checked in the shell before any page renders.
- RBAC and tenant isolation are enforced by the backend on every request.
- A deep link to a page the member's permissions do not open still loads that page. The page's own API calls are refused as before. Nothing in the sidebar is highlighted, also as before.

## Verification

- **Frontend: `vitest` 1,087 / 1,087.** New tests:
  - `orgNav.test.ts` (29 tests): each of the 22 addresses has exactly one current entry and the expected tab, with no double highlight. All 22 pages are reachable from the sidebar or a tab. `store/<product>` and `settings/webhooks` stay under their tabs, and checkout is unowned. A plain member's 7 entries and landing pages. Each tab carries its page's old permission. Nothing is highlighted on a page the member cannot open.
  - `Shell.test.tsx` (+3): rendered old address under its parent, tabs drawn by permission, My Security vs Network Access.
  - `accessibility.test.tsx` was updated for the new labels; axe passes in jsdom.
  - `console.test.tsx` was updated: the Members page no longer draws its own Invitations link.
- **`tsc`** has no errors in `src`. The only errors are 8 in a stale generated `.next/types/validator.ts`, which refers to `partner/[partnerId]/*` pages that no longer exist; they predate this change. **`eslint`** is clean on the changed files. **`next build`** (production, scratch copy) succeeded.
- **Browser** (production build, headless Chrome, API answers mocked because there is no dev login to use): 36 / 36.
  - An administrator sees 13 entries. Each of the 22 addresses, plus `store/easy_crm`, `settings/webhooks` and `checkout`, shows the right current entry and tab.
  - Clicking a tab changes the address and the current tab.
  - A sidebar entry opens its first permitted page.
  - A plain member sees 7 entries and no tab strips.
  - At 360px there is no horizontal overflow, and the drawer lists all 13 entries.
  - Members has no duplicate Invitations link.
  - axe (light theme) finds nothing new on a tabbed page or a single page. It does report one existing finding, `label-content-name-mismatch` on the account avatar button: the button shows "AL" but its accessible name is "Account menu for …". That design is unchanged and is asserted by `accessibility.test.tsx`.
- **Backend:** the full suite, run as bare `pytest`: **5,056 passed** (1 h 01 min). There is no backend change, so this is a regression check. That run started before the Members/Invitations header links were removed, so the backend tests that read frontend source were run again on the final files: 209 passed. These were `architecture/`, `extension/test_frontend_contract.py`, `partner_*/test_architecture.py`, `partner_center/test_contract_shapes.py`, `partner_upgrade/test_upgrade_architecture.py` and `phase10/test_10f_architecture.py`.
- **Data:** there is no migration and no backend change, so no purchase, subscription, entitlement, permission, invitation, security policy or audit record can change.

## Not done / limitations

- The browser check used mocked API answers, not a real organization. The page bodies there show their own "couldn't load" states. The pages' real behaviour is covered by the unchanged vitest suites and the backend suite.
- `checkout` highlights no entry, as before. Moving it under Billing & Orders would draw tabs in the middle of a purchase, so it was left unowned.

## Files changed

- `frontend/src/features/launcher/orgNav.ts` - new: the 13 entries, sidebar and tabs
- `frontend/src/features/launcher/orgNav.test.ts` - new
- `frontend/src/features/launcher/Shell.tsx` - uses `orgNav`, draws the tab strip, account menu labels
- `frontend/src/features/launcher/Shell.test.tsx` - 3 tests added
- `frontend/src/components/sidebar/SidebarShell.tsx` - optional `current` on an entry
- `frontend/src/features/console/Console.module.css` - `.tabs`
- `frontend/src/features/console/MembersScreen.tsx`, `InvitationsScreen.tsx` - duplicate header links removed
- `frontend/src/features/console/console.test.tsx`, `frontend/src/accessibility.test.tsx` - updated assertions
