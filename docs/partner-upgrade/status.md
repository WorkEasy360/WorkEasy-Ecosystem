# Partner upgrade (Affiliate -> Reseller / White-label on the same login): status

Date: 2026-10-24. Decision: [ADR-049](../architecture/adrs/ADR-049-partner-upgrade.md). Browser check: [scripts/partner_upgrade_browser_check](../../scripts/partner_upgrade_browser_check/README.md). **Not committed, not pushed, not deployed.**

## Result

**PARTNER UPGRADE SYSTEM: PASS** for everything that can be verified before a release is deployed - on the platform's own code, a real database, a production build and a real browser. **Production login is BLOCKED**: this change is not in a release, and building and deploying one is yours to run (section 6).

Two things the brief asked for that the platform has no model for, stated rather than faked (section 5): **a paid upgrade** (set-up or onboarding fee, checkout, payment success) and **partner-set pricing / margins**.

## 1. What was built

- **One new table**, `PartnerUpgradeRequest` (`partners.0009`, one `CreateModel`): a workflow record with the partner (the one account), from / to experience, a status (`requested`, `profile_required`, `pending_approval`, `approved`, `rejected`, `cancelled`), a closed-key proposal, who asked and decided and when, what approving added and removed, a commercial *reference*, the correlation id and a version. A partial unique index allows **one open request per partner**; CHECK constraints pin the closed sets. It holds no capability, price or permission.
- **No new kind of partner, user or permission.** Upgrading is the existing `experience.transition_experience` (the same service the Experience panel uses) run by the approval, **additively**: the target bundle is granted and becomes the landing (`default_experience`, the brief's `primary_partner_workspace`); nothing is retired. Reason: `referrals.capture_referral` only accepts a code whose owner holds `referral.create`, so retiring the affiliate set would kill every referral link the partner has already published.
- **Partner side** (`/api/v1/partner/<id>/upgrade...`, administrators only to act, any member to read): state, start, save a step, submit, withdraw. A pure rule (`eligible_targets`) offers Reseller + White-label to an Affiliate, White-label to a Reseller, nothing to White-label. Business details go to the partner's own profile with the existing profile command, not into the request.
- **Staff side** (`/api/v1/admin/partners/<id>/upgrades...`, `staff_partner.manage`): read, decide (`approve` / `reject` / `request_info`), a cross-partner queue read. **Approval is one transaction**: partner active, request waiting, grant through the transition service, for White-label apply the proposed brand and domain through the existing brand / domain commands, then require the ADR-048 profile-completion rule for everything the partner now holds (products, commission rule, brand, referral code, business information) - or roll everything back and say what is missing.
- **Partner screens**: a *Grow Your Partnership* section on the Affiliate and Reseller dashboards (none on White-label); a step wizard at `<workspace>/upgrade[/reseller|/white-label]` - Reseller: Business, Products, Pricing, Commercial, Review; White-label: Business, Brand, Products, Pricing, Domain, Review. Each step is saved as completed and carries the request's version.
- **Super Admin screens**: an Upgrade panel on the partner's detail (current level, upgrade status, requested upgrade, server checklist, the proposal as plain text, history, Approve / Reject / Ask for more information), a notice above the tabs while a request is open, a badge on the All Partners row, and `upgrades_pending` on the dashboard.
- **Audit**: `partner.upgrade_requested`, `_submitted`, `_info_requested`, `_rejected`, `_cancelled`, `_approved` (the last carries from, to, who asked and when, capabilities added and removed, products, commercial reference and the request's correlation id). Entries name sections, never what the partner typed.

## 2. Evidence

| Gate | Result |
|---|---|
| New backend tests (`backend/tests/partner_upgrade/`) | **52** in 4 files, all pass: the flow end to end through the real endpoints (13), a partner cannot upgrade itself (22), what the upgrade must never become (14), the payloads against the TypeScript interfaces (3) |
| Full backend suite (bare `pytest`) | **4,779 collected: 4,762 passed, 3 failed, 14 errors** (56 min 42 s) - and all 17 are accounted for, none is a defect in the change. The 3 failures were the older architecture tests that pin the exact set of partner tables (`partner_center`, `partner_setup`, `phase10`): they exist to force a decision when a table is added, and the brief allows this one, so each now lists `PartnerUpgradeRequest` as a declared addition citing ADR-049. The 14 errors are every test that uses `caplog`: I ran the suite with `-p no:logging` to keep the log small, which removes that fixture. Those 17 were re-run without the flag together with the whole upgrade package: **69 passed, 0 failed**. The suite was not run a second time end to end after those test-only edits |
| ruff, ruff format | clean (780 files) |
| mypy | clean (33 source files) |
| lint-imports | 18 contracts kept, 0 broken |
| bandit (medium and above) | 0 |
| pip-audit --strict / npm audit (production) | no known vulnerabilities / 0 |
| `manage.py check`, `makemigrations --check` | no issues / no changes |
| Migration rehearsal (`partners.0009` onto a database that already holds partners, made by the real commands) | **PASS**: applied with no existing row changed (accounts, members, 30 capability grants, profiles, referral codes, ledger rows, users all identical), 20 columns, 4 CHECKs + the partial unique index + 2 indexes present, `w360_app` has SELECT / INSERT / UPDATE / DELETE, reverse to `0008` and forward again both clean, 0 unapplied |
| Frontend: eslint, `tsc --noEmit`, vitest | clean / clean / **876 passed in 48 files** (the new work adds 59: partner side 30, Super Admin side 29) |
| `next build` (scratch copy after a clean `npm ci`), `assert-dynamic-rendering` | clean; no new route (the upgrade pages are served by the existing workspace routes), every page dynamic |
| Secret scan (detect-secrets 1.5.0 on an LF export in a Linux container, CI's command) | 0 unaudited findings; `.secrets.baseline` unchanged |
| Real browser (production build, real backend, headless Chrome) | **7/7 journeys**; **45 pages** audited: axe WCAG 2.1 A/AA **0**, CSP **0**, console errors **0**, focus visible, one `h1` each; reflow **120/120** at 1440, 1024, 768, 390, 320 px |
| Manual screen reader | **NOT EXECUTED** |

**Database proof from the browser run** (scratch database, counted before and after the seven journeys): users 7 -> 7, partners 5 -> 5, partner memberships 5 -> 5, referral codes 4 -> 4, referrals 2 -> 2, ledger rows 2 -> 2. Four upgrades were approved (`alpha` Affiliate -> Reseller, `bravo` and `delta` Affiliate -> White-label, `echo` Reseller -> White-label); the Test C partner has one `requested` row and is still a plain Affiliate with its five grants.

**What the server said a partner held, before and after** (captured by the browser run): Affiliate -> Reseller: landing `affiliate` / 5 grants / workspaces `[affiliate]` -> landing `reseller` / 12 / `[reseller, affiliate]`. Affiliate -> White-label: `affiliate` / 5 -> `white_label` / 13 / `[white_label, affiliate]`. Reseller -> White-label: `reseller` / 10 -> `white_label` / 12 / `[white_label]` (White-label contains the Reseller workspace).

Bugs the checks found and that are fixed: staff could not reject a request the partner had started but never sent (the screen offered it, the service refused); a wizard step's heading shows before the profile has loaded (tests and harness wait for the form); and one harness flaw (a link in an input's value is not in `innerText`).

## 3. Acceptance table

| | AFFILIATE -> RESELLER | AFFILIATE -> WHITE-LABEL | RESELLER -> WHITE-LABEL |
|---|---|---|---|
| Same User | PASS | PASS | PASS |
| Same Email | PASS | PASS | PASS |
| Same Password | PASS | PASS | PASS |
| Same PartnerAccount | PASS | PASS | PASS |
| Upgrade Button | PASS | PASS | PASS |
| Upgrade Form | PASS | PASS | PASS |
| Approval | PASS | PASS | PASS |
| Capability Change | PASS | PASS | PASS |
| Correct Dashboard | PASS | PASS | PASS |
| Correct Menu | PASS | PASS | PASS |
| Wrong Route Denied | PASS | PASS | PASS |
| Old History Preserved | PASS | PASS | NOT APPLICABLE (no affiliate history to keep) |
| Commission History | PASS | PASS | NOT APPLICABLE |
| Payout History | PASS | PASS | NOT APPLICABLE |
| Audit Log | PASS | PASS | PASS |
| Production Login | BLOCKED | BLOCKED | BLOCKED |

Evidence: browser journeys A (Reseller), B (White-label, with a request for more information in the middle), C (denial), D and E (history), F (Reseller -> White-label), S (console); backend `test_upgrade_flow.py` and `test_upgrade_security.py`. "Same password" is the very password typed at Add Partner, used again after approval without any reset. "Old history preserved" is checked by identical referral links, clicks, conversions, balances and open commission / payout / referral screens before and after, plus unchanged row counts. "Production login" is BLOCKED because the change is not released (section 6).

## 4. Final verdict

1. **Can an Affiliate upgrade to Reseller using the SAME account and login? YES** - verified in a real browser and by row counts (no second user, partner or membership).
2. **After upgrade, does the SAME login automatically open the Reseller dashboard? YES** - `/partner/reseller/dashboard`, exactly the 14 Reseller entries, no Affiliate entries in the sidebar, the Affiliate workspace one click away in the workspace switcher. *In the platform's own stack*: production's `partner.workeasy360.com` is the same portal (since 2026-10-23) once this release is deployed.
3. **Can an Affiliate upgrade directly to White-label using the SAME account? YES** - without being a Reseller first.
4. **After upgrade, does the SAME login automatically open the White-label dashboard? YES** - `/partner/white-label/dashboard`, the 16 White-label entries, no upgrade offered.
5. **Are dashboard menus and permissions determined by the partner's CURRENT approved experience / capabilities? YES** - the landing is `default_experience` resolved against the live grants; the menu is drawn from the server-resolved permissions; every request is authorized again by the service it reaches. Before approval an Affiliate was refused `/partner/reseller/...`, `/reseller/...`, `/partner/white-label/...`, the same after editing `localStorage` / `sessionStorage` (`partner_type`), a forged request section, an ineligible target, the reseller APIs and every staff route - never a 2xx.
6. **Is all previous Affiliate referral, commission, payout and audit history preserved? YES** - nothing in the referral, ledger, payout or audit tables is read or written by the upgrade; the affiliate capability grants are kept, so published referral links keep earning.

**PARTNER UPGRADE SYSTEM: PASS** (production login BLOCKED until released).

## 5. Open items and what was not built

**Not built (decisions for the owner)**

1. **A paid upgrade.** The brief asks that set-up, subscription or onboarding fees come from Super Admin configuration and the billing architecture. The platform has no partner set-up fee, plan or checkout, and the Not-implemented registry already says a partner pays nothing for its programme. Approval therefore records a staff-entered *commercial reference* (a contract or invoice number) and nothing else: no amount, no currency, no payment status, no hard-coded figure (a test searches for them). Needs: what a partner pays, and an invoice / checkout path to attach to `approve` as a precondition.
2. **Pricing and margin configuration by the partner.** Prices and commission are set by staff; the rule engine is product-level and there is no wholesale cost. The wizard records a preference ("standard" or "I would like to discuss my pricing") and notes for the reviewer. For that reason the dashboard card says "Manage customers, subscriptions and direct sales" (the brief's section 17 wording), not "earn reseller margins".
3. **Domain verification as a gate to activation.** A custom domain is optional (ADR-048) and cannot be proven before the partner holds `domain.manage`, so approval *requests* the domain and the White-label workspace's Custom Domain page carries the DNS proof. Nothing is served on it until it is proven.
4. **Logo and favicon in the wizard.** A logo upload needs the White-label grant (done in Brand Settings after approval); the brand has no favicon or secondary-colour column beyond the theme's `accent`.
5. **A cross-partner review-queue screen.** `GET admin/partner-upgrades` exists (and a test uses it); staff reach requests through the partner page, the list badge and the dashboard count.

**P2**

- A partner may sit in two workspaces after an upgrade (`[reseller, affiliate]`). This is by design (decision 5 of the ADR); retiring the Affiliate set deliberately is an Experience-panel action and ends the published links.
- Approval is refused if the partner's profile is not complete for what it will hold - including an affiliate that no longer has an active referral code. The refusal names it; staff fix it with the existing screens.

**P3**

- A manual screen-reader pass was not executed.
- The Windows ephemeral-port caveat of the other browser checks applies: run it apart from the full backend suite.

## 6. Owner steps (nothing below has been done)

1. **Commit** when you are satisfied (the work is uncommitted on `fix/ci-run-1`).
2. **Release** as usual (`deploy/single-host/README.md`, "Release and rollback"): build and load the images under a new tag, unpack that commit's `deploy/` with `git -c core.autocrlf=false archive`, run `w360-backup` **first** (the backup is the recovery point; point-in-time recovery is off), set `W360_RELEASE`, re-point `current`, `w360 up -d`. The migration is `partners.0009`: one new table, nothing existing is touched, the previous release neither reads nor writes it, so the overlap is safe and a code rollback leaves the schema as is.
3. **Check it in production** with a partner that is an Affiliate: sign in at `partner.workeasy360.com`, open *Grow Your Partnership*, send a request, approve it in the Super Admin console with a commercial reference, sign out and in again with the same credentials.
4. **Decide** the paid-upgrade question (section 5, item 1) when you want a fee attached.
