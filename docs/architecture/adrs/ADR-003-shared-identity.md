# ADR-003 — Shared Identity Across All Products

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture / Security

---

## Context

Workeasy360 hosts many products. Each could, in principle, manage its own users — and in a company that grew by acquisition, each typically does. We are building from scratch, so the choice is open.

Customers expect one login for the whole ecosystem. Security requires one place to enforce MFA, revoke sessions and respond to compromise. Partners and staff need authentication too, with different requirements.

## Decision

**`platform/identity` is the sole authority for authentication. No product implements any part of it.**

- One `User`, one credential set, one session, one MFA enrolment, one SSO configuration per organization.
- Products receive an authenticated `TenantContext` and never see credentials.
- All principal types — interactive user, API key, OAuth client, staff, partner admin, service — converge on the same `TenantContext` and the same authorization path.
- MFA is modelled from day one (`AuthFactor` with TOTP and WebAuthn fields) even though enforcement policy arrives later.
- SSO is per organization, with DNS-verified domain claims and a mandatory non-SSO break-glass owner.

## Alternatives considered

### A. Per-product authentication

- **Pro:** complete product independence; a product could be sold entirely standalone.
- **Con:** users manage N passwords; MFA must be enrolled N times; revocation must happen N times, and will be missed; N implementations means N sets of authentication bugs; SSO must be configured per product.
- **Decisive objection:** authentication is the highest-consequence code in the system. Having several implementations multiplies the attack surface for no benefit the customer values. "Independently commercializable" means independently *sold*, not independently *authenticated*.

### B. External IdP as the sole identity (Auth0/Cognito/Okta)

- **Pro:** offloads a hard problem; compliance certifications included.
- **Con:** identity becomes a Tier-0 external dependency; per-MAU cost grows with success; multi-org membership semantics are awkward in most products; per-tenant SSO configuration is often an expensive tier; customer data leaves our boundary; migration away later is very hard.
- **Rejected as the sole authority**, but the `IdentityProvider` model permits federation *to* customer IdPs, which is the part customers actually ask for. We remain the authority; we federate authentication.

### C. Shared identity in `platform/` (selected)

## Why selected

1. **Single sign-on across the ecosystem is a product requirement**, not a convenience. One login is what makes it an ecosystem rather than a bundle.
2. **Security consolidation.** One place to add WebAuthn, one place to revoke on compromise, one audit trail, one rate-limiting surface.
3. **Progressive product activation requires it.** A customer adding Books must not create new accounts for everyone.
4. **Correct layering.** Identity is genuinely universal and product-agnostic — the exact test for platform membership ([01](../01-domain-boundaries.md)).
5. **We keep control** of session semantics, org switching, impersonation and break-glass, which are unusual enough that external IdPs model them poorly.

## Consequences

**Positive**

- One login, one MFA enrolment, one revocation point.
- Security investment benefits every product at once.
- Products carry no authentication code and cannot get it wrong.
- Impersonation and break-glass are implemented once, consistently.
- Per-organization SSO works uniformly across all products.

**Negative, accepted**

- `platform/identity` is a single point of failure for all access. Mitigated by DB-backed sessions with Redis as cache only, aggressive testing, and treating it as Tier 0.
- A product cannot be sold as a fully standalone product with its own login. Accepted: it contradicts the ecosystem vision.
- Identity changes require care because every product depends on them. Mitigated by a stable contract and comprehensive tests.
- SSO configuration complexity is centralised — more complex in one place, rather than absent in many.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Identity outage blocks everything | **Critical** | DB is source of truth; Redis optional; Tier-0 treatment; multi-AZ |
| SSO misconfiguration locks a customer out | High | Mandatory break-glass local owner; `is_enforced` refused without one |
| Domain-claim takeover | **Critical** | DNS verification before any auto-attach |
| Session fixation / hijack | High | Rotation on privilege change; idle + absolute expiry; binding signals |
| Global user disable over-reaches | Medium | Per-org `Membership.status` for scoped deactivation |
| A product implements a side-channel login | Medium | Fitness rule: no product defines a user model or auth view |

## Future extraction path

**Never extracted.** Distributing the authentication and authorization path introduces a network call into every request and the possibility of deciding against stale state.

If external IdP federation grows (SCIM provisioning, many enterprise IdPs), the `IdentityProvider` model absorbs it without changing the authority model: we remain the identity authority; external IdPs authenticate users *to* us.

## Related

- [04-identity-authz.md](../04-identity-authz.md), [12-security.md](../12-security.md)
- [ADR-002](ADR-002-multi-tenant-organization-model.md), [ADR-004](ADR-004-rbac-authorization.md)
