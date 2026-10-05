# ADR-028: A partner is what it has been granted, not what it is called

**Status:** Accepted
**Date:** 2026-09-24
**Deciders:** Architecture
**Depends on:** [ADR-027](ADR-027-control-plane-principals.md)

---

## Context

The business has resellers, white-label partners and affiliates, and will have
combinations (a reseller that is also an affiliate; a referral partner that
later resells). The obvious model is a `type` column with an `if
type == "reseller"` in every screen and service. That makes every combination a
new type and every new behaviour a change to every `if`.

## Decision

**`PartnerAccount` has no type.** A partner's powers are a set of
**capabilities** - `PartnerCapabilityGrant` rows (`customer.provision`,
`customer.manage`, `subscription.sell`, `subscription.manage`, `pricing.view`,
`branding.manage`, `domain.manage`, `commission.earn`, `commission.view`,
`payout.view`, `support.access`, `referral.create`, `referral.view`).
"Reseller", "white-label" and "affiliate" are names for *combinations*, never
stored.

- **One registry.** `commercial/partners/capabilities.py` declares every
  capability once, with the permissions it unlocks in two tiers: `view` (every
  active member) and `manage` (admins only). An unknown key is refused, so a
  request body cannot invent one. A test pins the capability strings the
  frontend uses to this registry.
- **Authorization is the intersection of three things:** an active
  *relationship* (partner status, and for a customer route a live link), a
  *capability* (granted, not expired or revoked), and a member *role* for the
  action. Removing any one removes the permission immediately (no cache).
- **Grants are history.** Revoking sets a status and end time; rows stay, so
  "did this partner hold `commission.earn` when that sale happened?" is
  answerable - and accrual asks exactly that.
- **Product scope is separate from capability.** `subscription.sell` says a
  partner *may sell*; `PartnerProductGrant` says *what*. A sale needs both.
- **Partner pricing uses the existing price books.** `PriceBook.partner_id`
  (a soft reference guarded by a check constraint) adds one step to
  `resolve_price`. No second pricing engine.
- **Provisioning is atomic and idempotent.** A partner-created customer
  (organization, pending owner, link, invitation, summary) commits together or
  not at all, keyed by the caller's idempotency key.
- **Customers outlive partners.** Terminating a partner ends its links; the
  customer's organization, subscriptions and entitlements are untouched.
  Commission on sales made before the end is still owed; nothing after accrues.
- **Money on screen is server-derived.** A partner's catalog view carries the
  resolved price; a sale body carries a plan-version id and a quantity, never an
  amount.

## Consequences

- Adding "distributor" is granting capabilities, with no schema or code change.
- The partner portal's navigation is derived from capabilities; there is no
  partner-type branch in the frontend (a test asserts partners with different
  grants differ only in what they are shown).
- Capability sprawl is the failure mode. Mitigation: the registry is small, each
  entry names its permissions, and every grant records a reason.

## Alternatives rejected

- **A `type` enum.** Rejected above.
- **Roles per partner type.** Conflates what the *partner* may do with what a
  *user* may do for the partner.
- **Per-partner permission rows.** Unbounded and unreviewable; the registry keeps
  the vocabulary reviewable in one file.
