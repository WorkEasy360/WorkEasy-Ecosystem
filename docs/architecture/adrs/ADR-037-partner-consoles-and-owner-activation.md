# ADR-037: Reseller and white-label are derived views of one partner account; a customer owner claims their account with a one-time link

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** Architecture, Security, Commercial
**Depends on:** [ADR-003](ADR-003-shared-identity.md), [ADR-027](ADR-027-control-plane-principals.md), [ADR-029](ADR-029-commission-ledger-and-payout-model.md), [ADR-036](ADR-036-self-service-accounts-and-staff-roles.md)
**Closes:** debt D-020 (a partner-provisioned customer owner could not claim their account)

---

## Context

Phase 7 built the partner channel (accounts, capability grants, product grants, links, brand, domains, commission and payouts) and the staff and partner APIs for it. Phase 10 adds the staff *consoles* for it - a partners overview, a reseller list and wizard, a reseller detail, and a white-label list and wizard - and closes the one journey the channel could not complete: a customer provisioned for a **new** email address received nothing and could never sign in (D-020, a P1 launch blocker).

The brief froze the architecture. Reseller and white-label are **user-experience concepts**: no `partner.type`, no `ResellerPartner`, no `WhiteLabelPartner`, and any combination of them on one account. There is no second identity, pricing, entitlement or commission system, and no new brand or domain model.

## Decision

### 1. A label is a function of the capabilities held, computed in one place

`partners/capabilities.py` maps capability keys to three labels (`reseller`, `white_label`, `affiliate`) in a single table (`EXPERIENCES`) and one function (`experience_labels`). The API returns the labels; the console renders them and never re-derives them. A list of "resellers" is the same `PartnerAccount` query with an `EXISTS` over effective grants; "white-label" is the same query over two other capabilities. Granting or revoking a capability changes the label at once, on the next read, and there is no combination a type could forbid. A test enumerates all eight subsets of the three labels.

The Add Reseller wizard offers a **Standard Reseller preset** that maps to existing capability keys (`CAPABILITY_PRESETS`), grouped for people (`CAPABILITY_GROUPS`), with the keys visible in an advanced view. It creates a partner with those grants - nothing more.

### 2. Each table's owner answers reads about it; the control plane composes

`commercial/controlplane` sits at the top of the commercial layering, and the consoles read across partners, commissions, pricing and provisioning. It does **not** open their tables (architecture rule F-6). Each owner exposes what it knows through its own selectors - `partners/console_reads.py`, `commissions/selectors.py`, `pricing/selectors.py`, `provisioning/selectors.py` - in a fixed number of grouped queries for exactly the partners on a page, and `controlplane/partner_console.py` and `partner_detail.py` authorize, validate filters and assemble. Lazy subqueries (organization ids matching a subscription state) pass between selectors so a filter across modules stays one statement. A measurement at 10, 100 and 500 partners shows the same query count for every console read (`tests/phase10/test_10f_performance.py`).

This replaces an earlier draft of the design that put the aggregate queries in `controlplane` itself; that draft failed `test_no_cross_module_orm_access` and was refactored.

### 3. Partner commission: the relationship existed; the use case did not

`CommissionRule.partner` and the precedence in `select_rule` were already in the schema. What was missing was a *command*. `commissions.rules.assign_partner_commission` is a thin, idempotent use case over the existing `create_rule_version`:

- **Version-safe**: the caller passes the version of the live rule it read (`0` for none); a stale token is refused, never merged. The first assignment for a (partner, product) pair is serialised by a transaction-scoped advisory lock, and the effective-dated clock is read *after* taking it.
- **Effective-dated, never retroactive**: the new version starts now (or later), the previous one is retired at that instant, and accrued sales keep the rule, version and rate stamped on their ledger row (the ledger is append-only by trigger and is not written here).
- **Dangerous** (AAL2), permission-protected (`platform.staff_commission.manage`), audited with the previous rate, and idempotent on retry.

No new table or engine. The wizard and the detail's Products & Pricing tab both call it; the browser computes no money (`price_figures` and `domain.commission_amount` do, in integers, half-up).

### 4. The partner administrator is a `PartnerMember` on the existing identity

The wizard's sixth step invites an administrator by email. If the address has no account, `ensure_identity_for_email` creates an uncredentialed `User`, and an existing `AccountToken` (reset-password kind, hashed, single-use, latest-wins) is emailed in a sealed notification; an existing account is simply attached. There is no `PartnerUser`.

### 5. Customer owner activation (closing D-020)

