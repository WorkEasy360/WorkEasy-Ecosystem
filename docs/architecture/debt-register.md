# Architecture Debt Register

Every accepted exception to the rules in [ARCHITECTURE.md](../../ARCHITECTURE.md) is recorded here with an owner and a review date. An exception that is not listed here is not accepted — it is a defect.

## How an entry gets here

1. ADR written (edge, reason, alternatives tried, cost of not having it)
2. Architecture review approved
3. Narrow exception added to `.importlinter` — specific module to specific module, never a wildcard
4. Row added below

## Open entries

| ID | Item | Type | Reason accepted | Owner | Added | Review by |
|---|---|---|---|---|---|---|
| D-001 | `platform.authz.Role` has a nullable `organization_id` (system templates vs tenant-owned roles) | Plane-rule exception | Two parallel tables plus a polymorphic join from `RoleAssignment` is materially worse for every query. Made safe by a partial RLS policy allowing reads of `organization_id IS NULL AND is_system`, and a `WITH CHECK` that forbids the application role from writing such rows at all. Implemented by `enable_system_template_rls()`. | Architecture | 2026-09-21 | 2027-03-21 |
| D-002 | `organizations.Membership` is a fourth cross-module FK target | F-6 allowlist extension | Phase 0's own models require it (`RoleAssignment.membership`, `SeatAssignment.membership`). Restricted to `platform/` and `commercial/`; products still use `identity.User`. The extraction cost F-6 protects against is nil, because ADR-001 declares the owning module never extractable. See [ADR-014](adrs/ADR-014-membership-fk-allowlist.md). Enforced by `W360.E003`. | Architecture | 2026-09-21 | 2027-03-21 |
| D-003 | `AuditEvent` is exempt from the R-10 suspension write block | Write-block exemption | A suspended organization must still accrue audit entries, including the record of each *denied* write - otherwise the period a customer most needs a trail is the period that has none, and every denial surfaces as `AuditWriteFailed` instead of a 402. Safe because the table is append-only and holds no business state. The **only** exemption; a second requires an ADR. | Architecture | 2026-09-21 | 2027-03-21 |
| D-004 | `AuditEvent.organization` has `db_constraint=False` | FK without a database constraint | Audit is **retained** as a tombstone on tenant purge (08-data-ownership.md section 6), so it must not cascade. The column remains, so RLS still scopes the table and customers still read only their own log. | Architecture | 2026-09-21 | 2027-03-21 |
| D-005 | A module inside `commercial/` may hold a foreign key to another module inside `commercial/` | F-6 allowlist extension | Phase 0's own model definitions specify these keys (`PlanPrice.plan_version`, `SubscriptionItem.plan_version`), and `on_delete=PROTECT` is the database-level mechanism behind "historical pricing cannot mutate". The extractability concern F-6 protects does not arise: ADR-006's extraction path moves catalog and pricing **together**. Narrow - `commercial` only; `platform/` siblings and products remain forbidden. See [ADR-016](adrs/ADR-016-intra-commercial-foreign-keys.md). Enforced by `W360.E003`. | Architecture | 2026-09-21 | 2027-03-21 |
| D-006 | Three modules added to the `unscoped` manager allowlist: `platform/audit/services.py`, `commercial/subscriptions/selectors.py`, `commercial/subscriptions/system.py` | Tenant-scoping bypass | Clock-driven commercial transitions have no actor and so no `TenantContext`, and they must work for **suspended** organizations whose writes R-10 blocks under any context. Each mirrors an existing Phase 1 precedent, each is one file, and **RLS is never relaxed** - every one enters `organization_db_scope` for exactly one organization. `system.py` additionally takes no caller-supplied status and audits every operation. See [docs/phase-2/security-review.md](../phase-2/security-review.md) section 5. | Architecture | 2026-09-21 | 2027-03-21 |

## Closed entries

_None yet._

## Rules

- Suppressions in code (`# noqa: arch`) require a ticket reference and **fail CI after 90 days**.
- An entry past its review date fails the monthly architecture review until re-justified or removed.
- Adding an entry is deliberately more work than doing it correctly. That asymmetry is the point.
