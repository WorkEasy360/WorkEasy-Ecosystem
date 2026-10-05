# ADR-026: Automation executes by creating durable records, never side effects

**Status:** Accepted
**Date:** 2026-09-23
**Deciders:** Architecture
**Depends on:** [ADR-008](ADR-008-transactional-outbox.md), [ADR-024](ADR-024-integration-platform-data-model-and-due-work.md)

---

## Context

Phase 6 adds generic automation: a trigger (an event, or a schedule), a
bounded condition set, and a short list of registered actions. Three
questions decide whether it is safe:

1. What happens when an execution is retried after it already did part of
   its work?
2. What happens when the rule is edited while an execution is in flight?
3. What stops a loop, including one that passes through a system the
   platform cannot see?

## Decision

**Actions create durable records; pipelines perform side effects.** An
action's effect is a row - a webhook `Delivery` (for `connector.invoke`) or
a `Notification` (for `notification.send`) - never a network call. The
execution evaluates conditions and runs every action inside **one database
transaction** with its own status update, so an execution commits all its
effects or none. Each effect is idempotent on `(execution_id, action_id)`
(a unique constraint on deliveries; the notification service's idempotency
key), so even a retry after a commit whose acknowledgement was lost creates
nothing twice. External side effects then happen in the delivery and
notification pipelines, with their own bounded retries and documented
at-least-once semantics.

**Executions pin an immutable rule version.** Every edit creates a new
`AutomationRuleVersion` (a trigger refuses updates to a version's
definition). An execution records the version current when it was created
and runs exactly that. Editing, pausing or archiving the rule never changes
what an existing execution does; a paused rule creates no new executions.

**Loops are bounded twice.** An ingested event may name the event that
caused it; its chain depth is derived from the platform's *own* record of
that event (never trusted from the producer), and an event deeper than
`AUTOMATION_MAX_CHAIN_DEPTH` starts no automation (the execution is recorded
dead, `chain_depth_exceeded`). Because a loop through an external system
may drop causation, a per-rule cap (`AUTOMATION_MAX_EXECUTIONS_PER_RULE_PER_HOUR`)
stops any rule running away regardless.

**Schedules are events.** A schedule firing records a platform-owned
`integration.schedule.fired` event with a deterministic id per (rule,
occurrence), so schedule-triggered and event-triggered rules share one
execution model, and a double firing is one execution. Missed runs are
coalesced into one run for the latest missed occurrence.

## Consequences

- No execution retry can duplicate an effect the platform controls; the
  remaining duplicates are the receiver-side at-least-once duplicates every
  webhook consumer handles by `event.id`.
- A new action is a registered `ActionSpec` (validate + execute), with no
  engine change - and must itself only create records through an approved
  service. A future action that needs a synchronous external call would
  need a new ADR.
- Conditions cannot express everything (no expressions, arithmetic,
  nesting). Deliberate: they are data evaluated by a fixed interpreter, with
  no path to code execution.
