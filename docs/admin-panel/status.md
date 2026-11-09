# Admin Panel programme: status

Branch `feat/admin-panel`, based on the live release `b2eb178`. Not deployed. Decisions: `docs/architecture/adrs/ADR-060-admin-panel-wallets-kyc-and-module-map.md`. Plan and audit: `00-audit-and-plan.md`. How to build a screen: `ui-guide.md`. Browser check: `scripts/admin_panel_check/README.md`.

## Where it stands

Built and tested locally on `feat/admin-panel`. **Not deployed, not pushed, and the local development database is not migrated.** Nothing has been tried against production.

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
| Malware scanning of KYC documents | The scanner is a stub (`NullScanner`). The screen tells the reviewer documents are untrusted |
| Cloudflare account connection | Domains & Apps checks DNS and TLS live, without a Cloudflare login |
| A real payment gateway in these tests | Every payment test used the test provider. No money moved |

### Blocked

| What | What it needs |
|---|---|
| The rules in `08_Credits_Wallets_System.txt` and `07_Product_Web_Mobile_Flow.txt` | The files. The owner chose to build from the brief, so the questions below stay open |
| Deployment | The owner's word |
| CI, including gitleaks | The branch being pushed. gitleaks is not installed on this machine, so it has not run |

## Test results

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
