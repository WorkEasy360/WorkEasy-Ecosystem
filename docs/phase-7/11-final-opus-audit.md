# Phase 7 final adversarial audit

Date: 2026-09-24. Scope: the Phase 7 Super Admin control plane and partner
channel (reseller, white-label, affiliate), as implemented - code first, docs
checked against it. Phase 8 was not started; no product-domain code and no
payment integration were added.

Every defect below was **proven before it was fixed**: a regression test was
written against the audited code and failed for the stated reason, then the fix
made it pass. The tests are in `backend/tests/phase7/test_opus_final_audit.py`
unless noted.

## Findings

### P0

None.

### P1 - fixed

| ID | Finding | Fix |
|---|---|---|
| A-01 | **Django `is_staff` was still an authority.** The Phase 2/3 organization-scoped routes (`/orgs/<slug>/commercial/*`, `/orgs/<slug>/integrations/*`) required a role permission plus `User.is_staff`. The owner role's `*` carries `commercial.catalog.manage`, `commercial.pricing.manage` and every `platform.integration.*` permission, so any staff identity that owned *any* organization - one it created itself - could change the global catalog and price books and register product integrations, change their redirect URIs and mint their credentials, **with no `StaffGrant`**. Revoking every grant did not remove it. Proven: a staff identity with no grant got 201/201/200; one whose grant was revoked still got 201. This was also the material divergence in D-022. | `require_platform_staff` reads the actor's live, uncached `StaffGrant`s and requires the exercised permission among them (`staff_catalog.manage` → `commercial.catalog.manage`, ...). New `platform.staff_integration.view/manage` (manage is dangerous) put integration administration behind a grant. A role can never carry a control-plane key (filtered at compile time), and a staff grant can only be a `platform.staff_*` key. |
| A-02 | **Staff grants could not expire.** The docs and R7-2's mitigation promised "optional expiry"; `StaffGrant` had no such column. | `StaffGrant.expires_at` (migration `authz.0006`), checked on every read like revocation; frozen by trigger (`tenancy.0007`); re-granting replaces an expired grant; UI and API accept an optional expiry. The "never zero security administrators" rule now counts **permanent** holders, so a platform cannot be left on a timer. |
| A-03 | **Commission on free trials.** Accrual ran on every priced line at the moment it was sold, trials included, and nothing reversed it when the trial lapsed (expiry sets no `cancelled_at`). A partner could provision a customer, start a trial and be paid for a sale the platform itself knew never happened. | A line earns only once its subscription is **activated** (`Subscription.activated_at`), and earns as of that moment: attribution, capability, rule and holdback all run from the conversion. A lapsed trial never earns. |
| A-04 | **A reversed commission could still be paid.** Approval re-derived totals only from the payout's own lines, so a refund or chargeback recorded between preparation and approval was invisible and the stale batch was approved in full. | `reconcile_payout` reports `entry_reversed_after_preparation`; approval refuses until the payout is cancelled and prepared again (which nets the reversal). |
| A-05 | **Reseller product scope was enforced on two of four selling paths.** `sell` and `change-plan` checked the product grant; `change-quantity` and `activate` (trial conversion) did not, so a partner holding only `subscription.manage` could add seats of - or convert a trial of - a product it was never approved for, or whose grant had been revoked, and earn on it. | Both check every affected line against the partner's live product grants, inside the transaction and under the link lock. |
| A-06 | **Partner reach outlived the relationship.** The partner's customer list returned *ended* links with the customer's **current** name, status, subscription status and products - indefinitely, including what another partner sold after the move. | Ended relationships are history: the list keeps the organization id and the dates and returns no current customer data. The portal shows "Former customer" with no actions. |
| A-07 | **D-020 reclassified from P2 to P1 (go-live gate, open).** A partner-provisioned owner with a new address cannot sign in, and nothing ever reaches them. See below. | Not built in this audit (it is a new identity flow needing its own review); documented as a go-live blocker with the design to close it. |

