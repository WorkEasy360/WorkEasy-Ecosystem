# ADR-011 — Shared AI Platform

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture / Security

---

## Context

Several products will want AI capabilities: CRM email drafting and deal summaries, Books document extraction, Support reply suggestions and triage, Projects status summaries, Marketing copy generation.

Each product could integrate a provider SDK directly. That would produce N implementations of prompt handling, cost control, retries, PII handling, output validation and failure behaviour — each with its own bugs, and each an independent way to leak tenant data to a third party or run up an unbounded bill.

AI also has properties that ordinary dependencies do not: non-deterministic output, variable and sometimes very high latency, per-token cost, and the ability to emit content that must not be trusted.

## Decision

**A shared `platform/ai` capability behind a provider-agnostic port, with mandatory cost, safety and failure controls. No product calls an AI provider SDK directly.**

- `contracts/ports/ai.py` defines the interface; `integration/connectors/<vendor>/` implements it. Default provider: Claude via the Anthropic API.
- **Every AI feature is entitlement-gated** through a declared feature key, so AI is sellable packaging, not a hidden cost.
- **Per-organization and per-request token/cost budgets**, enforced before the call. Exceeding a budget is a clear, user-visible refusal.
- **Tier 3 dependency**: AI unavailability never blocks a workflow ([09](../09-failure-resilience.md)). Circuit breaker per provider; explicit timeouts (30 s interactive, 120 s batch).
- **AI output is untrusted input.** Validated against a schema before touching domain logic. It is never used to make an authorization, entitlement or billing decision.
- **Prompt assembly is centralised and tenant-aware.** Context is gathered through existing selectors, so a prompt cannot include data the requesting user could not otherwise read.
- **Data handling is explicit:** what is sent, to whom, retention, and whether it may be used for training — disclosed per feature, with an organization-level opt-out.
- All AI calls are audited: feature, org, actor, model, token counts, cost, latency, outcome.

## Alternatives considered

### A. Per-product provider integration

- **Pro:** product teams move fast; no shared abstraction to agree on.
- **Con:** N implementations of cost control, retries, PII redaction and output validation; inconsistent failure behaviour; no platform-wide cost visibility; changing provider means N migrations; N chances to leak tenant data.
- **Rejected:** the cross-cutting concerns are exactly the ones that must not be duplicated.

### B. A single "AI service" that owns AI features end to end

A module that implements deal summarisation, invoice extraction, and so on.

- **Pro:** centralises everything AI-related.
- **Con:** it would need to understand CRM deals, Books invoices and Support tickets — domain knowledge belonging to products. It would become a module that depends on every product, inverting the dependency rules.
- **Rejected:** AI is a *capability*, not a domain. Products own their prompts and their interpretation; the platform owns transport, safety and cost.

### C. No AI platform; AI as an external product

- **Pro:** zero complexity now.
- **Con:** AI features are already a competitive expectation; retrofitting governance after several products have shipped their own integrations is far more expensive than providing the shared capability first.
- **Rejected.**

### D. Shared port + per-product prompts (selected)

## Why selected

1. **Cross-cutting concerns are solved once**: cost, timeouts, breakers, redaction, validation, audit.
2. **Domain knowledge stays with the domain.** CRM knows what a good deal summary is; the platform does not.
3. **Provider independence is real** because the port exists from day one. Switching or adding a provider (or routing by cost/capability) is an adapter change.
4. **Cost is controlled centrally**, which matters because AI cost scales with usage in a way that can outrun revenue silently.
5. **It is sellable.** Entitlement-gating AI features makes them packaging rather than an uncontrolled expense.
6. **Tenant data handling is auditable in one place**, which is what enterprise customers ask about.

## Consequences

**Positive**

- Consistent AI failure behaviour across the platform.
- One place to add caching, prompt-injection defences, model routing or evaluation.
- Cost visible per organization, per feature and per product.
- Provider switching does not touch product code.
- One data-handling posture to describe to customers and auditors.

**Negative, accepted**

- An abstraction layer over provider SDKs, which will occasionally lag provider-specific features. Mitigated by keeping the port narrow and allowing a documented escape hatch for a specific capability, behind review.
- Products cannot adopt a new provider capability instantly.
- Prompt versioning and evaluation infrastructure is additional work — deferred until there are enough AI features to justify it, but the audit record makes retrospective evaluation possible.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Tenant data sent to a provider without authority | **High** | Context gathered via tenant-scoped selectors; per-feature disclosure; org opt-out; audit of every call |
| Prompt injection via customer data | **High** | Output treated as untrusted; schema validation; never used for authorization; no tool access to write paths without explicit confirmation |
| Runaway cost | High | Per-request and per-org budgets enforced pre-call; alerting on spend rate; kill switch per feature |
| AI latency degrades UX | Medium | Timeouts; async for anything slow; never on a blocking path |
| Over-reliance on AI output for financial data | **High** | Extraction results are *proposals* requiring human confirmation before posting to a ledger |
| Provider outage | Medium | Tier 3; breaker; clear degraded state |
| Model change alters behaviour silently | Medium | Model version pinned per feature; changes are deliberate and release-noted |

## Future extraction path

`platform/ai` is one of the **most likely extraction candidates** ([13](../13-deployment-evolution.md)): inference workloads have a genuinely different scaling profile, are bursty, and may eventually need specialised hardware.

Because it already sits behind a port and is invoked asynchronously for anything non-trivial, extraction is an adapter swap. Product code does not change.

## Related

- [09-failure-resilience.md](../09-failure-resilience.md), [12-security.md](../12-security.md), [06-entitlements.md](../06-entitlements.md)
- [ADR-001](ADR-001-modular-monolith-first.md), [ADR-012](ADR-012-failure-isolation-strategy.md)
