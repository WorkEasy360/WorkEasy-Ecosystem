# Admin Panel programme: status

Branch `feat/admin-panel`, based on the live release `b2eb178`. Not deployed. Decisions: `docs/architecture/adrs/ADR-060-admin-panel-wallets-kyc-and-module-map.md` and `ADR-061-kyc-activation-scanning-publishing-and-partner-onboarding.md`. Plan and audit: `00-audit-and-plan.md`. How to build a screen: `ui-guide.md`. Browser check: `scripts/admin_panel_check/README.md`.

## Follow-up round, 2026-11-10

The owner's follow-up brief reported bugs found while inspecting the panel against a local database, and asked for the partner portal and launch gaps to be closed. This round is **not committed, not pushed and not deployed**. Its one new migration (`tenancy.0016`) is not applied to the development database. The production checks below ran locally only.

### What the reported bugs turned out to be

| Report | Finding | Fix |
|---|---|---|
| Three partners active with no approved KYC | Legacy accounts, not a bypass. `demo-partner`, `aman` and `na-partner` were activated on 16 to 27 October, before KYC existed, and have no KYC row at all | Lists label them "Active before KYC". The KYC page counts them and links to `?kyc=legacy`. They keep their status: approving them would forge a review |
| A new partner might be activated without KYC | **One real bypass.** A parent partner activating a draft sub-partner never asked about KYC (`hierarchy.activate_sub_partner`) | That path now calls `assert_may_activate`. Approval also no longer activates: activation is a separate step for `platform.staff_partner.manage` |
| 3 trial users, Subscriptions shows Trial = 0, customer shows no overrides | These are product trials (their own record), not subscriptions, so Trial = 0 on Subscriptions is right. The Entitlements page called every non-subscription access an "Override". The customer page did not list trials. All three trials had ended (the last on 9 November), but no clock job runs on the development machine, so their rows still read active | Entitlements names the real source (subscription, bundle, staff override, product trial). The customer page lists product trials and shows a trial past its end as ended. Subscriptions explains the difference. No subscription is fabricated |
| Easy CRM published without an Application URL | Its integration launches it (`https://crm.workeasy360.com/`). The old checklist wrongly demanded both URLs | One launch destination: an Application URL, the integration's launch URL, or an app link |
| Duplicate `crm` product, WhatsApp link, no plans, published | Created on 30 October through the status command, which never ran the checklist (products are created "listed"). Its only references are 3 media rows. Its integration is a draft. It has no plans, orders, subscriptions, entitlements or partner grants | The status command now runs the checklist for a listed product. The checklist also requires an active plan, a sales channel and return destinations. The product page flags "Published, but not ready". **The product itself is not touched:** archiving it needs the owner's word (see below). Deletion is refused by design, because it has been active |
| No CSV button on reports | The link rendered only when the current period had rows. The default is the last 30 days, so an empty database showed none. The earlier browser check fetched the CSV address directly | The button is always there. When there is nothing to export, it is disabled with the reason written beside it |

### Completed this round

