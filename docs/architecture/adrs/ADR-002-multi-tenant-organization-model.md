# ADR-002 — Multi-Tenant Organization Model

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture

---

## Context

Workeasy360 serves many customer businesses from one platform. We need a unit of tenancy that is simultaneously the boundary for data isolation, the subject of billing, the container for users and permissions, and the holder of product entitlements.

Complicating factors: one person may belong to several customers (an accountant with twelve clients); multi-entity customers (holding companies, franchises) exist and will ask for group structures; partners are businesses in the system but are not customers; and permissions must differ per product within one customer.

## Decision

**`Organization` is the single unit of tenancy. `User` is global. `Membership` is the junction that grants all access.**

- `Organization` — the tenant. Owns all business data, holds entitlements, is the billing subject, is the `organization_id` on every tenant row.
- `User` — a global login identity, not scoped to any organization.
- `Membership` — `(user, organization)` with status and type. **All authorization resolves through a membership, never through a user.**
- `Organization.parent` exists for future group structures but grants **nothing** implicitly.
- `PartnerAccount` is a separate scope, not an organization.
- `Organization.residency_region` and `shard_key` exist from day one, unused.

## Alternatives considered

### A. Workspace/project as the tenant (Slack/Asana style)

- **Pro:** finer-grained isolation; natural for collaboration tools.
- **Con:** Books, Payroll and Billing are organization-level concepts. A ledger per workspace is incoherent. Entitlements and billing would need a parent anyway — so a second tenancy level appears regardless.
- **Rejected:** business-operations software is organization-shaped.

### B. Per-organization user accounts

Each org has its own users; the same person has several accounts.

- **Pro:** simple isolation; `User` gets an `organization_id` and RLS covers it.
- **Con:** an accountant maintains twelve passwords and cannot switch context. Compromise of one account cannot be correlated with others. Invitation UX is poor. Cross-org collaboration is impossible.
- **Rejected:** unacceptable for our primary multi-org personas (accountants, consultancies, partners).

### C. Organization hierarchy as a first-class isolation concept

Parent orgs automatically see children's data.

- **Pro:** matches enterprise group structures directly.
- **Con:** RLS becomes a recursive predicate on every query in the system — a serious performance and correctness hazard. Every isolation check becomes "is this org a descendant of that one?", which is exactly the kind of implicit widening that produces breaches.
- **Rejected for Phase 0:** the column exists; the semantics are deferred to an explicit, audited cross-org grant.

### D. Organization as the tenant, user global, membership as the junction (selected)

## Why selected

1. **It matches the commercial reality.** We sell to businesses; the business is the tenant, and billing, entitlements and data isolation align on one boundary.
2. **It supports multi-org humans natively**, which is a primary persona, not an edge case.
3. **`organization_id` on every row makes RLS a simple equality predicate** — fast, indexable, and easy to verify.
4. **Membership as the access junction makes per-product permissions natural**: roles attach to memberships, scoped by product.
5. **Global identity centralises security.** One place for MFA, breach response, session revocation. A compromised person is stoppable everywhere at once.
6. **Reserved fields cost nothing now** and preserve residency and sharding options later.

## Consequences

**Positive**

- One equality predicate isolates every table.
- Org switching is a first-class, audited action.
- Billing, entitlements and isolation share one boundary — no reconciliation between them.
- Partner scoping stays a separate, clearly different problem.

**Negative, accepted**

- `User` has no `organization_id`, so RLS cannot protect it. Mitigated: `User` is treated as control-plane-adjacent, always accessed via `organizations` selectors joining through `Membership`; product code never queries it directly.
- Email uniqueness is global, enabling enumeration attempts. Mitigated by uniform responses and uniform timing.
- Global user disable affects all the person's organizations. This is intended; per-org deactivation is `Membership.status`.
- SSO is more complex: a user may be in one SSO-enforcing org and one not. Resolved by making authentication method a property of the *organization session*, not the user.
- Group structures need explicit work later rather than coming free.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| A query forgets `organization_id` | **Critical** | Five isolation layers ([03](../03-multi-tenancy.md)); RLS is the backstop |
| `User` accessed without membership join | High | Selector-only access; AST check; `F-6` rule |
| Org-switch state confusion (acting in the wrong org) | Medium | `active_membership` on the session *and* org in the URL path; both must agree |
| Customers demand group visibility early | Medium | Explicit `OrganizationLink` grant path defined; no RLS change needed |
| Membership row missing but session valid | Medium | Checked on every request; fails closed |

## Future extraction path

`Organization` and `Membership` are in `platform/organizations` and are **never extracted** — the authorization path must stay in-process ([ADR-001](ADR-001-modular-monolith-first.md)).

If per-tenant database isolation is ever required for an enterprise customer, the model already supports it: `shard_key` and `residency_region` exist, tenant routing is centralised in `platform/tenancy`, and every row already carries `organization_id`. The change is a routing layer, not a data model change.

## Related

- [03-multi-tenancy.md](../03-multi-tenancy.md), [04-identity-authz.md](../04-identity-authz.md)
- [ADR-003](ADR-003-shared-identity.md), [ADR-009](ADR-009-tenant-data-isolation.md)
