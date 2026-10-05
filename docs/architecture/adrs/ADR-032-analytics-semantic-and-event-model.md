# ADR-032: Analytics is product-defined metrics over routed events, projected into rebuildable aggregates

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Architecture
**Depends on:** [ADR-024](ADR-024-integration-platform-data-model-and-due-work.md), [ADR-030](ADR-030-ai-gateway-and-provider-ownership.md)

---

## Context

The platform needs unified analytics across independently deployed products
without querying their databases, without owning their business meaning, and
without a warehouse it has no measured need for. Phase 6 already accepts
authenticated, namespaced, versioned, idempotent product events.

## Decision

**One ingestion path.** Analytics consumes the events products already publish
to `/integrations/events`. `integration/routing` gained a consumer registry;
analytics registers one. For each routed product event it looks up the active
metrics the owning product defined over that event type and version, extracts
a **fact** per metric, and records it in the routing transaction - so a fact
exists exactly once per event and metric, and no second ingestion endpoint or
credential exists.

**Products own meaning; the platform stores integers.** A metric is registered
by its product over its credential, in its own namespace, over one of its own
declared events: a key, a unit, `sum` or `count`, the integer field it
measures, optionally a currency field, and at most three dimensions whose
values are an **enumerated allowlist** (anything else becomes `other`, a
missing value `unknown`). No expression, schema or SQL is accepted. Money is
integer minor units with its currency, and currency is part of every grouping.

**Facts are the record; aggregates are a projection.** Facts are immutable
(a trigger refuses any change but the projection mark) and bucketed by when the
event *occurred*. A per-organization projection, driven by the due-work timer,
adds batches of facts to hourly and daily aggregates with one upsert per batch
and marks them projected in the same transaction, under an advisory lock the
rebuild shares - so every fact is counted exactly once, late events correct
their historical buckets, and `rebuild` recreates aggregates from facts at any
time. `reconcile` reports drift without writing.

**Queries are bounded and scoped.** A query names a metric, a range, `hour`,
`day` or `month`, and up to two of the metric's own dimensions - nothing that
becomes SQL text. Ranges, buckets and worst-case rows are bounded before the
database is touched. A product reads its own metrics and those an owner
shared with it explicitly, for organizations it is entitled to; an
organization's administrator reads metrics of products the organization
holds. Anything else is `metric_not_found`, the same answer as a key that does
not exist. Every answer states its freshness.

**Platform operational metrics stay separate.** AI usage, subscription counts
and integration health are served by their own read models and the control
plane, not mixed into product business analytics.

## Consequences

- A product gets analytics by declaring a metric; it publishes nothing twice.
- Facts outlive routed-event retention (400 days vs 30), so aggregates can be
  rebuilt for the retained window; older buckets are final.
- A metric counts events routed after it was registered (no backfill yet).
- Buckets are UTC; organization-local calendars are future work.

## Alternatives rejected

- **A separate analytics ingestion endpoint.** A second trust surface and a
  second publish for every event.
- **Aggregating inside the routing transaction.** Couples webhook routing to
  aggregate contention and makes a projection fault a routing fault.
- **A warehouse (ClickHouse, a lake).** No measured need; PostgreSQL
  aggregates with bounded queries serve the foundation.
- **Free-form dimensions with a cardinality cap.** The cap is hit by the first
  product that sends an email address; an allowlist cannot be.
