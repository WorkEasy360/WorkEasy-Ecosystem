# Phase 6 — The event model

## 1. Who owns which events (Tasks 6, 9, 90)

| Namespace | Owner | Schema lives in |
|---|---|---|
| `platform.*`, `commercial.*`, `integration.*`, and the Phase 1 roots `organizations.*`, `identity.*`, `entitlements.*` | Workeasy360 | `contracts/events/` (validated at publish) |
| `product.<product_code>.<aggregate>.<verb>[.<qualifier>…]` | the product registered as `<product_code>` | **the product** — Workeasy360 stores a declaration (name, version, description), never a schema |

`contracts/events/namespaces.py` is the single definition. Ownership is
structural: a product's namespace is derived from its **authenticated**
`product_code`. There is no request field in which a product could claim to
be another product, so `product.books.*` from the CRM credential, or
`platform.identity.*` from any product, is refused (Task 9, proved by
`test_phase6_acceptance` step 14 and `test_namespace_ownership_is_structural`).

The platform routes a product event as an opaque, bounded, versioned
envelope. It never interprets it beyond the bounded condition language an
organization's own automation rules apply to it, and it never creates
business meaning from it: there is no platform rule "if CRM deal won then
create invoice" and there cannot be one (Task 90).

## 2. Registration: the manifest declares events (Task 8)

Manifest version `2026-10-01` adds one optional field:

```json
"events": [
  {"type": "product.example.record.created", "version": 1, "description": "A record was created."}
]
```

- every type must be in the product's own namespace (checked at
  registration as well as at ingestion);
- at most 100 declarations, versions 1–999, descriptions ≤ 200 characters,
  no duplicates;
- `2026-09-01` remains accepted unchanged (legacy products keep working and
  simply publish nothing until they move to `2026-10-01`).

An event not declared in the product's **current, active** registration is
refused at ingestion (422). The declaration is what makes an event
routable, and what the subscription catalogue offers an organization.

## 3. The ingestion contract (Tasks 8, 10, 11, 51, 60, 61)

`POST /api/v1/integrations/events` — advertised in discovery as
`workeasy360_event_endpoint`. Form-encoded, exactly like the token and
entitlement endpoints, so client authentication is the same fields in the
same place (`client_assertion` per RFC 7523, or `client_secret_basic`):

| Field | |
|---|---|
| `organization_id` | the organization the event belongs to (a *claim*, see §4) |
| `event` | a JSON string: the envelope below |

```json
{
  "event_id": "UUID — the idempotency key",
  "event_type": "product.example.record.created",
  "event_version": 1,
  "occurred_at": "2026-09-23T10:00:00+00:00",
  "correlation_id": "UUID (optional)",
  "causation_id": "UUID of the event that caused this one (optional)",
  "data": { "…": "the product's payload" }
}
```

Checks, in order: client authentication (a suspended integration is refused
with `integration_suspended`) → per-client rate limit → body size (413
before parsing) → JSON validity (no `NaN`/`Infinity`) and envelope shape →
bounds → per-(client, organization) rate limit → namespace (403) →
declaration (422) → entitlement (403) → record.

| Bound | Default |
|---|---|
| encoded `event` | 64 KiB (`INTEGRATION_EVENT_MAX_BYTES`); request body 80 KiB |
| nesting depth of `data` | 8 |
| values in `data` | 2,000 |
| string length | 8,192 |
| array length | 500 |
| key length | 128 |
| `occurred_at` | required, timezone-aware, ≤ 5 min in the future, ≤ 7 days old |

The platform stores events for routing. It is not blob storage; files
belong in file infrastructure (Task 11).

Responses: `202 {"status": "accepted"}`, `200 {"status": "duplicate"}`, or an
OAuth-shaped error (`invalid_request`, `invalid_client`,
`integration_suspended`, `product_not_entitled`, `organization_inactive`,
`temporarily_unavailable`) — the shape the product's client library already
parses.

## 4. Binding the organization (Tasks 60, 61)

The `organization_id` in the request is only a claim. It is bound by asking
the live entitlement decision — the same one `/integrations/entitlements/verify`
answers — whether **this** product may operate on **that** organization
**for writes**, right now:

- not entitled, or an organization that does not exist → identical `403
  product_not_entitled` (no enumeration oracle);
- entitled but read-only (suspended, locked, pending deletion) → `403
  organization_inactive`: no new events, and so no new side effects;
- a platform failure reading the decision → `503 temporarily_unavailable`
  with `Retry-After`, never a denial.

## 5. Idempotency (Task 10)

`RoutedEvent` is unique on `(organization, source, event_id)` and the insert
runs in a savepoint, so the database — not a read-then-write — decides which
of two concurrent submissions wins. A repeat of the same event returns
`200 duplicate` and creates no second processing chain. The same
`event_id` with a different body (compared by a SHA-256 fingerprint over
type, version, `occurred_at` and `data`) is `409`: one idempotency key cannot
stand for two facts. Proved single, eight-times and concurrently
(`test_one_event_submitted_concurrently_is_accepted_once`: 8 threads → 1
accepted, 7 duplicates, 1 row).

## 6. The envelope and platform-owned events (Tasks 7, 66, 91)

Every routed event carries `event_id`, `event_type`, `event_version`,
`occurred_at`, `organization_id`, `source`, `correlation_id`,
`causation_id` and `chain_depth` (§7). The receiver-facing form is the
versioned `workeasy360.webhook.v1` body ([03-webhooks.md](03-webhooks.md) §2).

Two platform-owned events exist, both in `contracts/events/integration_events.py`:

- `integration.webhook.pinged.v1` — a connection's "send test event";
- `integration.schedule.fired.v1` — the trigger event of a scheduled rule.

Both are *direct*: created for exactly one consumer and never fanned out.

## 7. Causation and loop protection (Task 46)

A product may say which event caused its event (`causation_id`). If the
platform holds that event for the same organization, the new event's
`chain_depth` is the parent's plus one; otherwise zero. The depth comes from
the platform's own record, never from the producer. Automation refuses to
start from an event deeper than `AUTOMATION_MAX_CHAIN_DEPTH` (5) — see
[04-automation.md](04-automation.md) §6.

## 8. The platform's own domain events

The outbox (`OutboxEvent`, ADR-008) is unchanged and still carries the
platform's domain events to in-process handlers. Routing those events to
customer webhooks is **not** built: each would need a public, versioned,
privacy-reviewed payload contract first, and nothing in Phase 6's brief
required one. The routing layer is source-agnostic, so adding one later is a
new source of `RoutedEvent`, not a new pipeline.
