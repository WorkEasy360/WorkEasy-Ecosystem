# Phase 6 — Automation

Generic, organization-configured automation: **when** (an event or a
schedule) **if** (a bounded condition set) **then** (one to five registered
actions). No product-specific condition or action exists, and none can be
added without registering a generic one (Task 90;
`test_no_registered_action_or_connector_is_product_specific`). The execution
model is [ADR-026](../architecture/adrs/ADR-026-automation-execution-semantics.md).

## 1. Rules and versions (Tasks 31, 39)

| Status | Runs? | Editable? | Next |
|---|---|---|---|
| `draft` | no | yes | `active`, `archived` |
| `active` | yes | yes (new version) | `paused`, `archived` |
| `paused` | no; executions already created still finish | yes | `active`, `archived` |
| `archived` | no (terminal, kept for history) | no | — |

- **Every save creates an immutable `AutomationRuleVersion`.** A trigger
  refuses updates to a version's definition. `current_version` points at the
  newest one.
- **An execution pins the version it was created with** and runs exactly
  that. Editing, pausing or archiving the rule never changes an execution
  already in flight.
- **Edits are optimistic:** `PUT` carries `expected_revision`, and a stale
  one is `409` with nothing written
  (`test_a_stale_or_invalid_edit_changes_nothing`).
- **Status changes are conditional updates** on `(status, revision)`: two
  racing activations have one winner (`test_racing_activations_have_one_winner`).
- **Activation re-validates the definition against today's state.** A
  connection disabled, or a product no longer entitled, since the rule was
  written blocks activation. It never produces a rule that fails at run time.
- At most 100 non-archived rules per organization
  (`AUTOMATION_MAX_RULES`), and 5 actions per rule.

## 2. The definition

```json
{
  "trigger":    {"kind": "event", "event": "product.example.record.created.v1"},
  "conditions": {"match": "all", "rules": [
                  {"path": "payload.amount", "op": "greater_than", "value": 100}]},
  "actions":    [{"type": "connector.invoke", "config": {"connection_id": "…"}}]
}
```

Everything is validated at save time, before any write (D-016):

- the trigger is checked against the organization's event catalogue (only
  events of products it is entitled to, as declared);
- each condition is checked against the grammar;
- each action is checked by its own `validate`.

The stored form is normalised: actions get stable ids `a1…a5` and a
`version`.

## 3. Triggers (Tasks 32, 42–45)

**Event:** an exact `<type>.v<version>`, or a product wildcard
`product.<code>.*`. Matching is an indexed `IN` over at most two candidate
filters per event, so rule count does not change query count
([08-performance.md](08-performance.md)).

**Schedule:** three bounded kinds. There is no cron grammar and no way to
say "every second":

| Kind | Shape | Notes |
|---|---|---|
| `interval` | `{"kind": "interval", "every_minutes": 60}` | ≥ 15 (`AUTOMATION_MIN_INTERVAL_MINUTES`), ≤ 7 days; aligned to multiples of N since the Unix epoch, so "every 60" runs on the hour, UTC |
| `daily` | `{"kind": "daily", "at": "09:00", "timezone": "Europe/London"}` | IANA zone, never an offset |
| `weekly` | `{"kind": "weekly", "days": ["mon", "fri"], "at": "09:00", "timezone": "…"}` | |

- **DST:** computed with `zoneinfo`, with no hand-written offset arithmetic.
  - A local time that does not exist (the spring-forward gap) runs just
    after the gap. The run is never skipped.
  - An ambiguous local time (the autumn repeat) runs **once**, at its first
    occurrence.
- **Missed runs are coalesced.** After downtime, the rule fires once for the
  most recent missed occurrence, and `missed_occurrences` on the fired event
  says how many were skipped. There is never a burst of catch-up runs.
- **A firing is an event.** `integration.schedule.fired.v1`, with a
  deterministic id `uuid5(rule, occurrence)`, is recorded *direct*: for that
  rule only, never fanned out. Schedule- and event-triggered rules share one
  execution model, and two workers firing the same occurrence produce one
  execution (`test_one_schedule_occurrence_fires_once_under_contention`).
