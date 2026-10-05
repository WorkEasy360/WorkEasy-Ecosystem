# Phase 10 - Status

**Reseller + white-label administration, and the customer owner's activation (D-020 closed).**
Completed 2026-09-28, in the order the brief set: 10a, 10b, 10c, 10d, 10e, 10f. Nothing is committed (the working tree has been uncommitted since Phase 5). Design freeze: [00-plan.md](00-plan.md). Decisions: [ADR-037](../architecture/adrs/ADR-037-partner-consoles-and-owner-activation.md).

## Verdict

**RESELLER + WHITE-LABEL ADMIN: PASS.**

> *Can Workeasy360 now support a complete reseller lifecycle - creation, customers, sales, commission, payouts, branding, termination - without compromising the customer's independence?* **YES.**

A partner is created and set up in the console; provisions a customer; the customer's owner is emailed one secure link, proves the mailbox, chooses a password and becomes the owner; the partner sells the customer a plan; the owner holds the product; commission accrues and pays out through the Phase 7 lifecycle; and when the partner is terminated it loses access at once while the customer keeps their sign-in, their organization and their product. Every step of that was run through the UI, in a real browser, against the production build.

**One limit is stated, not hidden (P2, debt D-035):** a partner's verified, active custom domain **shows its brand** (login page, header, launcher) but **visitors cannot sign in from that domain yet** - Django's CSRF origin check does not trust a partner hostname, and TLS/edge routing for arbitrary hostnames is outside this repository. The browser check records the refusal on every run. It blocks nothing in the reseller channel.

## What was built

| Sub-phase | Delivered |
|---|---|
| 10a | Nav restructured into a **Partners** group (Overview, Resellers, White-label, Customers, Products & Pricing, Commissions, Payouts). `GET /admin/partners/overview`: eight factual cards (no MRR/ARR/growth/revenue), each linking to its filtered list. `GET /admin/partners/resellers`: keyset-paginated, filters (search, status, product, capability, scope), no N+1. Labels (Reseller / White-label / Affiliate) **derived** from effective capability grants by one function. |
| 10b | **Add Reseller wizard** (7 steps, `useStepRunner` + `ProgressList`): details, capabilities (the *Standard Reseller* preset over existing keys, grouped, keys in an advanced view), products, pricing, commission, administrator, server-backed review, confirmation. `POST /admin/partners/reseller-review` is a dry run that writes nothing; every figure is computed by the server. `commissions.rules.assign_partner_commission` closes the "missing relationship" (the FK already existed; the use case did not). Partner administrator = `PartnerMember` on the existing identity, invited through the existing account-token email. |
| 10c | **Reseller detail** with ten ARIA tabs: Overview (with setup progress), Customers ("Owner activation pending" shown honestly), Products & Pricing, Subscriptions (read-only here; changed where they always were), Commissions (accrual / reversal / adjustment, payout linkage, customer, sale, date, rule and version), Payouts (the existing lifecycle), Branding, Domains, Users & Permissions, Activity (the one audit log). |
| 10d | **White-label list** (a filtered view of `PartnerAccount`) and a **5-step wizard** (brand identity, live preview, domain request, DNS verification, activation) usable by the partner or by staff through the same commands. PNG/JPEG only, size-checked, same-origin, no SVG/remote images/arbitrary CSS or JS; a CSSOM-only preview of login, header and launcher. Lifecycle stays requested -> verification_pending -> verified -> active, never automatic. A second factor is asked on the **domain** step only. |
| 10e | **D-020 closed.** One hashed, single-use, expiring, org-bound link in the URL fragment; public preview/accept; existing-account and pending-self-signup handling; resend and withdraw for the provisioning partner and for staff; the console shows Pending / Activated / Expired / Revoked and never a token; customer independence. Additive, reversible migration `partners.0003`. |
| 10f | Architecture, security, performance and journey verification (below), plus the bugs they found. |

## Gates

**Backend**

