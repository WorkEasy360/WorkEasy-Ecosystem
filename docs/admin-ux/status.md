# Admin UX simplification — status

The owner's brief of 2026-11-18 asks for a simpler Super Admin console (`/admin`) in five phases. Phases 1–2 are frontend changes only. Phase 3 also adds optional date filters to the list endpoints; there are no database or RBAC changes. No feature is removed, every old address keeps working, and every page and action keeps its permission key.

| Phase | State |
|---|---|
| Step 0 — discovery | Reported 2026-11-18. The owner accepted the recommended answers to the 7 decisions. |
| Phase 1 — flatten navigation | **Done 2026-11-18** (4a97b2a). |
| Phase 2 — detail pages | **Done 2026-11-18** (the Customer page; Step-0 list D approved nothing else). |
| Phase 3 — tables, filters & actions | **Done 2026-11-18** (bcff059, b10530e), with the server disk cleanup. Deployed as 2026.11.18-b10530e. |
| Phases 4–5 | Not started. Each waits for the owner's approval of the previous phase. |

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

## Phase 2 — what changed

**Customer page (`/admin/customers/:id`):** six tabs in place of ten stacked blocks.

| Tab | What it holds |
|---|---|
| Overview | Subscription; Partner (its card for `partner.view`, else one line); member counts |
| Products & access | Entitlements and seats, staff overrides, product trials |
| Billing | Billing account; invoices (`payment.view`); plan history |
| Members | The members table |
| Activity | The customer's audit trail |
| Troubleshoot | "Why can't someone launch a product?" |

- **The tab is in the address:** `?tab=products|billing|members|activity|troubleshoot`, with Overview having no parameter.
  - The old `#billing-h` links open Billing and scroll to the invoices.
  - Failed & stuck's "Open billing" and the orders list's "Verify payment" now link to `?tab=billing#billing-h`.
- **A tab's data is read when it opens.** Members and plan history no longer need a "Show" click inside their own tab.
- **The partner appears once.** Staff with `partner.view` see the Partner card. Everyone else sees one "Partner" line, so no one loses sight of it.
- **Header:**
  - The primary action is "Assign plan" (`subscription.manage`).
  - "More actions" holds Reset network allow-list and Suspend / Reactivate organization (`customer.manage`).
  - Suspend keeps its confirmation with the optional reason.
  - Reset and Reactivate run at once, recording the automatic reason, as their buttons did before. The owner's decision of 2026-10-27 means no reason prompts.

## Phase 3 — tables, filters & actions (2026-11-18)

The owner's Phase 3 brief settled two questions:
- **Decision A:** Login as Guest stays a button beside View; it does not move into the `⋯` menu.
- **Decision B:** unused release images on the server are cleaned up safely.

Unlike Phases 1–2, this phase changes the backend too: list endpoints accept date filters. Those parameters are additive and optional, so a request without them answers exactly as before. There is no migration.

### The table system (kit)

**`DataTable`**
- **Actions column.** A table with row actions has one "Actions" column. It is always last and is held at the right edge while the table scrolls sideways. It holds the row's frequent actions as buttons (`actions`), then its `⋯` menu (`rowMenu`). In a table narrower than 600 px the buttons keep their icon and accessible name and drop the visible word.
- **Small screens.** Each column has a `priority`: `essential`, `high` (folds under 600 px), `medium` (under 900 px) or `low` (under 1200 px). It is measured on the table's own width (a container query), not the window's. A folded column's value is listed in the row's details, opened by the button at the start of the row (`aria-expanded`).
- **States:**
  - skeleton rows while loading;
  - the error with **Try again** (`onRetry`);
  - "Load more" with its own spinner, and its own error that keeps the rows already shown;
  - the empty state.
- **Sorting** stays on the rows loaded. While the server has more rows, the footer says "sorted within the N loaded". The server's own order (newest first) is the default.

**`usePaged`** has an in-flight guard, `loadingMore` and `moreError`. `pagedTable(paged, noun)` gives every server-paged list the same props. **`useLoad`** errors carry `retry`, so every `LoadGate` offers "Try again".

**`FilterBar`**: with `active`, the applied filters are listed under the controls as tags, each with its own remove button, and **Reset filters** clears them. Without it, the old "Clear filters" link is drawn.

