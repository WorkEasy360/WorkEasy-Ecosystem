# ADR-010 — Partner, Reseller, White-label and Affiliate Model

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture / Commercial

---

## Context

Workeasy360 sells through resellers, white-label partners and affiliates. Each needs different privileges: affiliates refer and earn commission; resellers sell, are invoiced, and manage subscriptions; white-label partners additionally control branding and serve on their own domain.

The hard requirement: **partners must not receive separate copies or forks of the products.** One codebase, one deployment.

Real partner programmes are also messy — affiliates become resellers for a few accounts, resellers want branding without billing, distributors recruit sub-partners. A model that assumes three clean types will not survive contact with the sales team.

## Decision

**One `PartnerAccount` with composable capability grants. Partner scope is a third scope, separate from tenant scope.**

- `PartnerAccount` + `PartnerCapability` rows (`REFER`, `RESELL`, `MANAGE_BILLING`, `MANAGE_SUBSCRIPTION`, `BRAND`, `CUSTOM_DOMAIN`, `SUB_PARTNER`, `SUPPORT_TIER1`).
- "Reseller", "white-label" and "affiliate" are **presets**, not types. Authorization code asks `partner.has(RESELL)`, never `partner.type == ...`.
- `PartnerRelationship(partner, organization, kind)` links partners to customers. **At most one active `managed` relationship per organization**, enforced by a partial unique constraint.
- **Partners get no customer business data by default.** Access requires a `DataAccessGrant` issued by the **customer**, scoped, time-boxed, revocable, audited and customer-visible.
- Branding is **data resolved at the edge** into a neutral value object; product code never imports `partners`.
- A custom domain changes presentation only — **never authorization**. Sessions are host-scoped.
- Reseller margins use partner price books; affiliate commissions use an **append-only ledger** with reversals and a `clears_at` holdback.

## Alternatives considered

### A. Separate deployment per white-label partner

- **Pro:** total isolation; trivially "their own product".
- **Con:** N deployments, N migration runs, N monitoring setups, N security patch cycles. Divergence is inevitable. Directly contradicts the brief.
- **Rejected.**

### B. Partner as a type of Organization

Reuse the tenant model for partners.

- **Pro:** reuses RLS and membership machinery.
- **Con:** conflates "a business that buys software" with "a business that sells our software". A partner would get a data plane it should not have, and every tenant query would need to exclude partner orgs. Partner-scoped data (commissions, payouts, price books) is not tenant-scoped data.
- **Rejected:** a partner *may* have its own organization as a customer (`home_organization`), but that is a separate relationship, not an identity.

### C. Partner type enum with per-type logic

`partner.type in ("affiliate", "reseller", "whitelabel")`.

- **Pro:** simple; matches how sales describes it.
- **Con:** every privilege check becomes a branch; hybrid partners require new enum values combinatorially; the branch that accidentally grants an affiliate reseller access is a data breach.
- **Rejected:** privilege logic must not be expressed as type branching.

### D. Capability-based `PartnerAccount` (selected)

## Why selected

1. **Capabilities compose; types do not.** Every real-world hybrid is a row, not a code change.
2. **Affiliate isolation is structural.** `REFER` grants attribution visibility only; customer data requires `RESELL` **plus** an active relationship **plus** a customer-issued grant. There is no code path from `REFER` to PII.
3. **Customer-issued data grants keep the trust boundary correct.** The customer owns their data; a reseller is a commercial intermediary. A partner who loses an account loses access.
4. **Branding as edge-resolved data** keeps `products → commercial` forbidden and prevents partner-specific code paths in products.
5. **Domains carry no authorization meaning**, which closes an obvious escalation path — otherwise "which partner's domain am I on?" becomes an implicit credential.
6. **Append-only commission ledger** makes partner disputes resolvable and survives audit.

## Consequences

**Positive**

- One codebase serves all channels.
- New channel models are new capability combinations.
- Affiliate privilege separation is enforced by construction.
- Customers retain control over who sees their data.
- Commission and payout history is reconstructible.

**Negative, accepted**

- Capability checks are more verbose than a type check. Worth it.
- Partner scope is a third authorization scope alongside tenant and staff scope — more to test. Mitigated by a partner-specific authorization matrix test.
- White-label branding is limited to validated design tokens, not arbitrary CSS. Necessary: partner-supplied CSS is a stored-XSS vector.
- Custom domain provisioning (DNS verification, certificates, host routing) is real operational work.
- Sub-partners are depth-capped at 2, which some distributors may find limiting. Deliberate: deeper trees make commissions unexplainable.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Partner sees customer data without a grant | **Critical** | Default-deny; grant required; authorization matrix tests per capability |
| Affiliate privilege creep | High | `REFER` has no data path; aggregate-only endpoints |
| Custom domain treated as an identity signal | High | Explicit rule; host-scoped sessions; tests asserting identical authorization across hosts |
| Two partners claim one customer | Medium | Partial unique constraint on active `managed` relationship |
| Commission miscalculation | Medium | Append-only ledger, reversals, reconciliation, dual approval on payouts |
| Double payout on retry | High | Unique `idempotency_key` at the database level |
| Partner-supplied branding used for phishing | Medium | Token-only palette; DKIM/SPF/DMARC verification before sending from partner domains |
| Partner offboarding leaves access | Medium | Termination revokes all grants and portal sessions immediately |

## Future extraction path

`commercial/partners` and `commercial/commissions` are plausible extraction candidates — they are off the customer hot path and have a clear contract. In practice, extraction would mainly complicate reporting that spans billing and commissions; there is no current reason.

The partner **portal** is a separate frontend surface from day one, so a partner-facing service could be introduced without disturbing the customer application.

## Related

- [05-commercial-model.md](../05-commercial-model.md), [12-security.md](../12-security.md), [04-identity-authz.md](../04-identity-authz.md)
- [ADR-006](ADR-006-product-catalog-and-bundles.md)