`partners/activation.py`, over the existing `PartnerOwnerInvite`, the identity lifecycle and the notification path:

- **The link** is `<organization id (hex)>.<256 random bits>` in the URL **fragment** of `/owner-invitations/accept`. The organization prefix binds it to one organization; a redemption checks it against the row it finds, and the hash decides whether anything is there.
- **At rest only the SHA-256** is stored (`token_hash`, partial unique index). The token is carried in the sealed notification context (wiped once delivered) and the fragment (which browsers never send to a server), and by POST bodies on the two public calls. It appears in no URL a server sees, no log, no audit entry, no API response and no console screen; the console shows only the state (pending, activated, expired, revoked) and dates.
- **Single use, expiring, race-safe**: claiming locks the invitation row, clears the hash on success, and spends it; two browsers presenting one link produce one owner and one refusal. `expires_at` is fourteen days by default. A resend replaces the link (the old one dies) and can revive an expired, withdrawn or declined invitation; withdrawing stops it at once. Every failure to redeem is one uniform `invitation_invalid`.
- **Who may claim**: the holder of a valid link who chooses a password (and a name if none is on record); a **signed-in visitor must be the invited person** (else `invitation_email_mismatch`, nothing changes); an address that **already has an active account with a password** must sign in as it - holding a link never sets a password on an account that can already sign in, and no second account is made; a **pending self-signup** for the address (which someone else may have registered) is completed with the *invitee's* password and name, which defeats pre-registration.
- **Existing rules, not new ones**: the password policy is `check_new_password`; the hash is written by `identity.lifecycle.complete_with_password`; ownership is `activate_pending_owner`; the email is `notify_account`. No password is ever emailed.
- **Sending and withdrawing** belong to the provisioning partner (`platform.partner_customer.provision`, dangerous, on a live link with an active partner) and to staff (`platform.staff_partner.manage`, dangerous); both are audited, rate-limited and refused once the owner has activated.
- **Customer independence**: nothing in the claim reads the partner relationship. Ending the link does not disable the owner's sign-in, delete the organization or remove a subscription or entitlement, and an unclaimed link still works after the partner has gone - what is being claimed is the customer's own organization.

`PartnerOwnerInvite` gained `token_hash`, `send_count`, `last_sent_at`, `revoked_at` and a `revoked` status in one additive, reversible migration (`partners.0003`). Rows from earlier phases have no hash and so cannot be claimed until a link is sent.

### 6. White-label administration reuses the partner's own commands

Branding and domains have one implementation. Staff preparing a partner's brand or domain call the same functions with an explicit `partner_id`, resolved by `access.subject_partner` (a partner principal may name only its own partner; staff need `platform.staff_partner.manage` and may not act on a terminated partner). The domain lifecycle stays `requested -> verification_pending -> verified -> active` and is never automatic. Branding is not a dangerous permission and a domain is, so they are separate submits: a partner is asked for a second factor on the domain step and never to change a colour.

### 7. What a partner can do follows its status, and the console says so

`partners/resolver.py` already defines the permission set by status (active: everything its capabilities unlock; suspended: read-only; terminated: commission and payout visibility only; draft: nothing). Terminating a partner ends its links and disables its domains in the same transaction and touches no customer. The portal's navigation and banner now match what the server will answer, instead of offering pages that can only refuse.

## Consequences

- One `PartnerAccount` can be a reseller, a white-label partner, both, or neither, and only its capabilities say which.
- The reseller channel's new-customer journey is complete: provision, email, claim, sign in, sell, use the product - proved in a real browser against the production build.
- Adding a fourth label is one table entry. Adding a partner *type* would be a schema change that architecture tests refuse.
- The console's aggregate reads are O(partners) in time for the overview (constant in queries); a much larger partner count would want a maintained projection. Measured, not claimed: see the Phase 10 status document.
- `is_plausible_email` now applies the model's own validator, so an address the model would refuse is refused up front instead of raising after the check (found by the Phase 10 security tests; it affected invitations, sign-up and partner provisioning).

## Alternatives considered

- **A partner `type` column** - rejected by the brief; it would forbid the combined partner and turn a label into schema.
- **Aggregate queries inside `controlplane`** - the first design; fails F-6, and welds the console to three modules' schemas.
- **A second invitation/credential system for owners** - rejected; the existing hashed-token, sealed-notification and identity-lifecycle pieces cover every requirement.
- **Auto-accepting after the password is set for an existing account** - rejected; taking ownership is a deliberate step, so the signed-in invitee presses one button.
