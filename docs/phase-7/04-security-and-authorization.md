# Security and authorization

## The decision, in one place

Every control-plane request is decided by `platform.authz.check`, the same
function organization requests use ([ADR-027](../architecture/adrs/ADR-027-control-plane-principals.md)).
What differs is the principal and how its permission set is resolved:

| Principal | Permission set comes from | Scope the request runs in |
|---|---|---|
| member | role assignments in one organization | that organization |
| staff | live `StaffGrant` rows (no cache) | the platform organization, or **one** customer via `organization_scope` |
| partner user | partner status × granted capabilities × member role | the partner; a customer only through a live link |

`dangerous` permissions require AAL 2. A missing step-up is 401
`step_up_required`; the UI runs the MFA challenge and retries.

The complete set of dangerous control-plane permissions is pinned by a test
(`test_the_dangerous_permissions_are_exactly_the_pinned_set`), so downgrading one
is a visible decision:

- **Staff:** `staff_customer.manage`, `staff_catalog.manage`,
  `staff_pricing.manage`, `staff_subscription.manage`, `staff_partner.manage`,
  `staff_commission.manage`, `staff_payout.manage`, `staff_payout.approve`,
  `staff_security.manage`, `staff_integration.manage` (added by the final
  audit, when product-integration administration moved off Django's
  `is_staff` onto a grant).
- **Partner:** `partner_domain.manage` (a domain serves the platform's own pages),
  and `partner_customer.provision`, `partner_customer.manage`,
  `partner_subscription.sell`, `partner_subscription.manage` (creating a customer
  and selling it a plan earns the partner commission, so a stolen session could
  otherwise manufacture it silently).

No `.view` permission is dangerous: an AAL 2 prompt on a read trains people to
approve prompts.

**Partner authorization is an intersection** - active partner, live link (for a
customer route), granted and unexpired capability, and a sufficient member role.
Removing any one removes access on the next request. A terminated partner keeps
only the two history-view permissions.

## Failure statuses

| Situation | Response |
|---|---|
| Not signed in | 401 `login_required` |
| Recent second factor needed | 401 `step_up_required` |
| Signed in, lacks permission | 403 |
| Partner not active | 403 `partner_not_active` |
| A customer/partner/domain/payout id the caller may not see, or that does not exist | **404** (identical) |
| Stale version | 409 |
| Validation | 400, with the field |
| Rate limited | 429 |

The 404-for-unseen rule is what stops ids being an oracle: a partner probing
another partner's customer, domain, accrual or payout id learns nothing
(tested per resource).

## Rules that are enforced, not conventional

- **One staff authority.** Every control-plane write - including the older
  organization-scoped catalog, price-book and integration-registry routes -
  requires a live `StaffGrant` conferring the exercised permission
  (`require_platform_staff`). Django's `is_staff` is a precondition an operator
  sets, never an authority; a customer role can never carry a control-plane
  key (filtered at compile time); a staff grant can only be a
  `platform.staff_*` key.

- **No client authority.** Request bodies carry selections (a plan-version id, a
  capability key, a reason, an `expected_version`) - never a price, a commission
  amount, a rate, an approver, a status, a partner id (taken from the URL and
  re-checked against membership) or an organization the caller was not shown.
  Mass-assignment tests post forbidden fields and assert they are ignored or
  refused.
- **Separation of duties.** The preparer of a payout cannot approve it: checked
  in the service and refused by a database constraint.
- **Grants require reasons; staff actions are audited** through the existing
  `AuditEvent` service (`actor_kind = staff`, membership null,
  `actor_partner_id` recorded for partner actions), including refusals.
- **Append-only where it matters.** The commission ledger cannot be updated or
  deleted, by the application role or by a trigger for anyone else. Frozen
  columns on subscription items, payout lines and rule rows are guarded by
  triggers (`freeze_columns`).
- **RLS is never relaxed.** Control-plane code enters an explicit scope; the
  five exempt tables (D-019) are each read only through a verified principal.
- **Webhooks and events carry ids only.** `commercial.partner.*`,
  `commercial.commission.*`, `commercial.payout.*` payloads contain identifiers
  and states, never amounts' counterparties, emails, or brand content.

## Abuse limits

Per-user and per-IP throttles (`CONTROL_PLANE_RATE_LIMITS`, environment
overridable) protect the endpoints an outsider or a compromised partner could
hammer: customer provisioning (20/min/user), domain verification (10/min/user -
each is a DNS query), support diagnostics (30/min/user), public branding
(120/min/IP), public referral capture (20/min/IP) and referral conversion
(10/min/user). The limiter degrades to a per-process counter if the shared
cache is unreachable (D-012), which is a capacity change, never an
authorization one. Limiter scope names are a fixed set, so metric labels stay
bounded; partner ids, hostnames, referral codes and payout ids are **forbidden
metric labels** (enforced in `platform/observability/metrics.py`).

## Domain and branding safety

- DNS proof is required before a hostname does anything; verified is not active.
- The platform's own hostnames and subdomains cannot be claimed. Several
  partners may request a hostname but only one can reach verified/active (partial
  unique index, race-tested; the loser gets `hostname_claimed`).
- Branding text is escaped by React; colour is a validated six-digit hex applied
  through the CSSOM; the logo is a scanned PNG/JPEG (SVG refused) served from the
  platform's own origin, so `img-src` stays `'self' data:`.
- The public branding endpoints resolve from a canonicalised `host` parameter
  only (never a file id, and never the request's own host headers), match active
  verified domains only, and are rate limited. The logo response carries
  `nosniff` and `default-src 'none'; sandbox`.

## Support diagnostics

Read-only by construction (the service has no write path other than the audit
entry), authorized like any other action, rate limited, and it reports which step
fails - never the underlying secret, token or another tenant's data. A partner
can diagnose only customers it is linked to, and learns nothing about an
address that is not a member of that customer: an unknown address and another
tenant's account get the same `not_a_member` (final audit A-09).

## What this phase does not protect against

- A compromised **staff account with AAL 2** can do what its grants allow. The
  mitigations are grants with reasons and (optional) expiry, instant revocation
  and the operator's one-step `w360_staff_identity --disable`, the audit trail,
  separation of duties on payouts (preparer and manual-entry authors cannot
  approve), grants only to operator-designated staff identities, and the
  last-permanent-security-administrator lock.
- Payment is manual; a person recording `paid` is trusted to have paid. The
  reference is stored for reconciliation against the real bank statement.
