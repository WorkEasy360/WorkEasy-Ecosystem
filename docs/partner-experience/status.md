# Admin-created partners and type-specific workspaces: status

Date: 2026-10-23. Decision: [ADR-047](../architecture/adrs/ADR-047-admin-created-partners-and-type-workspaces.md). Browser check: [scripts/partner_experience_browser_check](../../scripts/partner_experience_browser_check/README.md). **Not committed, not pushed, not deployed** - the owner tests all three partner types by hand first.

## Result

**ADMIN-CREATED PARTNER + TYPE PANELS: PASS** for everything the platform has a model for; the open items and honest gaps are in sections 4 and 5. Gates in section 2.

**Can Super Admin now create a partner with an email, an admin-defined password and a selected partner experience, after which the same credentials work at the partner portal and the partner is routed to the correct Reseller, White-label or Affiliate panel while authorization remains capability-driven?**

**YES - in the Workeasy360 stack** (verified in a real browser against a production build and a real backend). **One thing is not in this code's hands:** `partner.workeasy360.com` currently serves the *separate* Partner Panel app (decision of 2026-10-20), which has its own sign-in. These credentials work on the Workeasy360 partner portal (`/login?mode=partner` -> `/partner/<workspace>/dashboard`, and on the `partner.` host the multi-portal configuration of ADR-043 serves). Pointing the production partner host at this portal (or making the panel use Workeasy360 identity) is an edge/owner decision that was not made here. The portal-host routing of the new addresses is covered by unit tests (`portal-routing.test.ts`), not by a live host.

## 1. What was built

- **Add Partner** (`/admin/partners/new`, "+ Add Partner" on every Partners list): seven steps - Business details, Login credentials, Partner type, Products, Commission, Pricing (not for an affiliate), Review - then **Create Partner**. One server command validates the whole form, then one transaction creates the user (hash only), the partner, its profile, the capability grants, the product grants, the partner prices, the commission rules, the administrator's owner membership and the activation. The review is the same code as a dry run and never receives a password.
- **Credentials**: the administrator's email and the password Super Admin types (+ confirm, strength hint, requirements, show/hide). The platform's password policy applies plus a 128-character ceiling. An email that already has an account is never overwritten: "This email already belongs to an existing Workeasy360 account." with **Link existing user as Partner Admin** (no password involved) or **Cancel**; a staff account cannot be linked.
- **Partner type = a preset**, not a kind: `capabilities.EXPERIENCE_BUNDLES` lists registry keys applied through `grant_capability`; `PartnerAccount.default_experience` (one CHECK-constrained column, migration `partners.0007`) only selects the landing among workspaces the grants already open.
- **Three workspaces** over one shared panel (`/partner/reseller|white-label|affiliate/...`, with `/reseller/...` etc. as redirects): Reseller (13 entries for the standard grants), White-label (15: adds Brand Settings, Custom Domain, Website Branding, Pricing & Plans, Orders & Billing), Affiliate (12: dashboard with the referral link and **Copy Link**, Referral Links, Referral Codes, Campaigns, Marketing Materials, Leads & Referrals, Conversions, Commissions, Payouts, Reports, Profile & Settings, Support). Entries appear only for the permissions the server resolved; pages outside a workspace and workspaces the account does not hold are refused; every request is authorized again by the service it reaches.
- **Super Admin Partners list**: Partner, Admin email, Primary experience, Capabilities, Products, Customers, Status, Created, Last login, Actions (View, Edit details, Change capabilities and experience, Products and pricing, Commission, Reset password, Suspend / Reactivate). **Experience panel** on a partner: add / retire an experience or set the primary one, audited with a reason, never touching referrals, commissions, payouts or customers.
- **Workspace switcher** only for a partner that holds several workspaces; a person with two accounts of one kind is asked which they mean.
- Defense in depth for the typed password: absent from every `repr`, hidden from Django error reports (`sensitive_variables`), `no-store` responses, never in audit/outbox/log/metric, and the development log format prints plain tracebacks (no local variables).

### Changes outside the feature, and why