| Gate | Result |
|---|---|
| `pytest` (full suite, `-p no:cacheprovider`) | **2,849 tests: 2,846 passed on the first full run; the 3 failures were Phase 7 guards that pin exact shapes this phase deliberately extended (a new public route, a new partner-visible field). Each guard was updated and now also pins what the new field may contain; those three files re-run: 39 passed.** No backend source file changed after the run began. |
| Phase 10 tests | **427**: 10a 19, 10b 53, 10c 28, 10d 31, 10e 54, contract shapes 8, architecture 28, security 202, performance 4 |
| `ruff check`, `ruff format --check` | pass (610 files) |
| `mypy` | pass (29 source files; this repo type-checks `contracts/`) |
| `lint-imports` | 18 contracts kept, 0 broken |
| architecture checks | `tests/architecture` pass, including F-6 (no cross-module ORM access) |
| `bandit -ll` | exit 0 (0 medium, 0 high) |
| `pip-audit --strict` | no known vulnerabilities (the first two attempts hit PyPI 503 and were retried) |
| `manage.py check`; `check --deploy` | no issues; `--deploy` is asserted against the real production settings by `tests/security/test_security_baseline.py`, which passes in the suite |
| `makemigrations --check` | no changes |
| Upgrade rehearsal `scripts/rehearse_phase10_upgrade.py` | **21/21 upgrade + 9/9 fresh**: Phase 9-shaped database with legacy invitations of every state migrated; no pre-existing column of any row changed; legacy rows hold no redeemable link; migration reverses and re-applies over populated data; models match migrations |

**Frontend** (clean copy: `npm ci` from the lockfile, nothing carried over)

| Gate | Result |
|---|---|
| `npm ci` | exit 0 |
| `npm run lint` | exit 0 |
| `npm run typecheck` (`next typegen` + `tsc --noEmit`) | exit 0 |
| `npm test` | **333 tests in 20 files pass** |
| `npm run build` (production) | exit 0 |
| No route statically prerendered (`assert-dynamic-rendering.mjs`) | pass |
| Production CSP | verified on the running production server: per-request nonce, `strict-dynamic`, no script `unsafe-inline`, `img-src 'self' data:`, `upgrade-insecure-requests`; zero CSP violations across every audited page |
| `npm audit --audit-level=moderate` | 0 vulnerabilities |

**Real-browser check** (`scripts/phase10_browser_check/`, production build, real backend, scratch database, Chrome via puppeteer-core; not part of CI)

| | Result |
|---|---|
| Journeys | **20/20 pass** (5 journeys + sub-steps + 3 keyboard-only flows) |
| Pages audited | 43 states |
| axe WCAG 2.1 A/AA (incl. colour contrast) | 0 violations |
| Visible focus indicator (40-Tab traversal) | 0 pages without |
| Exactly one `h1` | all 43 |
| CSP violations / console errors | 0 / 0 on every audited page. Seven *intended* refusals inside journeys are counted separately: three 401 step-up prompts, the spent owner link's 404, two 403s for a terminated partner, and the refused sign-in on the partner's own domain (D-035) |
| Reflow at **1440, 1024, 768, 390, 320 px** | 17 pages x 5 widths = 85 checks, **0 horizontal overflow** |
| Manual screen-reader pass | **NOT EXECUTED** |

## The five journeys

1. **Reseller creation.** Staff run the wizard; the review shows a £2.00 discount computed by the server (list £14.99, partner £12.99); confirmation; the step-up prompt appears on the click that needs it; the progress list completes; the partner is active with products, pricing and a 15% commission rule set, and the administrator pending. It appears in Resellers and in the overview counts. The administrator opens the emailed setup link (fragment removed from the address bar), chooses a password and enrols an authenticator.
2. **Customer provisioning -> activation -> product access** (mandatory). The partner provisions "Journey Customer Ltd" (step-up on that click). Staff see **Owner activation pending**. The owner's email carries one link in the fragment and no password, and the link appears in no other log line. The owner opens it in their own browser, sets a password and becomes the owner. Opening the link again is refused with one message. The owner signs in again. The partner sells a plan; the owner's app directory shows Example Desk **active and launchable**; staff now see **Owner activated**.
3. **White-label with AAL2.** A partner with branding and domain capability edits a brand with a PNG logo (nothing stored by editing; **no second factor**), approves the preview (saves; still none), requests a domain (**second factor asked here**), publishes the DNS record (which the platform then really reads), verifies (unproven until the record exists), and activates as its own explicit step. Before activation the brand is not served; after, it is - on the partner's own hostname only, with the logo loading same-origin.
4. **Combined partner.** Granting the reseller `branding.manage` and `domain.manage` changes its labels from `[reseller]` to `[reseller, white_label]`, adds it to the White-label list, and leaves exactly one account. It brands and serves a domain of its own alongside its customers.
5. **Termination.** The partner's domain goes off the air at once; its portal says the account has ended and offers only Commissions and Payouts; provisioning and customer reads are refused (403) while commission history stays readable (200). The customer signs in, is still the owner, and **Example Desk is still active**; staff see the relationship ended and the owner's activation still recorded.

