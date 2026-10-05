# Phase 10 - Reseller and white-label administration (design freeze)

Status: **complete** (2026-09-28; results in [phase-10-status.md](phase-10-status.md)). Brief: "Reseller + White-label Admin
Implementation" (10a-10f, in that order). D-020 is **closed in this release**
(it was deferred to the reseller launch in Phase 9; the owner confirmed it as a P1
launch blocker) - and it is: see debt register D-020 (closed) and ADR-037.

## Frozen: what is reused, and what is forbidden

Reused as-is: `PartnerAccount`, `PartnerCapabilityGrant`, `PartnerOrganizationLink`,
`PartnerMember`, `PartnerProductGrant`, `PriceBook`/`PlanPrice` (+ `resolve_price`),
`CommissionRule`, the append-only `CommissionAccrual` ledger, `Payout`/`PayoutLine`,
`PartnerBrand`, `PartnerDomain`, `PartnerOwnerInvite`, the provisioning service, the
entitlement writer, `authz.check`, AAL2 step-up, the audit log, the Files platform,
`identity` (User, AccountToken, lifecycle), `notifications`.

Forbidden (pinned by `backend/tests/phase10/test_10f_architecture.py`): a partner
`type`, `ResellerPartner`/`WhiteLabelPartner`/`AffiliatePartner`, a second identity
table, a second pricing engine, a second entitlement writer, a second commission
ledger/engine, new branding/domain models, any product-domain import.

**Reseller / White-label / Affiliate are derived labels.** One pure function in
`commercial/partners/capabilities.py` (`experience_labels`) computes them from the
*held capability keys*; the API returns the result and the frontend only renders it.

## Module placement (import-linter layers, architecture rule F-6)

`commissions` and `partners` cannot import upward, and the console reads span
partners + commissions + provisioning + pricing. `commercial/controlplane`
(`partner_console.py`, `partner_detail.py`, `reseller_setup.py`) is the top layer and
**composes**; it does not open other modules' tables. Each owner answers reads about
its own tables through its selectors - `partners/console_reads.py`,
`commissions/selectors.py`, `pricing/selectors.py`, `provisioning/selectors.py` - for
exactly the partners on a page, in a fixed number of grouped queries. (The first
draft put the queries in `controlplane` itself; it failed
`test_no_cross_module_orm_access` and was refactored. See ADR-037.) Commands stay in
their owning module.

## 10a - overview and reseller list

- `GET /admin/partners/overview` - factual counts (`platform.staff_partner.view`).
  No MRR/ARR/growth. Cards: active partners, active resellers, white-label enabled,
  partner customers (live links), active partner subscriptions (projection), pending
  commissions (ledger entries not yet in a live payout - per currency, unconverted),
  pending payouts (draft/ready/approved), domains awaiting verification.
- `GET /admin/partners/resellers` - keyset page. Filters: `q`, `status`, `product`,
  `capability`, `scope` (`resellers` default | `all`, so a half-set-up partner is
  never invisible). A fixed number of queries whatever the page size.
- Navigation: a "Partners" group in `adminNav` (Overview, Resellers, White-label,
  Customers, Products & Pricing, Commissions, Payouts). Commissions/Payouts moved out
  of "Billing" and keep their existing pages.

## 10b - Add Reseller wizard

Front-end orchestrated over the existing commands with `useStepRunner`/`ProgressList`
(each step is idempotent and resumable), plus **one dry-run endpoint**:

- `POST /admin/partners/reseller-review` - validates the whole draft with the real
  validators and returns server-computed money (list price, partner price, discount,
  commission per sale, platform net). Writes nothing. The browser computes no money.
- Capability groups + the "Standard Reseller" preset are served by
  `GET /admin/partners/capabilities` (`group`, `groups`, `presets`), defined once in
  `capabilities.py`. A preset is a list of existing keys; nothing is created.
- Products: existing `grant_product`. Pricing: existing partner `PriceBook` +
  `publish_price`. Nothing is duplicated.
- **Commission assignment.** Verification found the relationship already exists in the
  schema (`CommissionRule.partner`, precedence in `domain.select_rule`, a frozen-column
  trigger, an append-only accrual that snapshots rule/version/rate). What was missing
  is the *use case*: `commissions.rules.assign_partner_commission` (+ read
  `partner_commission_state`). It finds the partner's rule chain by (partner, product)
  - never by a naming convention - or starts one, then appends a version through the
  existing `create_rule_version` (advisory lock, effective-dated, no backdating,
  retires the predecessor, audited, `platform.staff_commission.manage` = dangerous =
  AAL2). Optimistic `expected_version`. Old accruals keep their snapshot; nothing is
  attached retroactively. No new table, no new engine.
- Partner administrator: `PartnerMember` + the existing identity path
  (`ensure_identity_for_email` -> uncredentialed User) and the existing
  `AccountToken` reset-password link (hash stored, single use, expiring, latest wins),
  with a dedicated email template. No `PartnerUser`.

## 10c - reseller detail

Tabs (`Tabs<K>`): Overview, Customers, Products & Pricing, Subscriptions,
Commissions, Payouts, Branding, Domains, Users & Permissions, Activity. Existing
sections move into their tabs. New reads: `GET /admin/partners/<id>/summary`,
`/customers`, `/commission` (rules), `/ledger`, `/payouts`(existing list filtered),
`/activity` (existing `audit_page` extended with generic `resource_ids`/`change_ref`
filters - no partner-specific audit storage). No new subscription mutation logic:
subscription operations remain the existing staff/partner commercial services.

## 10d - white-label

- `GET /admin/partners/white-label` - filtered `PartnerAccount` view (effective
  `branding.manage` or `domain.manage`).
- Staff-assisted brand/logo/domain commands reuse the *same service functions*: each
  takes an explicit `partner_id` when the caller is a STAFF principal
  (`platform.staff_partner.manage`, dangerous). The partner path is unchanged.
- One `WhiteLabelWizard` component over an adapter (partner API or staff API); every
  step is its own action through `useAction`, so step-up happens at the step that needs
  it (domain), never inside a combined submit. Activation is never automatic.

## 10e - D-020 (customer-owner activation)

`PartnerOwnerInvite` gains `token_hash`, `send_count`, `last_sent_at`, `revoked_at`
and a `revoked` status (migration `partners.0003`). Token = `<org hex>.<256 random
bits>`; only the SHA-256 is stored; delivered in the URL fragment through the sealed
notification context. Public preview/accept (POST bodies, rate-limited) mirror
`platform.administration.invitations`: the row is locked, single use, expiring,
bound to user + organization; a person named without a usable password sets one
(`lifecycle.complete_with_password`, password policy applied); an existing account
with a password must sign in as that account. Acceptance = the existing
`activate_pending_owner`. Resend (new token, old link dies) and revoke for staff and
for the provisioning partner. The customer tab shows factual status only.

## 10f - gates

Security matrix, architecture tests, five browser journeys, five viewports, axe,
keyboard, query-count measurements at 10/100/500, full backend and frontend gates.