- **New Partner form.**
  - Fields: **Partner Email** (with a hint that it is the partner's own login, not a staff address), Partner / Business name, type, country, optional mobile number, optional separate contact email, and the invitation method.
  - The submit button says whether an invitation goes out.
  - Both access options stay. With "Set a password now", the partner must replace the typed password at first sign-in unless the Admin unticks it; only the hash is stored and no password is emailed.
  - Refused: a duplicate name (unless the Admin confirms a different partner), a duplicate contact address, and an address whose invitation is still pending. That refusal names the partner waiting for it.
- **Invitations told as they are.** The create answer says queued, deferred or not sent. The member list reads the email's delivery record: queued, sent, retrying, failed or suppressed. A development server says it prints mail instead of sending it.
- **Partner portal.**
  - Accept Invitation page (`/partner/accept-invitation`).
  - Draft partners open on their KYC page.
  - Password change and sessions on the partner's Account page.
  - **Continue as Guest** on the partner sign-in, opening a static preview with example data, no session and no API call (`/partner/preview`).
- **KYC.**
  - Two-step activation.
  - The sub-partner bypass is closed.
  - "Approved partners to activate" in Pending Actions and the bell.
  - Every state but approved is refused, tested for each state.
- **KYC malware scanning, fail-closed.**
  - ClamAV (`clamd`, INSTREAM) adapter, in the outbound boundary (`integration/egress/clamd.py`) like every other socket.
  - With `KYC_MALWARE_SCAN_REQUIRED` on (production refuses to start with it off), a document is opened only after a clean scan. Approval waits for every current document.
  - No scanner or no verdict keeps the file quarantined. A threat deletes the content.
  - Retried every 5 minutes.
- **Customers.** Assign Partner, Change Partner and Partner Assignment History on the customer page. Eligibility, permission and audit are enforced, and no commission is rewritten.
- **Orders.**
  - Purchase source column and filter: Store, partner activation, partner storefront.
  - The source reference names the partner.
  - "Verify payment" on orders awaiting payment.
  - The CSV export uses the currency's own minor unit: it used to divide by 100, which is wrong for JPY.
- **Plans.** The New Plan form now has the renewal rule, the upgrade/downgrade rule and included usage per meter. It points to Commercial rules for partner purchase rates, which are per product and partner type, not per plan.
- **Settings.**
  - KYC document access log across every application (Settings → KYC document access).
  - Live DNS and TLS checks for product and partner domains, not only the portals.
  - "Approved branding" renamed to what it is: partner branding.
- **Wallets.**
  - The wallet ledger and the KYC logs refuse TRUNCATE (`tenancy.0016`).
  - Usage events dated in the future are refused, because they could draw on a free allowance that has not started.

### Partially completed or still open

| Area | Where it stands |
|---|---|
| Top-up refund race | The "already spent" check runs when the refund is requested, and the money leaves the wallet when the provider confirms. A customer who spends in between makes that confirmation fail. The fix is designed (hold the amount at request, credit it back on failure) but **not built**, because it crosses the refund and settlement states |
| Plan tier (Free / Basic / Advanced) | No tier field exists. Plans of a product are ordered by `sort_order`. A tier label would be a new column; not added without a decision |
| Plan validity | Price windows (`valid_from`/`valid_to`) exist on Product → Plans & pricing; a fixed plan term does not |
| Notification configuration | One notifications email. No per-event or per-channel settings |
| Payment verification queue | "Verify payment" opens the customer's invoice, where an offline payment is marked received. There is no cross-customer queue screen beyond Orders → Awaiting payment |
| Subscription history | Plan lines added and ended (Assignment history) and the customer's audit trail. No status timeline |
| Partner Platform Wallet in the partner portal | Staff see and top up a partner's Platform Wallet. The partner portal does not show it yet |
| Suspended partner and commissions | Accrual still runs while suspended (`accrual.py`), unchanged. The owner's rule is pending |
| Development data | The ended trials, the duplicate `crm` and the missing `tenancy.0016` are in the local development database. Each needs the owner's approval to change |

### Test results for this round

Only what actually ran, on the uncommitted working tree on top of `b24d7b3`.

| Check | Result |
|---|---|
| Backend suite (bare `pytest`, real PostgreSQL) | 5,330 passed of 5,334 in 1 h 53 min. The 4 failures were architecture and API-shape pins, not behaviour: the invitation-state read bypassed tenant scoping (now reads through the scoped managers), the ClamAV adapter opened a socket outside `integration/egress` (moved there, registered at start-up), and two tests pinned the old New Partner request and customer record (updated for the deliberate additions). The fixes, and the server half of "choose your own password at first sign-in", were then re-run in the affected folders (`tests/architecture`, `partner_experience`, `partner_management`, `admin_panel`, `phase7/test_http_api.py`, `platform/files`, `platform/identity`): **572 passed, 0 failed**. The whole suite was not run a second time |
| Frontend suite (vitest, with axe) | **1,548 passed of 1,548** (90 files). The first full run had 1 failure: the partner page's title was read once, before the effect that sets it had run on a loaded machine. That file passed alone three times; the test now waits for the title, and the full re-run passed |
| Real browser, production build, Chrome | **140 passed of 140**; 0 console errors, 0 API errors. New journey M: Continue as Guest (no API call, axe clean at 1366 and 390), Accept Invitation, the Partner Email field, a new partner stays a draft, the duplicate-name confirmation, and a customer's partner assigned with its history kept. Every report shows its Export CSV button |
| Page sweep inside that run | 60 pages at 1920, 1366, 1024, 768, 390 and 360: 360 loads, no page overflow, nothing clipped, one h1 each |
| axe in the real browser | 118 page loads: 0 violations |
| Migration rehearsal from live release `b2eb178` | **30 checks passed**: rows unchanged forward, after reversal and after re-applying; no KYC approval forged; the ledgers' TRUNCATE guards exist, go on reversal and come back |
| Production build | Compiled, on the final frontend source |
| Static checks | `tsc` (source clean; 8 errors only in a stale generated `.next/types/validator.ts` of 13 October in the development folder), `eslint`, `ruff check`, `ruff format --check`, `mypy` (CI scope), import contracts (18 kept), `manage.py check`, `makemigrations --check` (no changes), `bandit -ll` (no issues) |
| Secrets | `detect-secrets` over the 109 changed files: nothing beyond the baseline (run on Windows, which can skip UTF-8 files; CI on Linux is the authority). gitleaks is not installed here |

## Where the programme stands (2026-11-09)

The programme itself is committed on `feat/admin-panel` (last commit `b24d7b3`). **Not deployed and not pushed.** Its migrations were applied to the local development database on 2026-11-10; this round's `tenancy.0016` is not. Nothing has been tried against production.

### Completed

Every item below ran against real endpoints and a real database, with no mock data on any screen.

- **Admin login:** the split-screen design, real credentials, Show/Hide, Remember Me, forgot and reset password, rate limits, sign-out, no public registration.
- **Dashboard:** the six cards, the three charts, Pending Actions, Recent Activities and Top Products, all on real figures.
- **All nine modules** and every page inside them: 52 pages plus 6 record pages. Only main menus appear in the sidebar; the rest opens inside the module.
- **Partner KYC:** public application, documents with an access log, review, corrections, approval, rejection. Approval is required before a new partner can be activated. Suspension is separate and leaves customers' subscriptions alone.
- **Three wallets:** Platform, AI Credits and Consumption, each an append-only ledger with idempotency keys and no overdraft. Commissions stay earnings, not a fourth wallet.
- **Usage pricing:** AI token rates, credit packs, messaging, API and webhook rates, all entered by an Admin. No rate means no charge.
- **Billing:** orders, invoices, credit notes, refunds that name the original payment, activation retry, plan and renewal rules that take effect.
- **Commissions:** rules, clearance, payout requests, approval, processing.
- **Support, reports and settings:** tickets, help content, 8 reports with filters and CSV, company and notification settings, domains, login clients, permissions, sign-in and administrative audit.
- **Global search and notifications** on real data, limited to what the person may see.
- **Authorization on the server:** every `admin/` route is walked by a test for anonymous, customer, no-grant staff and read-only staff, and for CSRF.
- **Upgrade:** the rehearsal from the live release leaves every existing row unchanged, reverses and re-applies, and the live Admin keeps the whole role.

### Partially completed

Each works; the limit is stated so nobody assumes more.

| Area | The limit |
|---|---|
| Notifications | The counts are real. Which ones are marked read is remembered per browser, not per person |
| Audit log | The By and Resource filters act on the rows loaded, not on the server |
| Ticket filter | "Open" also includes in-progress and waiting |
| KYC decision | An empty choice of partner type means "what they asked for". The record page does not list rights already granted |
| Queue counts | The counts on status chips ignore the search and product filters |
| Stuck orders | The list stops at 100 and says so |
| Plan rules | They apply to plans, not bundles |
| KYC PDF viewing | The browser check opened a PNG. For a PDF only the security headers are tested, so open one by hand once |
| Applicant who already has an account | They receive a notice by email and nothing else changes, so the public form cannot reveal who is registered |
| Commission to payout | Covered by the existing `tests/phase7` acceptance flow, plus a test that prepares a payout from the new screen. There is no browser journey for it |
| A link to the billing section | `#billing-h` may not scroll into place, because the section loads late |

### Not implemented

| What | Why |
|---|---|
| Sending SMS or WhatsApp | The platform has no sender, so those charges arrive only when a product reports usage |
| Malware scanning of KYC documents | Superseded on 2026-11-10: a ClamAV adapter now exists, and KYC documents fail closed without it. Production still needs a `clamd` service (see below) |
| Cloudflare account connection | Domains & Apps checks DNS and TLS live, without a Cloudflare login |
| A real payment gateway in these tests | Every payment test used the test provider. No money moved |

### Blocked

| What | What it needs |
|---|---|
| The rules in `08_Credits_Wallets_System.txt` and `07_Product_Web_Mobile_Flow.txt` | The files. The owner chose to build from the brief, so the questions below stay open |
| Deployment | The owner's word |
| CI, including gitleaks | The branch being pushed. gitleaks is not installed on this machine, so it has not run |

## Test results of the programme (2026-11-09)

Only what actually ran, on commit `5dbec10`. The commit after it updates one test's migration pin and these documents; nothing else changed.

| Check | Result |
|---|---|
| Backend suite (bare `pytest`, real PostgreSQL) | 5,296 passed of 5,297 in 2 h 11 min. The one failure was `tests/partner_center/test_architecture.py`, which pins the newest `authz` migration and did not yet name `authz.0010`. The pin now names it, with its reason; that file, `tests/admin_panel/test_upgrade_grants.py` and `tests/architecture` were re-run: 110 passed |
| Frontend suite (vitest, with axe) | 1,531 passed of 1,531, on an idle machine |
| Real browser, production build, Chrome | 113 passed of 113; 0 console errors, 0 API errors |
| Page sweep inside that run | 58 pages at 1920, 1366, 1024, 768, 390 and 360: 348 loads, no page overflow, nothing clipped, one h1 each |
| axe in the real browser | 116 Admin page loads at 1366 and 390, plus login, forgot password and the partner application at both: 0 violations |
| Migration rehearsal from live release `b2eb178` | 26 checks passed: rows unchanged forward, after reversal and after re-applying; no KYC approval forged; no wallet invented; the Admin keeps every permission |
| Production build | Compiled, on the final frontend source |
| Type checks | `tsc` clean, `mypy` clean |
| Lint | `ruff check`, `ruff format --check` and `eslint` clean, with 1 existing warning in the storefront |
| Import contracts | 18 kept, 0 broken |
| Django | `check` clean, `makemigrations --check` finds nothing |
| Security | `bandit` clean at medium and above, `detect-secrets` flags nothing beyond the baseline across 271 changed files, `npm audit --omit=dev` finds 0 |
| `pip-audit` | Reports dnspython 2.7.0 (local Python 3.10; CI on 3.11 uses 2.9.0) and filelock (not in the requirement files). No dependency changed on this branch |

The ten workflows in the brief: 1 in `tests/admin_panel/test_kyc_and_dashboard.py` and the browser, 2 to 7, 9 and 10 in `tests/admin_panel/test_workflows.py`, 8 in `tests/phase7/test_final_acceptance_flow.py` and `test_commissions.py`.

## What deployment needs

0. **Added 2026-11-10.**
   - Run a ClamAV daemon reachable from the web and worker containers, and set `W360_FILES_MALWARE_SCANNER=clamd` (plus `W360_CLAMD_HOST` and `W360_CLAMD_PORT`). Without it, KYC documents stay quarantined and no application can be approved. That is intended.
   - `tenancy.0016` is the fifteenth migration. The upgrade rehearsal applies it, reverses it and re-applies it, and checks the three TRUNCATE guards each time.
   - The `integration.egress` app is new in `INSTALLED_APPS`. It has no models; it registers the ClamAV adapter at start-up, so the web and worker containers both need the new settings.
   - New invitation emails link to `/partner/accept-invitation`.
   - After an approval, someone with partner management must activate the partner.
1. Back up first. Fourteen migrations apply; all of them reverse (rehearsed). `authz.0010` gives the live Admin the six new permissions.
2. `W360_PARTNER_KYC_REQUIRED` stays on. Production refuses to start without it.
3. After deploying, an Admin enters KYC requirements, usage rates, credit packs and low-balance thresholds, and sets the notifications email. Until then nothing bills, which is deliberate.
4. Open one PDF as a KYC document by hand, as noted above.
5. The older browser-check scripts that activate partners on development settings now need `W360_PARTNER_KYC_REQUIRED=false`. `scripts/ecosystem_browser_check/prepare.py` already sets it.
6. Migrating the local development database needs the owner's approval.

## Decisions the owner should know about

The owner decided two things on 2026-11-09: build from the brief, because `08_Credits_Wallets_System.txt` and `07_Product_Web_Mobile_Flow.txt` are not on this machine, and add a public partner application form.

Defaults chosen where the brief was silent. Each is easy to change:

| Default | Why |
|---|---|
| Partners already active stay active, with KYC in draft | Marking them approved would forge a review. The KYC gate applies only to draft to active |
| "Pending Setup" is a separate setup state, not a subscription status | The brief keeps payment, subscription, setup and member access separate |
| The UI says "Admin"; the internal key stays `SUPER_ADMIN` | Renaming the key would touch every grant for no gain |
| Partner commission views say "Earnings" | The brief: commissions are earnings records, not a fourth wallet |
| Old Admin addresses keep working | Nothing bookmarked breaks |
| A plan set to renew by hand and one set not to renew behave alike | Both end with the paid period and can be bought again. The brief names the rules but not a difference |
| On upgrade, security administrators receive the six new permissions; holders of narrower roles do not | Otherwise the live Admin would meet a 403 on the new screens. A Billing or Support Admin who needs wallets, KYC or reports is granted them by an Admin, with a reason |

## Questions for the owner

The two missing documents would probably have answered the first six.

1. Do AI credits expire?
2. Is there a minimum top-up?
3. Should a refund go back to the wallet or to the original payment method? Today a wallet payment refunds to the wallet and a card payment to the card.
4. Should a renewal be paid from the Platform Wallet automatically? Today it is not.
5. Are top-ups and credit packs taxed? Today no tax is charged on them.
6. What low-balance threshold should a new wallet start with? Today it has none, so no alert fires until an Admin sets one.
7. Should a suspended partner keep earning commission on new sales? Today it does (`commissions/accrual.py`).
8. Should a partner's Platform Wallet pay for anything? Today it receives offline top-ups only.
