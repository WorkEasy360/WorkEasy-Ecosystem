# Phase 7 — Super Admin control plane and the partner channel

**What Phase 7 adds:** the two actors that are not "a member of one
organization". **Workeasy360 staff** operate the platform across customers
(directory, lifecycle, catalog, subscriptions, entitlement overrides, support
diagnostics, audit). **Partners** - resellers, white-label operators and
affiliates - sell to, brand for and refer customers, and earn commission that
staff review and pay out **manually**.

Nothing here is a product. No product-domain route, table or screen was added
(asserted by `tests/architecture/test_phase7_architecture.py`), and no real
payment is made or attempted.

| Document | Covers |
|---|---|
| [01-super-admin.md](01-super-admin.md) | Staff permissions, the admin API and screens, customer lifecycle, subscription and entitlement administration, support diagnostics |
| [02-partners.md](02-partners.md) | The capability model in practice; reseller, white-label and affiliate foundations; provisioning; independence after termination; branding, domains, referrals |
| [03-commissions-and-payouts.md](03-commissions-and-payouts.md) | Rules, accrual, the append-only ledger, reversals, adjustments, payouts, reconciliation |
| [04-security-and-authorization.md](04-security-and-authorization.md) | The authorization model, AAL2, tenant isolation, abuse limits, the permission matrix |
| [05-concurrency-and-failure.md](05-concurrency-and-failure.md) | Races and how each is settled; what happens when a dependency fails |
| [06-frontend.md](06-frontend.md) | `/admin` and `/partner`, CSP, accessibility, branding safety |
| [07-performance.md](07-performance.md) | Measured query counts and timings at 10/100/500 rows |
| [08-known-debt.md](08-known-debt.md) | What is deliberately not done, and why |
| [09-opus-audit.md](09-opus-audit.md) | Handoff for the independent audit: where to look first |
| [10-retention-and-privacy.md](10-retention-and-privacy.md) | What a partner can and cannot learn about a customer; what is kept, for how long, and what removes it |
| [11-final-opus-audit.md](11-final-opus-audit.md) | The final adversarial audit: every finding, its proof and fix, the go-live gates (D-020, custom-domain serving) |
| [phase-7-status.md](phase-7-status.md) | Status, gates, evidence |

Decisions: [ADR-027](../architecture/adrs/ADR-027-control-plane-principals.md)
(staff and partner principals, explicit scope),
[ADR-028](../architecture/adrs/ADR-028-partner-capability-model.md)
(capabilities, not types),
[ADR-029](../architecture/adrs/ADR-029-commission-ledger-and-payout-model.md)
(append-only commission ledger; payouts move no money).

## The ten rules Phase 7 lives by

1. **Backend authorization is authoritative.** The browser hides controls; every
   request is authorized again by the same `authz.check`.
2. **Dangerous means AAL 2.** Anything that moves money, changes access or
   authority, or suspends a customer needs a recent second factor.
3. **Money is server-derived.** No request body carries a price, a commission
   amount, a rate, an approver or a status stamp.
4. **One writer per fact.** Staff use the existing commercial and provisioning
   services; there is no second catalog, audit system or entitlement writer.
5. **Validate before write, or raise inside `atomic`** (D-016), and every
   client-controlled foreign key is tested with a real `COMMIT`.
6. **A partner is a set of capabilities**, never a type.
7. **A partner reaches a customer only through a live link**, and gets 404, not
   403, for anything else.
8. **The ledger only grows.** A wrong entry is corrected by a new one.
9. **Customers outlive partners.** Ending a partner never touches a customer's
   organization, subscription or entitlements.
10. **The platform moves no money.** `paid` records that a person did.
