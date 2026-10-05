# ADR-024: The integration platform's data model, and a cross-tenant due-work timer

**Status:** Accepted
**Date:** 2026-09-23
**Deciders:** Architecture
**Depends on:** [ADR-008](ADR-008-transactional-outbox.md), [ADR-009](ADR-009-tenant-data-isolation.md), [ADR-016](ADR-016-intra-commercial-foreign-keys.md)

---

## Context

Phase 6 adds the integration platform: connections (an organization's
configured external systems), webhook subscriptions and deliveries, routed
events, automation rules, their versions, and executions. Two existing
rules pull against it.

1. **F-6 / W360.E003: cross-module foreign keys are limited to a frozen
   allowlist.** The new models are spread over `integration/connectors`,
   `integration/webhooks`, `integration/events` and `integration/automation`.
   A delivery must reference its connection and its event; an execution its
   event. Soft references (plain UUID columns) would satisfy E003, but they
   give up what B4 exists for: a composite `(id, organization_id)` foreign
   key that makes a delivery for organization A pointing at organization
   B's connection **unrepresentable**, not merely unexpected (Tasks 57/58).

2. **Row-Level Security hides every tenant's rows from a cross-tenant
   sweeper.** Deliveries retry, executions retry, schedules fire, and work
   whose after-commit enqueue was lost to a broker outage must be recovered.
   All of that needs a process that asks "what is due now, across every
   organization?" With RLS it can only ask one organization at a time: the
   notification sweeper already does exactly that, and at 10,000
   organizations and a 5-second tick it is 2,000 queries a second of idle
   polling. The Phase 5 audit recorded the pattern as a scaling ceiling.

Celery's own `countdown`/ETA was considered for retries and rejected:
RabbitMQ keeps an ETA message unacknowledged on the worker that prefetched
it until it fires, and closes the channel once its consumer timeout (30
minutes by default) passes - so a two-hour backoff through the broker is a
redelivery storm, not a retry.

## Decision

**1. `integration/` joins `commercial/` in `INTRA_PACKAGE_FK_PACKAGES`.**
Modules inside `integration/` may hold foreign keys to each other, for
ADR-016's reason: they are one extraction unit (the integration service
moves as a whole), and the keys between them carry isolation guarantees.
Still `integration -> integration` only; a foreign key leaving `integration`
must target the frozen allowlist (it does: `Organization`, `User`). Every
tenant-to-tenant key is also a composite key (`tenancy.0005`), and the
import graph inside `integration/` is layered by import-linter
(`integration-internal-layers`: mechanism < connectors < webhooks <
automation < routing). A key pointing *up* that order (a delivery created by
an automation action) stays a soft reference, made idempotent by a unique
constraint instead.

**2. One RLS-exempt timer table: `scheduling.DueWork`.** One row per
pending unit of work - `(kind, organization_id, target_id, due_at, claims)`,
unique on `(kind, target_id)` - and **nothing else**: no payload, no status,
no tenant data (pinned by `test_the_due_work_timer_holds_no_tenant_data`).
The sweeper claims due rows across tenants with `FOR UPDATE SKIP LOCKED` in
one statement, commits, and dispatches a task per row inside a routing
context carrying that organization's *live* status. The task enters that one
organization's scope and does the work in RLS-protected tables. Consumers
write and delete their timers inside their own tenant transactions, so a
timer commits or rolls back with the work it describes. A claim pushes
`due_at` out by a lease that doubles with every unacknowledged dispatch
(capped at an hour), so a backlog is re-dispatched ever less often, and a
dispatch that never ran - broker down, worker killed - is retried when its
lease expires.

It is exempt from W360.E001 in `TENANT_SCOPE_EXEMPT` and recorded as
debt-register D-017, in exactly the class of `events.OutboxEvent`.

## Consequences

- Cost of finding due work is O(due rows), not O(organizations). Measured
  claim plan: an index scan on `ix_due_work_due` with 20,000 future timers
  in the table (`test_the_hot_queries_are_index_served_on_a_padded_table`).
- One table, four consumers (routing, webhook delivery, automation
  execution, schedule firing), one sweeper task - no per-consumer sweep.
- Every enqueue is at-least-once: the fast path (after-commit) and the
  timer path can both dispatch one item. Every consumer claims with a
  conditional write, so a duplicate dispatch is a no-op.
- Broker outage recovery is structural rather than a separate job: the
  timer *is* the durable record that work remains (Task 68).
- A `DueWork` row reveals that *some* work exists for an organization. It
  is readable only by the application role (as `OutboxEvent` is), and the
  `organization_id` it carries is already in every log line.

## Alternatives rejected

- **Soft references everywhere.** Loses B4's structural guarantee for the
  most security-relevant references Phase 6 adds.
- **Per-organization sweep (the notification pattern).** O(tenants) every
  tick; the Phase 5 audit already flagged it.
- **Broker ETA/countdown for retries.** Not durable across the delays a
  webhook retry needs (above).
- **A second outbox for integration work.** The outbox is for domain events
  (facts); "retry delivery 123 at 14:05" is a command and would violate D5.
