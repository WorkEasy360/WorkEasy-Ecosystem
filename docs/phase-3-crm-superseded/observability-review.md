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

# Observability Review

**Status:** Accepted (Phase 3)

---

## 1. What CRM adds

CRM emits through the same three channels every other module uses -
structured logs (`platform.observability.logging`), metrics
(`platform.observability.metrics`), and the outbox/audit trail - adding no
new observability mechanism of its own (Task 51 asks CRM to *use* the
existing infrastructure, not build a parallel one).

**Structured log events** (module-qualified, matching the existing
`tenant.write_blocked`/`entitlement.applied` naming convention):

- `crm.system.overdue_sweep` (info) - one summary line per full sweep run:
  organizations with overdue activities, total overdue count, failures.
- `crm.system.sweep_failed` (error) - one organization's failure during the
  sweep, with `organization_id` and `error_type`, never the exception's
  full text (matches the platform-wide "no PII/secrets in logs" rule).
- Every audit-worthy CRM action (creation, ownership reassignment, bulk
  actions, lead conversion, pipeline/stage changes, deal stage/won/lost
  transitions, archive/restore) produces an `audit.record()` call, which
  itself is the platform's own structured, hash-chained log - see
  §3 below rather than duplicating that list here.

**One new metric**: `crm_overdue_activities_found_total` (counter,
incremented by the sweep, no `organization_id` label - Task 51's
cardinality rule: a per-organization label on a metric would make its
cardinality grow with the customer base, which is exactly what the
existing platform metrics avoid).

**Correlation.** CRM introduces no new correlation mechanism - every
service call happens inside a request or task that already carries
`current_correlation_id()` (set by `CorrelationIdMiddleware` or
`TenantTask.before_start`), and `events.build_event()` reads it
automatically for every CRM domain event, exactly as it does for commercial
events. No CRM code sets or threads a correlation id itself.

## 2. Deal stage changes and lead conversions as first-class signals

Task 51 names these explicitly. Both are audited
(`crm.deal.stage_changed`/`crm.deal.won`/`crm.deal.lost`,
`crm.lead.converted`) and published as domain events with the same names -
an operator or a future dashboard can derive a rate or a count from either
the audit table or the outbox without CRM adding a bespoke metric for
something the existing structured trail already answers. No additional
counter was added for these specifically, because one already exists in
substance (the audit/event stream) and a duplicate counter would be the
"redundant events" Task 61's cleanup pass would later have to remove.

## 3. What was not added, and why

- **No CRM-specific dashboard or alert definition.** Nothing in this
  repository defines Grafana dashboards or alert rules for *any* module
  (commercial included) - that lives in `infra/`, out of this phase's
  code scope, and Phase 3 introduces no gap relative to Phase 2's own
  observability surface.
- **No per-request CRM latency histogram.** The existing
  `ENTITLEMENT_CHECK_DURATION`-style `timed()` pattern is available and
  was considered for `require_crm_access`, but every CRM service already
  goes through `platform.entitlements`' own timed check and
  `platform.authz`'s own metrics
  (`AUTHZ_CHECKS`/`AUTHZ_DENIALS`) - adding a second timer around the same
  call would double-count rather than add information.
- **Outbox backlog for CRM events specifically**: not tracked separately
  from the platform-wide outbox backlog metric (`pending_age_seconds()`,
  unchanged) - CRM events share the same `OutboxEvent` table and the same
  relay, so a CRM-specific backlog figure would require a filter the
  platform metric does not need for any other module either.

## 4. Verified

`tests/test_system_tasks.py::test_full_sweep_covers_multiple_organizations_independently`
exercises the sweep end to end and implicitly exercises the logging/metric
path (no assertion on log content directly - the existing platform
convention does not unit-test log line text, only behaviour). No dedicated
metrics-assertion test was written; `platform.observability.metrics.metrics.reset()`
runs autouse between every test (`backend/conftest.py::_reset_registries`),
so a metric a CRM test needed to assert on would already be isolated
per-test if such an assertion were added later.
