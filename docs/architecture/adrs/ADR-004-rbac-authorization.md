# ADR-004 — RBAC Authorization with Scoping

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture / Security

---

## Context

Authorization must express: different permissions per product within one organization; standard roles most customers accept unmodified; custom roles for customers who need them; resource-scoped access for external collaborators; temporary elevation; and object-level rules ("edit deals you own").

It must be explainable to a customer admin, fast enough to run several times per request, and expressible in list queries without post-filtering.

## Decision

**Role-based access control, with role assignments scoped by organization, product or resource, plus a narrow set of object-level rules evaluated by the owning module.**

- `Permission` — code-declared, `<product>.<resource>.<action>`. Cannot be created by users.
- `Role` — system templates (global) or custom (tenant-owned). Customising a system role clones it.
- `RoleAssignment` — binds `(membership, role, scope)` with an optional `expires_at`.
- One decision function, `authz.check(ctx, permission, obj=None)`, used by every principal type.
- **Permission sets are compiled and cached** per membership, versioned per organization.
- **Object-level visibility is expressed as queryset predicates** (`visible_*`), never per-row evaluation.
- Authorization is checked **after** entitlement ([ADR-005](ADR-005-product-entitlement-model.md)).

## Alternatives considered

### A. Django's built-in permissions

- **Pro:** free, familiar, admin integration.
- **Con:** permissions attach to `User`, not to a membership — wrong shape for multi-org. No scoping. Model-level only (`add`/`change`/`delete`), which does not express `crm.deal.win`. Object permissions require a third-party package with known performance problems in list queries.
- **Rejected:** wrong tenancy shape at the foundation.

### B. Full ABAC / policy engine (OPA, Cedar)

Attribute-based policies evaluated by an engine.

- **Pro:** extremely expressive; policy as data; externally auditable.
- **Con:** decisions become hard to explain to a customer admin ("why can't I edit this?" has no simple answer); list queries need either per-row evaluation or a policy-to-SQL compiler; a new runtime dependency on the request path; a policy language for the team to learn.
- **Rejected for Phase 0.** The expressiveness solves problems we do not have, at the cost of comprehensibility we need. Reconsider if genuinely complex cross-attribute policies appear.

### C. Simple role enum per membership

`role = "admin" | "member" | "viewer"`.

- **Pro:** trivial.
- **Con:** cannot express per-product permissions, custom roles, or resource scoping — all of which are stated requirements.
- **Rejected:** insufficient on day one.

### D. RBAC with scoping and narrow object rules (selected)

## Why selected

1. **Customers understand roles.** "Sales Manager can edit deals" is explainable; an attribute policy is not.
2. **Scoping covers the real requirements** — per product, per resource subtree — without ABAC's generality.
3. **Fast.** A compiled permission set makes a check one set-membership test.
4. **List-query safe.** Requiring visibility as a predicate means list endpoints cannot leak by forgetting a per-object check, and pagination counts stay correct.
5. **Auditable.** Effective permissions are enumerable per user, which is required for access reviews and customer security questionnaires.
6. **It can grow.** Object rules are an extension point; if genuine policy complexity arrives, the rule evaluator can be replaced behind `authz.check()` without touching callers.

## Consequences

**Positive**

- One decision path for all principal types.
- Permission surface is greppable — every privilege is a declaration in code.
- Compiled sets make checks negligible in the latency budget.
- Version-keyed cache invalidation is a single counter bump per organization.
- `expires_at` makes temporary access expire automatically.

**Negative, accepted**

- Complex conditional rules need code, not configuration. Deliberate: rules nobody can explain are a liability.
- Role explosion is possible if every customer customises. Mitigated by good defaults and product-scoped roles.
- Visibility rules must be predicate-expressible. A rule that cannot be is treated as the wrong rule.
- `Role` has a nullable `organization_id` (system vs custom), the one exception to the plane rule. Made safe by a partial RLS policy that forbids the application role from writing system roles at all, and recorded in the debt register.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| A new endpoint ships without a permission check | **High** | Generated authorization-matrix test over (persona × endpoint) |
| Stale permission cache after revocation | High | Version-keyed invalidation + short TTL; denials cached more briefly than grants |
| Over-privileged default roles | Medium | Admin excludes billing by default; quarterly review of system roles |
| Custom roles drift from product changes | Medium | New permissions default to ungranted; release notes list new permissions |
| Object rule bypassed in a task path | Medium | Checks live in application services, not views |

## Future extraction path

`platform/authz` is **never extracted** — a network hop per authorization check is unacceptable, and deciding against stale replicated permission data is a security risk.

If policy complexity grows beyond RBAC, the path is to replace the internals of `authz.check()` with a policy engine evaluated **in-process**, keeping the same call signature. Callers do not change. A policy-to-SQL compiler would be required before any such engine could serve list queries.

## Related

- [04-identity-authz.md](../04-identity-authz.md), [12-security.md](../12-security.md)
- [ADR-003](ADR-003-shared-identity.md), [ADR-005](ADR-005-product-entitlement-model.md)