**`DateRangeFilter`**
- A button reads, for example, "Created: 1 Nov 2026 – 18 Nov 2026".
- Its panel has quick ranges, From and To, and **Apply** / **Reset**. The server is asked only on Apply.
- It refuses a range that ends before it starts, or one that starts in the future.
- Days are whole UTC days, both ends included, matching the server.
- Keyboard: the panel takes focus, Tab stays inside it, and Escape returns to the button.

**`useUrlFilters(spec)`** keeps a list's filters in its address:
- A filtered list survives a reload, can be linked to, and comes back with Back after opening a record.
- Values the spec does not allow are dropped, never sent, and the page says so. A reversed range drops both ends.
- Other parameters in the address (`tab`, `report`) are kept.
- The router's own catch-up is not taken for a change from outside.

### Pages

**Partners › Partners** (Decision A):
- The row is View · Login as Guest · `⋯` in the Actions column. "View partner" left the menu, because View is the row's own button.
- A "Created" date range was added.
- Applied filters show as tags. The type chips stay a separate row, as the list itself, not a filter.
- Priorities: status essential, KYC high, type medium, the rest low. At 1366 px nothing scrolls sideways, and View / Login as Guest stay in view at every width.

**Login as Guest**
- The confirmation is the reason dialog, now titled "Login as Guest to <partner>?". It says that the session is read-only, lasts 30 minutes, and keeps the Admin signed in as themselves (no partner password). It says that **Exit Guest Session** in the banner ends it, and that the reason and every page are recorded. It names the partner type and account status.
- A draft's button is shown, says why it is unavailable when pressed, and starts nothing. The server refuses a draft too.
- The banner's buttons are now **Exit Guest Session** (was "Return to Super Admin") and **Exit to partner record** (was "End Guest Session"). Both end the session on the server first.
- The server side is unchanged (ADR-062/063):
  - the explicit dangerous permission `platform.staff_partner.guest`;
  - a server row per session, read on every request;
  - a 60-second one-time ticket, posted in a form body and stored only as a digest;
  - read-only enforcement on every endpoint;
  - one partner per session;
  - audit of start, end and every request.
- **Reauthentication:** none was added. The owner removed step-up for every action (ADR-050, 2026-10-24). A guest session cannot write anything, ends by itself, and is fully audited.

**Lists with filters in the address, date range, tags, Try again and the Actions column:**

| Page | Filters (address) | Date range | Actions column |
|---|---|---|---|
| Customers | q, status | Created | — (row opens) |
| Users | q, segment, product (server, was client-side) | Signed up (server, was client-side) | menu |
| Orders | q (server, was client-side), status, source | Created (server, was client-side) | — |
| Subscriptions | status, product, renewing, q | Created | — |
| Invoices | status, q | Issued | — |
| Refunds | status | Requested | — |
| Credit notes | q | Issued | — |
| Commission entries | unpaid (`?partner` kept) | Earned | Reverse |
| Payouts | status | Created | Mark ready, Approve, Record as paid, Cancel |
| Failed & stuck | — | — | Open billing, Retry activation |
| Payment events | provider, outcome | — | — |
| Entitlements | product | — | — |
| KYC applications | status, q (fixes the ignored `?status=` link from notifications) | Submitted | — |
| Product requests | status, q, kind (already in the address) | — | Review |
| Products | q, status, visibility, category | — | menu |
| Coupons | — | — | Enable, Disable |
| Wallets, Transaction controls | owner_type, q, low; tab + each tab's filters | — | History, Reverse, Retry, Mark reviewed |
| Tickets, Partner tickets | status, audience, assigned, q | Opened | — |
| Help articles | status, kind, q | — | menu |
| Customer setup | status, product | — | Approve, Request correction |
| Withdrawals | status | — | Start payout, Reject |
| Audit log | action, area, person | Date | — |
| Sign-in audit | whose, kind, q | Date | — |
| Reports | report, product, q | Period (default: last 30 days) | — |
| Usage pricing, Credit packs | — | — | Set/Remove rate, Edit/Archive |

The Audit log's "By" and "Resource" filters still filter only the rows loaded, because the endpoint has no such parameters. They are listed as tags, with a note when more rows exist.

**Confirmations added** for sensitive actions that acted at once:
- Unpublish a help article, Unpublish a product, Disable a coupon.
- Disable a payment provider, Switch a provider between live and test.
- The withdrawal and payout-details Reject dialogs now say what follows.

The rest (Suspend, Deactivate, Sign out everywhere, Reverse, Cancel payout, Archive, Remove rate, Revoke) already confirmed.

