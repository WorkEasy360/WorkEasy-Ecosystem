# ADR-012 — Failure Isolation Strategy

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture / SRE

---

## Context

Workeasy360 depends on PostgreSQL, Redis, a broker, object storage, payment providers, email providers, AI providers, customer IdPs and many third-party connectors. It also hosts a dozen products in one process.

In a monolith, the default failure mode is total: one slow dependency consumes every worker, and the whole platform stops. Without explicit isolation, a failing AI provider takes down invoicing.

The requirement is that core workflows continue wherever safely possible, and that failures do not cascade.

## Decision

**Classify every dependency into a criticality tier, and enforce isolation with bulkheads, timeouts, bounded retries, circuit breakers, prioritised load shedding and kill switches.**

- **Tiers:** 0 Vital (PostgreSQL primary) · 1 Load-bearing (Redis, S3, replica, customer IdP) · 2 Deferrable (broker, workers, payment provider, search) · 3 Optional (email, AI, connectors, analytics, tax).
- **Rule:** a Tier-N component may never fail a Tier-(N−1) or lower operation.
- **Bulkheads:** seven queues by workload class with separate worker pools, separate database connection pools per tier, reports on a read replica, per-tenant concurrency caps, per-product kill switches.
- **Timeouts are mandatory and explicit** on every external call, enforced by a system check.
- **Retries are bounded (3–5), jittered, idempotent-only, and never nested.**
- **Circuit breakers on external dependencies only** — never on PostgreSQL.
- **Backpressure sheds by priority:** batch, analytics and AI first; interactive requests last.
- **Degradation is visible** in the UI, never silent.
- **No external I/O inside a database transaction**; `transaction.on_commit` is not used for anything that must not be lost.

## Alternatives considered

### A. Best-effort resilience (retry and hope)

- **Pro:** no design effort.
- **Con:** the default outcome is a total outage from a partial failure; unbounded retries amplify load on a struggling dependency.
- **Rejected.**

### B. Full microservice isolation

Process boundaries as the isolation mechanism.

- **Pro:** hard isolation; one service's failure cannot exhaust another's threads.
- **Con:** the costs in [ADR-001](ADR-001-modular-monolith-first.md); and it does not actually solve the problem — a synchronous call chain across services cascades just as readily, often worse. Isolation comes from asynchrony, bulkheads and timeouts, not from process boundaries.
- **Rejected:** process boundaries are neither necessary nor sufficient for failure isolation.

### C. Service mesh with sidecar-managed resilience

- **Pro:** retries, timeouts and breakers as infrastructure configuration.
- **Con:** solves inter-service communication problems we do not have; substantial operational complexity; does not address in-process concerns such as worker pools and connection pools, which are where our risk actually lies.
- **Rejected as premature.**

### D. Tiered dependencies with explicit isolation patterns (selected)

## Why selected

1. **Tiering forces the decision to be explicit.** "Can email failure block a signup?" has a documented answer, decided once, rather than being rediscovered per feature.
2. **Bulkheads are cheap and effective in a monolith.** Separate queues and pools give most of the isolation benefit of separate services at a fraction of the cost.
3. **Timeouts are the highest-leverage control.** Most cascades are slow dependencies consuming capacity; a slow dependency is more dangerous than a down one, and short timeouts convert the former into the latter.
4. **Asynchrony via the outbox** removes external dependencies from the write path entirely — the structural fix rather than a mitigation.
5. **Prioritised shedding preserves what matters.** Under load, users keep working while batch work waits.
6. **Kill switches provide a manual last resort** that exists before the incident rather than being improvised during one.

## Consequences

**Positive**

- Only PostgreSQL primary loss is a real outage; every other single failure leaves the platform usable.
- One product's incident is containable.
- Recovery is throttled, so a draining backlog cannot re-kill the system.
- Failure behaviour is specified as a contract (the degradation matrix) and testable.

**Negative, accepted**

- Seven queues and multiple pools are more infrastructure to run and size. Mitigated by one image with different entrypoints and by autoscaling on queue age.
- Circuit breakers add state and their own failure modes. Limited to external dependencies where the benefit is clear.
- Degraded states require UI design work per feature.
- Some operations legitimately fail closed (locks when Redis is down). Correct, but it means "graceful degradation" is not universal, and that nuance must be understood.
- Testing failure paths costs engineering time. Non-negotiable: an untested degradation path is a hypothesis.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| A new external call ships without a timeout | **High** | System check `W360.E008` fails the build |
| Retry amplification across layers | High | One retry layer per logical operation; reviewed |
| Queue misassignment defeats a bulkhead | Medium | `W360.E007` requires an explicit queue |
| Breaker opens spuriously and degrades unnecessarily | Medium | Tuned thresholds; per-provider metrics; half-open probes |
| Degradation invisible to users | Medium | Required UI treatment per degraded state |
| Recovery stampede after a long outage | Medium | Fixed batch sizes, bounded relay concurrency, throttled restart order |
| Tier assignment drifts as features change | Medium | Tier is declared per dependency and reviewed when a dependency is added |
| Untested failure path fails differently in production | High | Failure tests are release gates |

## Future extraction path

The patterns here are the same ones used across service boundaries. Extraction would move some bulkheads from in-process (thread and pool separation) to physical (separate deployments) — a strengthening, not a redesign.

The tiering itself is deployment-independent and survives any topology change.

## Related

- [09-failure-resilience.md](../09-failure-resilience.md), [10-performance-concurrency.md](../10-performance-concurrency.md), [11-observability.md](../11-observability.md)
- [ADR-001](ADR-001-modular-monolith-first.md), [ADR-008](ADR-008-transactional-outbox.md)