### P2 - fixed

| ID | Finding | Fix |
|---|---|---|
| A-08 | Separation of duties covered the payout's **preparer** but not the **author of a manual ledger entry** in it: one person holding `commission.manage` and `payout.approve` could record an adjustment and approve its payment, with preparation by a colleague being routine. | Approval refuses an approver who recorded any manual entry the payout contains. |
| A-09 | **Partner diagnostics was an account directory.** For any address, the chain said whether a Workeasy360 account existed anywhere on the platform and whether it was locked out, before checking membership of the partner's customer. | For a partner, an address that is not a member of that customer gets one answer (`not_a_member`), identical to an unknown address. |
| A-10 | Revoking `domain.manage` or `branding.manage` did not take a live custom domain off air (and the partner could then no longer disable it); suspending the partner left it served for up to the host-cache lifetime. | Serving a host requires both capabilities live; revocation and suspension purge the partner's hosts from the cache immediately and again on commit. |
| A-11 | Re-granting an **expired** capability silently did nothing: the expired row still held the one "active" slot and was returned with a 201. | The expired grant is closed (never deleted) and a new one issued. |
| A-12 | The trivial self-dealing routes were open: a partner could provision a customer whose owner is its own member, and a partner member could convert their own referral. | Both refused (for current and former members). |
| A-13 | Commission owed to a **terminated** partner could never be paid: preparation required an active partner, contradicting "commission already earned remains owed". | A terminated partner can be settled; a suspended one stays held. |
| A-14 | A retried staff (or customer) subscription command that carried `expected_version` got **409** instead of its replay - the version had moved because of the original call. | A replay skips the version check. |
| A-15 | The public logo was `Cache-Control: public, max-age=300`, and for a signed-in browser the response also carries a refreshed session cookie (`SESSION_SAVE_EVERY_REQUEST`). | `private, max-age=300`. |
| A-16 | Unknown catalog/pricing ids posted to `/admin/catalog/*` raised `DoesNotExist` - a 500. | A 404 (`CommercialRecordNotFound`) for all twelve lookups. |
| A-17 | No supported way to make a **second** person a staff identity (the grant screen refuses non-staff accounts; only the one-time bootstrap set `is_staff`), and no one-step cut-off for a compromised staff member. | `manage.py w360_staff_identity --email ... --enable|--disable --reason ... --confirm`: existing account only, audited, refuses to remove the last permanent security administrator. `--disable` ends every grant's effect at once and keeps the history. |

### P3 - fixed

- A-18 The ledger's append-only trigger is a row trigger, which `TRUNCATE` never fires: the table owner could empty the ledger in one statement. A statement-level `BEFORE TRUNCATE` trigger now refuses it for every role (`tenancy.0007`); the test database disables it for its flush-based teardown only.
- A-19 The bootstrap command's "no administrator yet" check and its grant were not serialised; two operators could both succeed. Now under an advisory lock.
- A-20 Two same-key payout preparations at once: the loser got "nothing payable" instead of the replay. The key is now checked under the partner lock (real-thread test in `test_phase7_concurrency.py`).
- A-21 Display strings (brand, partner, reason) accepted Unicode bidi overrides/isolates and line separators. Refused.
- A-22 `SecurityHeadersMiddleware` sat *innermost*, so tenancy's own 404/402 denials left without CSP or `no-store`, and `no-store` covered four prefixes only (`/auth/session` had none). It is now outermost and every `/api/` response defaults to `no-store` unless the view sets a policy.
- A-23 The domain re-check (up to 200 DNS lookups) ran on the `default` queue beside subscription due-processing; moved to `heavy`.
- A-24 A failed sign-out navigated to `/login`, which resumed the still-live session. The shells now say so and stay put.
- A-25 A partner accent such as `#ffffff` made the sign-in button's label unreadable. The label colour is derived (WCAG contrast), never partner-supplied.
- A-26 Documentation that disagreed with the code: D-020's stated cause, the known-debt claim that failed DNS lookups count as misses, the domain re-check queue, the middleware-order comment, the branding hook's Host comment.
- A-27 Found by the keyboard-only browser pass: after an action that ran through the reason prompt or the MFA step-up (both unmount on submit), **focus fell to `<body>`**, sending a keyboard or screen-reader user back to the top of the page. `useAction` now focuses the announced outcome in exactly that case.