**Route pages:** Wallets, Transaction controls, Orders, Invoices and Users no longer remount the screen on an address change, so a search box keeps its focus while the filter is written to the address.

### Server: `from` / `to` on the list endpoints

`YYYY-MM-DD`, whole UTC days, both ends included, either one optional. A bad day answers 400 ("Use a date like 2026-11-09."). A reversed range answers 400 ("The date range ends before it starts."). The check is in `optional_window`, so it also applies to the audit list.

| Endpoint | Field |
|---|---|
| admin partners list | created_at |
| customers | created_at |
| users | account created_at (also `product`) |
| orders | created_at (also `q`) |
| subscriptions | created_at |
| invoices | issued_at |
| refunds | created_at |
| credit notes | issued_at |
| tickets | opened_at |
| KYC queue | submitted_at |
| commission entries | event_at (when earned) |
| payouts | created_at |

Tests: `backend/tests/admin_panel/test_list_date_filters.py`.

### Server disk cleanup (Decision B)

**Before:**
- `/` was 80 % used, with 3.7 GB free.
- Docker held 74 images (8.6 GB), with every release tag since 2026-10-13 kept.
- Patch releases are built on top of each other, so most old tags held no layers of their own. The space was in images outside the kept chains: the 2026-10-13 build, five side-branch frontends from 2026-10-23/25, three untagged images, and one never-started container left by the failed first fa88fcc build attempt.

**Retention:** the new committed script `deploy/single-host/w360-prune-images` is installed at `/opt/workeasy360/bin/`.
- It keeps the release in `current` and in `W360_RELEASE`, the `--keep` rollback release and its `-flat` base, and every image any container uses.
- It removes only other `workeasy360-frontend` / `workeasy360-backend` tags, then untagged images no container uses.
- It never touches containers, volumes, release directories, backups or other repositories, including `alpine:3` (used by the backup job) and the stopped Partner Panel.
- It is a dry run unless `--apply`, and it refuses when a kept release is incomplete.
- Runbook: `deploy/single-host/README.md` › Release and rollback.

**First run** (kept 2026.11.18-fa88fcc current + 2026.11.18-4a97b2a rollback):
- 63 tags and 3 untagged images were removed.
- `/` is now 62 % used, with 7.0 GB free.
- Every service stayed up and healthy, with all 10 volumes present. The API health check answered 200, and admin, partner, app and store answered 200.

**Left as is (owner's call):** the stopped Partner Panel's images (`partner-panel-api` 1.07 GB, `partner-panel-web`, `postgres:16-alpine`) and its stopped containers. README › partner.workeasy360.com keeps them as its documented way back.

### Deployment

**Release.** 2026.11.18-b10530e has been live since 2026-11-18, about 16:05 IST. It is a patch build on the 2026.11.18-fa88fcc images:
- the backend's Python and the frontend changed;
- there is no migration;
- requirements, package-lock and the Dockerfiles are unchanged, so it still runs Next 16.4.2.

**Checks before the switch:**
- The new code accepts the live environment.
- The images keep the live images' settings and health check.
- A throwaway web container served `/login` with 200.
- A backup was taken: `20261118T103519Z`.

**Checks after the switch:**
- The API is healthy, with 0 unapplied migrations.
- The API and web logs show 0 errors.
- admin, partner, app and store each answer 200 and serve the new build.
- An admin list called without a session answers 401, whatever its parameters.

**Image retention** then kept b10530e and fa88fcc. `/` went to 54 % used (8.6 GB free).

**Rollback:** switch the release back to 2026.11.18-fa88fcc. There is no schema change, and the new list parameters are additive.

**Tests:**
- Frontend: vitest 1,911/1,911 across 103 files. Each file touched by the date-format fix was re-run on b10530e.
- Lint and type-check are clean, and `next build` plus the dynamic-rendering check pass.
- Backend: the full suite passed in CI on bcff059. The backend did not change after that commit.
- Browser: a fixture-backed check of a production build passed 223/223. It covered 5 widths, axe, the filters, Back from a record, the guest confirmation and Try again.

**CI on the PR.** CI had been red since Phase 1 for reasons outside this work:
- the npm audit gate, on Next 16.4.2 and on dev-only picomatch;
- gitleaks, which scans every branch, on another branch's `RETRY_KEY` name.

The fixes from `fix/next-16.4.6-advisory` were merged as a19617d. After that, all 13 CI checks passed, including the full backend suite and the frontend job on Next 16.4.6. Next 16.4.6 ships with the next release, because it changes `node_modules`.

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