- The next run is a `DueWork` timer row, armed in the same transaction as
  the rule change that implies it ([05-failure-and-operations.md](05-failure-and-operations.md) §2).

## 4. Conditions (Tasks 33, 34)

A condition set is **data evaluated by a fixed interpreter**. Nothing parses
Python, JavaScript, SQL, Jinja or a customer regex, and nothing reaches
`eval` or a template engine (`test_automation_evaluates_no_code`).

| | |
|---|---|
| Combinator | `match: all` or `any`; one level, no nesting |
| Operators | `equals`, `not_equals`, `contains`, `exists`, `greater_than`, `less_than`, `in` |
| Paths | `event.type`, `event.version`, `event.source`, or `payload.<segment>…` (≤ 8 segments; letters/digits/`_`/`-` or a list index; `__dunder__` segments refused) |
| Bounds | ≤ 20 rules, strings ≤ 256 characters, `in` lists ≤ 20, finite numbers only |
| Semantics | type-strict (`1` ≠ `true`, `"1"` ≠ `1`); ordering only on numbers; a missing field is false except for `exists` |

The event envelope's internals (organization id, correlation id) are
deliberately not addressable.

## 5. Actions (Tasks 35–38)

Actions are registered `ActionSpec`s: key, version, `validate`, `execute`,
and declared config fields that the UI renders. The engine looks an action up
by key and never branches on which one it is.

| Action | Config | Effect (a record, never a network call) |
|---|---|---|
| `connector.invoke` v1 | `connection_id`, `capability` (default `deliver_event`) | a webhook `Delivery` with `origin=automation`, unique on `(execution, action)` |
| `notification.send` v1 | `membership_ids` (≤ 10, active members of this organization at save **and** run time) | an e-mail `Notification` per member through the existing service, idempotency key `automation:<execution>:<action>:<member>` |

`notification.send` carries only the rule name and the event type, never
payload data.

## 6. Execution semantics (Tasks 39–41, 46, 47)

```
route_event ─▶ AutomationExecution(pending, pinned version, chain_depth)
                  │ DueWork timer + after-commit enqueue
run_execution: tx1 claim attempt N (running, lease)
               tx2 fence on N → conditions (pure) → every action → results   ← all or nothing
               tx3 (only if tx2 raised) retry with equal-jitter backoff, or dead
```

| Status | Meaning |
|---|---|
| `succeeded` | conditions matched, every action completed |
| `skipped` | conditions did not match; nothing was done |
| `failed` | conditions matched, an action failed permanently (e.g. its connection was disabled since) |
| `retry` | a transient error; backoff 10 s × 2ⁿ, cap 600 s, at most 3 attempts |
| `dead` | never ran: `chain_depth_exceeded`, `rate_limited`, `retries_exhausted`, or its trigger event is gone |

- **No external side effect inside a transaction (Task 40).** Actions create
  rows. Deliveries and notifications are sent by their own pipelines, after
  commit, with their own retries.
- **No duplicate effects on retry (Task 41).** Every effect is idempotent on
  `(execution_id, action_id)`, so even a retry after a lost commit
  acknowledgement creates nothing twice.
- **Leases and fencing:** a worker that dies mid-execution is recovered when
  its lease lapses. A late worker cannot overwrite a newer attempt.
- **Loop protection (Task 46)** is bounded twice:
  - **Chain depth.** Depth is derived from the platform's own record of the
    causing event. An event deeper than `AUTOMATION_MAX_CHAIN_DEPTH` (5)
    starts no automation
    (`test_a_loop_through_an_external_system_stops_at_the_depth_limit`).
  - **Per-rule hourly cap.** 1,000 executions per rule per hour
    (`AUTOMATION_MAX_EXECUTIONS_PER_RULE_PER_HOUR`) stops a loop that drops
    causation on the way through an external system.
- **A suspended or read-only organization runs nothing new.** Routing holds
  its events; execution re-reads the live organization status before it
  writes.

## 7. Permissions

| Permission | Grants | Step-up |
|---|---|---|
| `platform.automation.view` | list rules, versions, executions | — |
| `platform.automation.manage` | create, edit, activate, pause, archive | **dangerous**: AAL 2 |

Both are granted to the system `owner` and `admin` roles (`authz.0004`).