- `frontend/package.json` + lockfile: **Next 16.3.8 -> 16.4.2** (and `eslint-config-next`), plus the lockfile's vitest/vite patch releases. Advisories published after the last audit made `npm audit` fail on the unchanged tree (Next: critical RCE in the image-optimisation API, high DoS in Server Actions). The CI policy now passes; run `npm install` in `frontend/`.
- `.secrets.baseline`: regenerated on Linux with CI's detect-secrets 1.5.0 and 19 reviewed false positives marked (14 from this work - field names and test fixtures such as the review's password-state field - and 5 older ones that the earlier commits had not audited).
- `structlog` console renderer: plain tracebacks instead of Rich's variable dump; `user_facts_for_email` also says whether the account is staff; the products read lists prices only with `pricing.view`; commission amounts in the referral conversion rows only with `commission.view`; `referrals.summary` carries a server-made `total`.

## 2. Evidence

| Gate | Result |
|---|---|
| New backend tests (`backend/tests/partner_experience/`) | **167** tests in 7 files, all pass (architecture incl. the frontend pins, contract shapes, creation, credentials and atomicity, experiences and transitions, workspaces and isolation, pricing / commission / concurrency) |
| Full backend suite (bare `pytest`), final tree | **4,684 collected; 4,683 passed, 1 failed, 0 skipped** (51 min 33 s). The one failure, `integration/test_phase6_concurrency.py::test_concurrent_replays_of_one_dead_delivery_create_one` (eight threads replay one dead webhook delivery; two succeeded instead of one), is in code this work did not touch: it passed five times alone and its whole file passed twice more, so it is an intermittent race in the existing suite (an open P3 below), not a result of this change. An earlier full run, before the last edit, had a different single failure - a test that pins the exact shape of the referral counts, which an added `total` key broke - fixed by putting the total beside the counts; the 904 tests of the affected suites were re-run green |
| ruff, ruff format (CI scope: `backend`) | clean (764 files) |
| mypy | clean (33 source files) |
| lint-imports | 18 contracts kept, 0 broken |
| bandit (medium and above) | 0 |
| pip-audit --strict | no known vulnerabilities |
| `manage.py check`, `makemigrations --check` | no issues, no changes |
| Frontend: clean `npm ci` (final lockfile), eslint, `next typegen` + tsc, vitest | clean / 0 / 0 / **987 passed** in 49 files |
| `next build` (scratch copy), `assert-dynamic-rendering` | clean; every route dynamic (the three workspace routes included) |
| npm audit | production 0; CI policy passes (only the accepted `braces` advisory, lint tooling) |
| Real browser (production build, real backend, headless Chrome) | **13/13 journeys**; **60 pages** audited: axe WCAG 2.1 A/AA **0**, CSP **0**, console errors **0**, focus visible, one `h1` each; reflow **145/145** at 1440, 1024, 768, 390, 320 px |
| Secret scans | detect-secrets (Linux, CI's version) 0 unaudited after the review above; gitleaks: no leaks |
| Independent review (Opus, read-only, adversarial) | 0 P0; 1 P1 and the P2/P3 items listed in section 4 handled; its "verified OK" list is in the final report |
| Manual screen reader | **NOT EXECUTED** |

Bugs the checks found and that are fixed: the type cards load after the step heading shows (harness waits now); two same-kind accounts opened the first by guess; White-label <-> Reseller transitions refused or over-revoked; a retired seller kept its product grants and could read partner prices; a custom date range asked the server before it had dates; a Create button measured mid-fade by axe (harness waits for transitions).

## 3. The matrix

| | RESELLER | WHITE-LABEL | AFFILIATE |
|---|---|---|---|
| Admin creates | PASS | PASS | PASS |
| Email/password login | PASS | PASS | PASS |
| Correct dashboard | PASS | PASS | PASS |
| Products | PASS | PASS | NOT APPLICABLE (promotion scope only; no product grant) |
| Customers | PASS | PASS | NOT APPLICABLE (refused by design) |
| Pricing | PASS | PASS | NOT APPLICABLE (refused by design) |
| Subscriptions | PASS | PASS | NOT APPLICABLE (refused by design) |
| Referral | NOT APPLICABLE by default (no referral capability in the bundle; shown if granted) | NOT APPLICABLE by default | PASS |
| Commission | PASS | PASS | PASS |
| Payout | PASS | PASS | PASS |
| Branding | NOT APPLICABLE | PASS | NOT APPLICABLE |
| Domain | NOT APPLICABLE | PASS | NOT APPLICABLE |
| Sub-partners | PASS | NOT APPLICABLE by default (bundle omits the sub-partner capability) | NOT APPLICABLE |
| Team | PASS | PASS | NOT APPLICABLE (not in its approved menu) |
| Reports | PASS | PASS | PASS |
| Support | PASS | PASS | PASS |
| Isolation | PASS | PASS | PASS |

Evidence: browser journeys A-D (every page of each workspace opened with the partner's own credentials; the affiliate's customer, pricing, subscription, brand, domain and sub-partner API calls refused; the reseller refused the affiliate and white-label workspaces; another partner's data answers 404) and the backend isolation tests (`test_workspaces_and_isolation.py`).

## 4. Open items

**P1: none open.** (The review's P1 - White-label <-> Reseller transitions refused or over-revoked - is fixed and tested.)

**P2 - owner decisions and limits**

1. `partner.workeasy360.com` serves the separate Partner Panel today. Pointing it at this portal (or making the panel use Workeasy360 identity) is an edge/owner step; until then "the same credentials at that host" holds only for the Workeasy360 partner portal.
2. **No forced password change.** The brief excludes a temporary-password workflow, so the typed password is the real one: whoever was told it can sign in until its owner changes it, and the first to sign in can enrol the account's second factor. Whether to ask the person to change it at first sign-in is your decision (the platform's operator-demo rule, `set_temporary_password`, would force it).
3. **Dev database**: `partners.0007` is not applied to your local database; until it is, partner queries fail there. It is additive; it needs your go-ahead.
4. A person who belongs to **two partner accounts of the same kind** is asked which one they mean and is opened into the id-scoped panel; type-specific workspace addresses name no partner (adding the id would change ADR-043's host rules).
5. **Link existing user as Partner Admin** makes the person an *owner* of the partner without notifying them (staff accounts are refused). Consider a notification or the `admin` role.
6. Development only: with `DEBUG=True` Django's debug page prints local variables whatever the sensitive markers say.

**P3**

- Conversions show link, campaign, dates and commission; product, plan, sale amount and status per conversion are not recorded against a referral.
- Commission scope is product-level; the rule engine has no plan-level scope.
- The password is hashed inside the creation transaction (bounded hasher; the database closes a transaction idle for 15 s). A timeout rolls back whole and can be repeated.
- Passwords are not Unicode-normalised (platform-wide, pre-existing).
- Old `/partner/<id>/<page>` addresses draw the page shell for pages a role cannot use; the data is refused by the server.
- While a second-factor prompt is open, the retry closure keeps the typed password in memory (the fields are already empty).
- `approve_payout` checks preparer != approver but not that the approver belongs to the payee partner (pre-existing; one click closer when a person is linked).
- An intermittent failure in the existing suite: `test_concurrent_replays_of_one_dead_delivery_create_one` (webhook replay under eight concurrent callers) let two replays through once in two full runs. Not touched by this work; worth a look by whoever owns delivery replay.
- npm: the accepted `braces` advisory (lint tooling only).
- Manual screen-reader pass: **NOT EXECUTED**.

## 5. Deliberately not built

- Affiliate "promotion scope" has no stored list: the products chosen scope the commission rule assigned to the affiliate and grant no right to sell (`NOT_IMPLEMENTED["affiliate_promotion_scope"]`).
- Affiliate **Signups** (the platform records a visit and a conversion, nothing between), a **marketing library**, **promo / discount codes**, a **wholesale cost / margin over cost**, sub-resellers for a White-label-only partner, and plan-level commission scope (the rule engine is product-level) - each stated on its screen in the registry's words, never a zero.
- Conversions list the link, campaign, dates and commission; product, plan and sale amount per conversion are not recorded against a referral (the screen says so).
- No change to MFA, password policy, session policy, the entitlement writer or the commission ledger.

## 6. Owner steps

1. **Try it**: apply `partners.0007` to your dev database (additive: one column with a default, one CHECK), start the backend on 127.0.0.1:8010 and `next dev`, sign in at `/login?mode=admin`, add one partner of each type, then sign in at `/login?mode=partner` with what you typed. The chooser's "Partner" button opens the separate Partner Panel while `W360_PARTNER_PANEL_URL` is set in `frontend/.env.local`; use the `?mode=partner` address (or unset the variable) to reach this portal.
2. **Dependencies**: `npm install` in `frontend/` (the lockfile moved to Next 16.4.2).
3. **Production host**: decide how `partner.workeasy360.com` reaches this portal (above).
4. **Commit** only after you have tested.
