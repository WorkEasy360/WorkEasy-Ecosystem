# Handoff for the independent audit

Read in this order. Each item says what would be catastrophic if it were wrong,
and where the test that would catch it lives.

## 1. The scope primitives (highest blast radius)

`backend/workeasy360/platform/authz/control_plane.py`: `organization_scope`,
`acting_scope`, `ensure_scope`, `@in_scope`, `platform_context`. If one of these
leaks a scope past its transaction, or enters the wrong organization, RLS stops
protecting a customer. It is the one new module on the `.unscoped` allowlist.
*Try to break:* a service decorated `@in_scope` called from a request whose
context names another organization; a scope left set after an exception.

## 2. Partner-to-customer reachability

`commercial/partners/access.py` (`lock_active_link`) and every route in
`interfaces/api/v1/partner_views.py` that takes an `organization_id`. The claim:
*no* path reaches a customer except through a live link, and every miss is 404.
*Try to break:* a partner id from the URL that the caller is not a member of; a
link that ended between the check and the write; an organization id that belongs
to another partner's customer.

## 3. Money

`commercial/commissions/accrual.py` (`evaluate_sale`, the idempotency key),
`domain.py` (`select_rule`), `payouts.py` (re-derivation before approval),
`ledger.py` (reversal cap), and migration
`platform/tenancy/migrations/0006_partner_channel_controls.py` (append-only and
freeze triggers). *Try to break:* accrue the same sale from two processes; reverse
an accrual beyond its amount; approve a payout whose lines were altered; update a
ledger row as the migrator role.

## 4. Authorization of dangerous actions

`platform/authz/permissions.py` (`STAFF_PERMISSIONS`, the `implies` chains),
`platform/authz/staff.py` (`create_grant`, `end_grant`), and the per-endpoint AAL
test matrix in `tests/phase7/`. *Try to break:* call each dangerous endpoint at
AAL 1; use a permission after its grant was revoked; remove the last security
administrator from two sessions at once.

## 5. Code from earlier phases that this phase changed

Phase 7 touched Phase 2-6 code, so regressions are possible there even though the
full suite passes:

- `commercial/pricing` (`PriceBook.partner_id`, `resolve_price`; migration 0002
  contains a `RunPython` that retires orphan partner books),
- `commercial/subscriptions` (`suspend`/`resume`/`change_quantity`, `sale_facts`),
- `commercial/provisioning` (`_compute` refactor, `CustomerSummary`),
- `platform/audit` (actor kinds; membership null for control-plane principals),
- `integration/events` (actor kind; outbox backlog selector),
- `platform/authz`, `platform/organizations`, `platform/onboarding`, `platform/files`,
- `contracts/values/tenant.py` (`TenantContext` gained fields with validation).

`git diff` against the Phase 6 commit shows exactly these.

## 6. Public and semi-public surface

`interfaces/api/v1/public_views.py`: `public/branding` (resolves brand from
`Host`), `public/referrals`, `referrals/convert`, `invitations`. *Try to break:*
a forged `Host`/`X-Forwarded-Host`; a referral code enumeration; converting a
referral for an organization you do not own; accepting another user's invitation.

## 7. The frontend

`frontend/src/features/controlplane/`. *Try to break:* a brand colour that is not
a hex colour; an SVG logo; a screen that renders server text as HTML (none does);
any `style=`; a nav item that implies a permission the backend does not check.

## Found and fixed while verifying (so you know where it was thin)

Each was found by a cross-check rather than by a feature test, which is a hint
about where the next one might hide:

| Found by | Defect | Fix |
|---|---|---|
| Full suite | Two Phase 2 failure-mode tests reset the commercial change listeners and re-registered only provisioning, silently disabling commission accrual for every later test | Tests now restore the listeners they found |
| Full suite | `create_partner_price_book` wrote a partner's price book with no audit entry of its own | Audited in the same transaction |
| Frontend/API shape check | A newly requested domain (`requested`) was never offered "Check DNS" - only `verification_pending` was | UI and test corrected |
| Request-flow test | Saving a brand without resending `logo_file_id` **cleared** the logo (a save replaces the brand) | The screen resends the current logo id |
| Client-reference test | `quantity: 0` was coerced to a sale of 1 (`0 or 1`), and an unbounded quantity (10^9) sold and then made reconciliation raise `DataError` | Explicit default; `MAX_QUANTITY = 100,000` in the Phase 2 service |
| Client-reference test | `bool("false")` is true: a string could enable a trial, mark a cancellation immediate or make a catalog item public (Phase 2 routes included) | One strict `_flag` helper for every boolean body field |
| Security-matrix review | Partner provisioning and selling did not require AAL 2 although they earn the partner commission | Made dangerous; set pinned by test |
| Performance plans | Customer directory, payout list and partner list had no index for their own ordering | Three keyset indexes |
| Frontend contract test | The partner-facing subscription view names ids `subscription_id`/`item_id`, the admin view `id` | Separate client type |
| Architecture test | The `/admin/support` route tripped the "no product-named path" rule | Renamed `/admin/diagnostics` |
| Acceptance-flow test | A partner holding only `subscription.sell` could not list what it may sell (the catalog needed `pricing.view`, a capability the brief's scenario does not grant) | Selling now carries the (non-dangerous) pricing view tier; the sale itself stays AAL 2 |
| Real browser | The same catalog read returned 401 for a partner with every capability once selling became dangerous (a read demanding a second factor) | Same fix; found only by loading the page |
| Real browser | 11 of 20 pages scrolled horizontally at a 320px viewport (grid items sized to their widest content; header would not shrink) | `minmax(0, 1fr)` grids, wrapping header |
| Writing the runbook | Nothing could create the *first* security administrator (the UI grants only to holders) | `w360_bootstrap_staff`: existing account, one-time, audited, refuses once an administrator exists |

## For your judgement

- **R7-1 partner self-dealing.** Nothing stops a partner creating a customer
  that never pays and earning commission on it, other than holdback, staff
  reversal and payout review. Whether that is enough is a business call.
- **D-020** a partner-provisioned owner has no way to set a credential.
- The `unscoped` allowlist gained one module
  (`platform/authz/control_plane.py`); its one use is creating the platform organization; check that it stays that narrow.

## What the tests do not prove

- Colour contrast, focus visibility and 320px reflow (need a browser).
- Behaviour under real network partitions between the application and PostgreSQL.
- That a customer's real DNS behaves like the swappable resolver's fakes.
- Load beyond the 500-row measurements in [07-performance.md](07-performance.md).
