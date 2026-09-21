# ADR-005 — Product Entitlement Model

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture

---

## Context

Products must be purchasable individually, in predefined bundles, in an ecosystem bundle, in partner-specific packages, in custom enterprise deals, on trials, and per seat or per organization. Customers upgrade, downgrade, lapse and recover.

A product must answer "may this organization use this?" without knowing any of that. And a billing failure — a provider timeout, a delayed webhook, a stuck worker — must never produce inconsistent authorization state.

## Decision

**Entitlements are a read model in `platform/entitlements`, derived from commercial state by a single writer, `commercial/provisioning`.**

- `platform/entitlements` owns `Entitlement`, `FeatureEntitlement`, `SeatGrant`, `SeatAssignment`, `EntitlementSnapshot` and the `check()` / `feature()` / `quota()` / `has_seat()` API.
- `commercial/provisioning` is the **only** module permitted to write those tables (linter-enforced).
- `compute_entitlements(org, at)` is a **pure, deterministic function of durable database state** — never of a provider response. `apply_entitlements()` is idempotent and diff-based.
- Overlapping grants resolve **most generous wins**.
- Lifecycle: `trial → active → past_due → grace → suspended → cancelled → expired`, where `suspended` is **read-only, not lockout**.
- A scheduled reconciliation recomputes all organizations and alerts on drift.
- Manual `EntitlementGrant` is an *input* to the computation, so reconciliation preserves rather than reverts it.

## Alternatives considered

### A. Products read subscriptions directly

- **Pro:** no extra layer; one source of truth.
- **Con:** creates a legal `products → commercial` import edge. Product code ends up branching on plan names, so repackaging becomes a code change in every product. Bundles, contracts and partner packages each need per-product handling.
- **Decisive objection:** it makes packaging a product-code concern, which contradicts the vision.

### B. Entitlements inside `commercial/`

- **Pro:** co-located with their cause.
- **Con:** same fatal flaw — `products → commercial` becomes legal, and once legal it will be used for more than entitlement checks.
- **Rejected:** boundary integrity is the point.

### C. Entitlements computed on the fly from subscriptions

- **Pro:** no materialised state, no drift possible.
- **Con:** several joins across subscriptions, items, plan versions, bundles and grants on every request; resolution of overlapping grants repeated constantly; catastrophic under list-query load.
- **Rejected on performance**, though it is exactly what the reconciliation job does — periodically, as the correctness check.

### D. Entitlements in a token/JWT claim

- **Pro:** zero lookup cost.
- **Con:** cannot be revoked before expiry; upgrades do not take effect until re-login; token size grows with products; a stale claim grants access that was cancelled.
- **Rejected:** unacceptable revocation semantics for a paid product.

### E. Read model in `platform/`, single writer in `commercial/` (selected)

## Why selected

1. **It makes the forbidden edge enforceable.** `products → commercial` can be banned outright because products need nothing from it.
2. **Products stay packaging-agnostic.** Bundles, contracts, partner deals and trials all collapse into "entitled / not entitled with this value".
3. **Deterministic recomputation is the recovery mechanism.** Because the computation reads only durable state, it can be re-run at any time — which is what makes billing failures non-corrupting.
4. **Single writer means one place to audit, test and fix.** Drift has exactly one possible source.
5. **Fast reads.** A per-org snapshot plus a version-keyed cache makes a check a cache hit.
6. **Support can intervene safely**, because grants are inputs to the computation rather than manual edits that the next job reverts.

## Consequences

**Positive**

- Products never see commercial concepts.
- Recovery from any inconsistency is one reconciliation pass.
- Out-of-order and duplicate provider webhooks converge to the same state.
- New packaging strategies require no product changes.
- Entitlement state is inspectable in one row per organization.

**Negative, accepted**

- Derived state can lag. Bounded and monitored; staleness means the customer keeps what they had, which is the safe direction.
- Two representations of the same commercial truth. Justified by the boundary and performance benefits, and kept honest by reconciliation.
- Entitlement checks are on the hot path of nearly every request. Mitigated by request-local + Redis + snapshot caching, with a 1 ms p50 budget.
- Provisioning logic is a concentration of complexity. Accepted: better one well-tested translator than packaging logic spread across a dozen products.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Provisioning bug grants or denies incorrectly | **High** | Pure function with exhaustive unit tests; reconciliation with alerting; drift as a monitored SLI |
| Cache serves stale entitlement after downgrade | Medium | Version-keyed invalidation; short TTL; revocation events bump immediately |
| Reconciliation reverts a legitimate manual fix | Medium | Grants are inputs, not overrides — structurally impossible |
| A product caches entitlements locally | Medium | Explicit anti-pattern; fitness rule; code review |
| Entitlement service becomes a bottleneck | Medium | Snapshot + cache; measured against the 1 ms budget |
| Billing outage causes revocation | **High** | Invariant: absence of information never revokes. Revocation needs a positive durable decision |

## Future extraction path

`platform/entitlements` is **never extracted** — it sits on the request path alongside authorization.

`commercial/provisioning` *is* extractable: it is already event-driven, asynchronous and off the read path. If commercial systems grow complex enough to warrant separation, provisioning becomes a service writing to the entitlement store through a narrow API, and products are unaffected because they only read.

## Related

- [06-entitlements.md](../06-entitlements.md), [05-commercial-model.md](../05-commercial-model.md)
- [ADR-004](ADR-004-rbac-authorization.md), [ADR-006](ADR-006-product-catalog-and-bundles.md)
