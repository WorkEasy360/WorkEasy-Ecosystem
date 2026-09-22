> **SUPERSEDED — CRM MOVED OUTSIDE PLATFORM SCOPE.**
>
> This document describes the CRM product as it was built *inside* this
> repository during Phase 3. That code was removed by the Phase 3R scope
> correction: products are now separate applications in their own
> repositories ([ADR-017](../architecture/adrs/ADR-017-products-are-separate-applications.md),
> [16 Ecosystem / Product Boundary](../architecture/16-ecosystem-product-boundary.md)).
>
> Retained as historical audit evidence only. **Nothing here describes the
> current repository.** For what was kept from this work and why, see
> [Phase 3R status](../phase-3R/phase-3R-status.md).

---

# Failure Mode Review

**Status:** Accepted (Phase 3)

---

## 1. Graceful degradation (Task 50)

| Dependency | CRM's behaviour if unavailable |
|---|---|
| Redis (cache backend) | Entitlement and permission checks fall back to PostgreSQL reads - slower, still correct (platform behaviour, unchanged by CRM; CRM adds no cache of its own to fail) |
| RabbitMQ (broker) | Domain events still commit to the outbox in the same transaction as the business write (`integration.events.services.publish`, an INSERT, no broker call) - a broker outage delays delivery, never loses or blocks the write. Notification delivery (`activity_services._notify_assignment`) similarly persists intent first and queues delivery via `transaction.on_commit`, tolerating an enqueue failure (`_enqueue`'s own `except Exception` + log, platform code, unchanged) |
| Celery workers | The overdue-activity sweep (`sweep_overdue_activities`) not running is a *delay*, not a correctness failure - it only counts and logs, it does not mutate CRM data. No CRM write path depends on a worker completing |
| Notification provider | `_notify_assignment` catches `OrganizationNotWritable` specifically (the known suspended-org gap) and is itself wrapped by the platform's own best-effort delivery/retry/dead-letter path - an activity assignment always succeeds even if the notification about it never sends |
| A future AI service | None exists or is called anywhere in Phase 3 (explicitly out of scope - Task 39's forecast is deterministic arithmetic, not a model call) |

**No CRM write path holds a transaction open across a network call.** Every
external interaction (event publish, notification enqueue) either commits
synchronously to a local table first, or happens strictly after the
business transaction commits (`transaction.on_commit`) - the same
discipline `commercial/subscriptions/services.py` documents in its own
module docstring ("No external service is called anywhere in that
sequence, so no transaction is ever held across a network round trip").

## 2. Rate limiting (Task 54)

**Trigger reached: NO.**

CRM's API sits under the same tenant-scoped, authenticated-only path shape
every other endpoint in this codebase does (`/api/v1/orgs/<slug>/...`) - no
public, unauthenticated CRM surface exists. The Phase 1/2 documented
trigger ("before first production customer" / "public exposure") is
unchanged by Phase 3: CRM adds no public endpoint, and the platform-wide
rate-limiting debt item remains exactly as carried.

**Evidence**: every CRM URL pattern
(`products/crm/api/urls.py`) is included under
`orgs/<slug:org_slug>/crm/` in `interfaces/api/v1/urls.py`, and every view
reads `request.tenant_context`, which `TenancyMiddleware` only sets after
authenticating the caller and confirming an active membership - there is no
CRM route reachable without both.

**Action**: none. No targeted limit was added, because no CRM endpoint is
more security-sensitive than the commercial ones that already carry the
same unaddressed debt.

## 3. Circuit breakers / backpressure (Task 55)

**Trigger reached: NO**, for the same reason Phase 2 recorded: CRM adds no
external HTTP dependency. `products/crm/` calls no payment provider, no
third-party API, no AI service - the trigger for both remains "the first
payment provider or connector" (circuit breakers) and "before first
production customer" (backpressure/load shedding), both still unmet.

## 4. Other carried debt items, reassessed for a Phase-3 trigger

| Item | Trigger | Reached in Phase 3? | Evidence |
|---|---|---|---|
| Malware scanning (`NullScanner`) | No Phase 2 or audit trigger | **NO** | CRM does not implement file attachments (Task 42: "consume the existing platform file service" - no CRM record was found to need attachments in Phase 3's required scope, so none was added; the platform file service itself is untouched) |
| CSP not emitted | No HTML surface exists yet | **NO** | CRM ships no HTML surface - JSON API only, no frontend built (see [frontend-review.md](frontend-review.md)) |
| SSRF-safe fetch helper | Required before webhooks or connectors ship | **NO** | CRM makes no outbound HTTP call of any kind |
| Entitlement-version query (R-14 budget) | p95 > 4ms or hit ratio < 0.95 | **NO** | No new measurement invalidates the Phase 2 figures; CRM is a new, heavy *caller* of the existing mechanism (`require_crm_access` on every service call) but does not change its implementation. `test_entitlement_check_is_memoised_per_request` confirms the request-local memo still holds for CRM's call pattern |
| AAL2 step-up on commercial ops | Session/MFA policy wiring | **NO**, unchanged | Not CRM's dependency - no CRM permission is `dangerous` (see [permissions.md](permissions.md) §3), so this trigger is orthogonal to Phase 3 |
| Metrics backend (in-process only) | Before real observability infra | **NO** | CRM's one new metric (`crm_overdue_activities_found_total`) uses the existing in-process registry, adding no new requirement |

## 5. Failure scenarios explicitly tested

- **RLS blocks a query -> zero results**: `test_wrong_organization_context_cannot_read_other_orgs_data` -
  the caller sees "not found", not an error masquerading as an empty list,
  and the test asserts the *reason* (wrong tenant, not "genuinely no
  data") is not distinguishable at the API boundary (by design - an
  enumeration oracle would be worse).
- **Missing tenant scope**: raises, never returns empty -
  `test_missing_tenant_scope_raises_not_denies`,
  `test_count_overdue_without_scope_helper_raises_not_silently_empty`.
- **Worker started with no inherited context**: `tests/test_system_tasks.py`,
  see [tenant-isolation-review.md](tenant-isolation-review.md) §4.
- **One organization's failure does not stop a cross-tenant sweep for the
  rest**: `services/system.py::sweep_overdue_activities` catches per-organization
  exceptions, logs, and continues - not separately unit-tested with an
  injected failure in Phase 3, but structurally identical to
  `commercial.subscriptions.system.process_due_subscriptions`'s own
  per-organization `try/except`, which Phase 2 did test this way.