### P3 - open (recorded)

- Orphaned logo uploads are not swept. Stays P3: an unattached upload is never publicly reachable (the logo endpoint serves only an active brand's file on an active domain) and it holds the partner's own artwork, not customer data.
- Customer/partner text search is an unindexed scan. Measured (below); stays P3.
- Replaying a staff subscription command appends a second control-plane audit row; an entitlement override POST has no idempotency key (a retry makes a second, revocable grant).
- An expired owner invitation's `expired` status write is rolled back with the 404 (readers filter on `expires_at`, so access is unaffected).
- Provisioning answers 400 for a disabled/locked account's address (a noisy existence oracle: each other probe creates an organization, 20/min).
- The ledger/payout page selectors apply the partner filter only when given one; no partner route can omit it today.
- Partner-portal viewers see manage buttons that the server refuses.
- The draft-then-publish legacy catalog route can leave a draft behind in a race (D-016, accepted).
- `audit_event` has the same owner-TRUNCATE gap as the ledger had (pre-Phase 7; same fix applies).
- A suspended partner (or a suspended link) keeps accruing commission on its customers' own purchases; payouts stay held. A policy choice, now stated.
- An *immediate* downgrade made during a trial still earns, once the trial converts, on the plan it replaced (the replaced line earns in full and the downgrade earns nothing). Pre-existing and unchanged by A-03; an upgrade during a trial totals correctly (the replaced line plus the increase). The exact rule - a line replaced before activation never earned, so its continuation is a first sale - is a small change to `evaluate_sale` when it matters.

## D-020: the partner-provisioned owner

**Classification: P1, go-live blocker for the reseller channel's new-customer
journey.** It is not a defect in the control plane, and it blocks no Phase 8
engineering; it blocks resellers onboarding customers whose owners do not
already have an account.

The actual journey today:

1. The partner (capability `customer.provision`, AAL 2) creates the customer
   with the owner's address.
2. For a new address the platform creates an identity with an **unusable
   password and an unverified address**, the organization, a *pending* owner
   membership, the partner link and a 14-day ownership invitation. No email is
   sent - nothing wires the invitation to the notification service.
3. The partner UI says the owner must set up sign-in; there is nothing to set
   it up with: no sign-up, no password reset, no invitation link, no claim flow.
4. The owner cannot sign in; after 14 days the invitation expires and there is
   no way to re-issue it. The partner can still sell to the organization.

Only an address that already has a working account completes the journey (sign
in, accept at `/organizations/select`). An operator can unblock one owner by
hand (`manage.py changepassword`, then an out-of-band hand-over) - a
workaround, not a secure flow. The earlier reason for deferring ("no outbound
email capability") was wrong: `platform/notifications` has an SMTP email
channel.

To close it: a credential-claim token that is random (≥ 256 bits), stored only
as a hash, single-use, expiring with the invitation, bound to that invitation's
user **and** organization, issued only for an identity with no credential,
delivered by the email channel, and redeemed on a page that reads it from the
URL **fragment** (so it never reaches a server log or a `Referer`); redemption
sets the password once, marks the address verified and accepts the invitation
in one transaction, and a second redemption is a no-op.

Invitation acceptance as it exists (an existing account) was re-checked: it is
bound to the authenticated identity (no token to leak or replay), single-use
(the row is locked; a second acceptance returns the first result), expiring,
and per organization.

## Answers the brief asked for explicitly

**Self-dealing safe enough for Phase 7 freeze: YES, after A-03, A-04, A-08 and
A-12.** Before the audit the answer was no: the platform paid commission on
trials it knew never converted, could pay a commission refunded after the
batch was prepared, let the author of a manual entry approve its payment, and
let a partner own or refer its own customers. What remains is a partner using
a second, unrelated identity as a paying-looking customer. With no payment
provider, the platform cannot know whether that customer paid; the control is
the two-person, AAL 2 payout review, which must confirm customer payment
(runbook, below). The smallest control to add when payouts are automated: a
commission clears only on a verified customer payment event.

**Dual approval: still NO.** Payouts are manual, low volume, and now refuse:
the preparer, the author of any manual entry in the batch, a stale batch, a
suspended partner, a total that does not reconcile. The triggers in ADR-029
(automated execution, a threshold, approver availability) are unchanged and
none is met.

**Compromised staff account (R7-2): controls sufficient for Phase 7.** After
A-01/A-02/A-17 a staff account can do exactly what its grants allow, grants
can be time-boxed, revocation and `--disable` apply on the next request, every
dangerous action needs AAL 2, a security administrator can grant only to
identities an operator designated (never to a fresh attacker account), and
payouts need two people who did not author the money.

**Impersonation:** none exists. `TenantContext.is_break_glass` is a hook that
no production code path sets; `check()` blocks dangerous permissions under it.

**Custom-domain phishing (R7-3), residual risk:** the platform does not serve
partner domains yet (routing and TLS are infrastructure). Ownership proof,
uniqueness, re-verification, capability-gated serving and canonicalisation are
correct and now tested against a real DNS server. Before any infrastructure
routes a partner domain to the platform, these are **go-live gates**: (1) the
`/admin` and `/partner` areas are served only on canonical platform hosts;
(2) sign-in happens on a canonical host, or sessions are bound to the host
that created them - a cookie issued on a domain the partner controls is
readable by that partner the moment it repoints DNS; (3) certificates are
issued only for hostnames that are active in this table (an on-demand-TLS
"ask" check), and revoked when they are not.

## Evidence

- Probes-then-regressions: `test_opus_final_audit.py` (attacks above),
  `test_dns_resolver_integration.py` (a real UDP DNS server: single, multiple
  and split TXT records, NXDOMAIN, empty answer, SERVFAIL, silence, no caching,
  and the full request → verify → activate → lose-control → disable lifecycle
  through the production resolver), `test_final_audit_search_scale.py`
  (directory search at 100/500/5,000 rows), a same-key payout race in
  `test_phase7_concurrency.py`, the operator command in
  `test_bootstrap_staff.py`, and frontend tests for the ended-relationship row,
  the derived label colour and a failed sign-out.
- The fresh-database and Phase 6 → 7 upgrade rehearsal now also proves the
  owner cannot `TRUNCATE` the ledger and that a staff grant's expiry cannot be
  extended in place (55 of 55 checks, including rollback and re-apply of both
  new migrations).
- Real browser, production build (`scripts/phase7_browser_check/`): 20 pages,
  0 CSP violations, 0 console errors, 0 axe violations (incl. contrast), every
  tab stop visibly focused, no horizontal scroll at 320px; the new
  `keyboard_check.js` operates sign-in, navigation, payout approval, a
  time-boxed staff grant, a commission rule and a custom-domain request with
  Tab/typing/Enter only, through the real MFA step-up: 28 of 28 after A-27.
- Two new fitness tests: every production reader of Django's `is_staff` is on
  a reviewed allowlist (A-01 cannot quietly return), and the no-partner-type
  rule now covers the admin/partner app routes, the API client and the
  backend partner, commission and control-plane packages.

Not executed: a manual screen-reader pass (needs a person and assistive
technology); real registrar DNS (the resolver is exercised against a real DNS
server on a local port instead); a load test.

## Payout review runbook (addition)

Before approving a payout, the approver confirms from the finance system that
each customer whose sale it pays has actually paid. The approval screen shows
the batch; the commission list (filter by partner) shows each entry's customer.
Reversals recorded after preparation now block approval - cancel and prepare
again.