Keyboard-only flows also pass: the Add Reseller wizard (Tab to a field, type, Tab to Next, Enter; focus moves to the new step's heading), the partner sections as ARIA tabs (arrow keys, Home, End), and signing in.

## Findings fixed during verification

Found by the tests and browser checks written for this phase; all fixed and now covered.

| Finding | Where found | Fix |
|---|---|---|
| The reseller review returned **500** for a non-text entry in `products` | 10f security | validated as a problem the screen can show; a test feeds every part of the draft every wrong shape |
| An owner or invitee address like `a b@example.com` passed `is_plausible_email`, then raised in the model - a **500** in provisioning, invitations and sign-up | 10f security | `is_plausible_email` applies the model's own validator (identity unit test grows the cases) |
| A **pending** self-signup for the invitee's address could not be claimed (told to sign in to an account that cannot sign in), and kept the squatter's name | 10e tests | a pending account is treated as uncredentialed; its name is asked for and required |
| A partner price had **no ceiling** | 10f security | `MAX_PARTNER_PRICE_MINOR` (10^12), enforced in the command and its dry run |
| A partner's accent colour could make **link text unreadable** (dark green on the dark theme: 2.78:1) and the hover fill mismatched the label | real-browser axe | every colour derived from an accent (label, hover fill, link text on each theme) is derived and pinned by property tests over 210 accents |
| Query-count assertions were **vacuous**: Django's 9,000-entry query log saturates after bulk seeding, so a capture read 0 before and after | 10f performance | `count_queries()` clears the log and refuses a count of zero; the 10a/10c/10d tests use it |
| `test_the_review_writes_nothing` counted the audit trail and notifications through row-level security, so it counted 0 both times | 10f | counted inside the platform organization's scope, with a liveness assertion |
| The portal offered Customers, Brand and the rest to a **terminated** partner, all of which the server refuses | 10f security | navigation and banner follow the resolver's per-status permission set |
| The audit redactor blanks any key containing "credential" | 10e tests | the audit key is `account_completed` |
| Racy tests (waiting only 1 s for async UI; choosing a select option before the catalog loaded; reading a figure before it arrived) failed one run in three under parallel load | 10e/10f | global 5 s async query timeout, 20 s test timeout, two tests wait for what they act on |
| Three Phase 7 guards pinned exact shapes | full backend suite | updated, and now assert what the new field may contain (no address, no link) |
| The provisioning success message still said the owner "must set up their sign-in" | review | says a one-time link was emailed |

## Reported discrepancies and design decisions

- **"Missing relationship" between `PartnerAccount` and `CommissionRule`.** The relationship existed in the schema (`CommissionRule.partner`, precedence in `select_rule`). The gap was a *use case*. Nothing was added to the schema.
- **Aggregate queries live with their owners, not in the control plane.** The first design failed rule F-6; it was refactored (ADR-037 section 2).
- **Staff-assisted brand and domain reuse the partner's own commands** (explicit `partner_id`, staff permission, refused on a terminated partner). Provisioning a customer stays a partner-portal action; staff do not provision on a partner's behalf.
- **Termination leaves a partner its money history only** (the Phase 7 resolver's rule). The portal now matches it.
- **A code is refused if reused in its 30-second window** (replay protection): the browser check waits for the next window rather than working around it.

## Performance (measured, not a capacity claim)

`backend/tests/phase10/test_10f_performance.py`, one developer machine, inside a rollback-wrapped test through Django's test client, against partners each with capability grants, a product grant, three customers, invitations, ledger rows and a payout (500 partners = 1,500 customers). Query counts include the fixed session/permission overhead.

| Console read | Queries at 10 / 100 / 500 partners | Median ms at 10 / 100 / 500 |
|---|---|---|
| Overview | 14 / 14 / 14 | 29 / 73 / 250 |
| Resellers (25 rows) | 16 / 16 / 16 | 34 / 96 / 107 |
| Resellers (100 rows) | 16 / 16 / 16 | 35 / 173 / 178 |
| Resellers, all filters | 16 / 16 / 16 | 46 / 118 / 111 |
| White-label (25), plain and filtered | 11 / 11 / 11 | 23 / 65 / 71 |
| Customers, all partners (25 / 100 / filtered) | 9 / 9 / 9 | 23-26 / 53-72 / 55-90 |
| One partner's customers | 10 / 10 / 10 | 25 / 44 / 52 |
| Partner summary | 26 / 26 / 26 | 132 / 129 / 138 |
| Partner pricing | 23 / 23 / 23 | 154 / 155 / 169 |
| Partner activity (audit) | 14 / 14 / 14 | 58 / 57 / 57 |
| Staff ledger, one partner | 8 / 8 / 8 | 27 / 36 / 39 |

Page size (1, 10, 25, 100) does not change any read's query count. A keyset walk of 500 partners returns every row exactly once. The activity read filters the one audit log by `changes.partner_id`; for a partner with **no** entries (the worst case, a full newest-first walk) it took 40 / 53 / 60 ms at 1,000 / 10,000 / 50,000 audit entries, still 14 queries - no index was needed at that size. The overview's *time* grows with the data even though its queries do not: recorded as D-033.

## Security verification

Backend, through the real HTTP stack (CSRF enforced where it is the point): `test_10f_security_access.py` and `test_10f_security_content.py`, plus the 10d/10e files' own cases.

- **IDOR and isolation.** Another partner's id is indistinguishable from an id that does not exist (404) on every partner read and write, its domain, logo, customer, and payout; a partner has no reach into any staff endpoint; a customer user has none into either console; the portal lists only the caller's own partners; staff ids that do not exist are 404.
- **Permission and capability bypass.** Each of the 18 staff endpoints this phase added answers 403 to a staff member without its permission (and to one with none), and not-403 to one holding it; a partner without a capability is refused by the server whatever its menu shows.
- **Price and commission manipulation.** A sale is priced by the server whatever the request carries (forged amounts, prices, discounts, rates, ids are ignored); forged quantities are refused, not coerced; the review recomputes every figure; commission rates outside 0-100%, non-integers and booleans are refused; a retroactive rate is refused; assigning a rate never rewrites an accrued row; forged prices, cycles and absurd amounts are refused and write nothing.
- **Domain takeover.** A request is not a claim (two partners may ask; only DNS proof gives the name, decided by a partial unique index); a proof for another hostname or partner verifies nothing; a released name needs its own new proof; a serving domain cannot be displaced by a new request; 26 hostile hostnames (loopback, IP, wildcard, port, path, credentials, control characters, over-long, the platform's own domains, reserved suffixes) are refused; a unicode lookalike is a different, unverified punycode name; nothing serves before proof and a separate activation; a partner's domains are bounded.
- **Host-header spoofing.** The API refuses a partner hostname in the `Host` header (400); `X-Forwarded-Host`, `Forwarded`, `Origin`, `Referer` never select a brand; only the explicit `host` parameter does, canonicalised, active + verified domains only, 16 lookalike and malformed forms get the default; the answer carries presentation fields only (no partner id); links in emails use the configured frontend, never the request (password-reset poisoning) - asserted for both the owner link and the administrator link.
- **XSS and injection.** Markup in a brand is stored and returned as JSON text, never a page; unsafe URLs, colours, theme tokens and SVG/HTML/GIF "logos" are refused and nothing stored; a logo cannot be a remote URL or another partner's file; the email is plain text with no HTML part; a header-injection attempt in an owner address or organization name reaches no recipient.
- **CSRF.** Every staff, partner and public write this phase added refuses a request without the token.
- **AAL2.** Each dangerous staff action answers 401 `step_up_required` without a recent second factor, and a stale one counts as none; branding saves at AAL1, a domain request does not.
- **Stale authority and termination.** A revoked capability, an expired grant, a removed member and a revoked staff grant stop working on the very next request; a terminated partner keeps only its ledger.
- **Invitation replay, expiry and race** (10e): the same link twice, six browsers at once (exactly one owner), two resends at once (exactly one working link), a claim racing a withdrawal (one consistent state), expired, wrong user, another organization's link, a tampered link, an existing account, a pending self-signup, a weak password, a suspended organization, a disabled account, rate limits, CSRF.

No exploitable IDOR, isolation failure or capability bypass was found. The defects that were found (two 500s and a missing ceiling) are in the table above.

## Accessibility

Real-browser axe (WCAG 2.1 A/AA, **including colour contrast**, which jsdom cannot see) over 43 states: 0 violations after the accent fix. Visible focus on every page, one `h1` on every page, focus moves to the new step heading in the wizard, the partner sections are true ARIA tabs, every action button names its customer (`aria-label`), and status is text first with colour only reinforcing it. **A manual screen-reader pass was not executed.**

## Remaining

**P1: none.** D-020, the P1 launch blocker, is closed.

**P2**
- **D-035 - sign-in on a partner's custom domain** (above). The design is in the debt row: trust the `Origin` of an active, verified partner domain in the CSRF middleware (with its own tests), and provide TLS and routing at the edge.

**P3**
- D-033 - the overview's time is O(partners) (constant queries); a maintained projection would be needed well beyond a few thousand partners.
- D-034 - the staff ledger shows the sale reference and customer, not the product name (the append-only ledger carries no product snapshot).
- The wizard is orchestrated by the browser over idempotent commands; closing the tab mid-way leaves a resumable partial partner (visible in the list as a draft) rather than an atomic one. The progress list says exactly which step ran.
- Notification emails are plain text and unbranded.
- The manual screen-reader pass.
