# ADR-030: One AI gateway owns providers, spend and safety; products own meaning

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Architecture / Security
**Refines:** [ADR-011](ADR-011-shared-ai-platform.md) (shared AI platform)
**Depends on:** [ADR-019](ADR-019-product-service-authentication.md), [ADR-024](ADR-024-integration-platform-data-model-and-due-work.md), [ADR-025](ADR-025-outbound-http-and-ssrf-boundary.md)

---

## Context

ADR-011 decided that AI is a shared capability behind a narrow port with
mandatory cost, safety and failure controls, and that no product calls a
provider directly. It placed the capability in `platform/ai`. Phase 8 builds
it, and three facts change where and how:

1. Products are separate applications (ADR-017). They reach the platform over
   HTTP with their Phase 3 client credential, never by import.
2. Every outbound request must go through `integration/egress` (D9, ADR-025),
   which `platform/` may not import.
3. The gateway must meter spend against the commercial entitlement without
   becoming part of billing.

## Decision

**A new top-level package, `intelligence/`, beside `commercial/`.** It sits
above `integration/` (egress, the due-work timer, routed events) and is an
*independent sibling* of `commercial/` in the import-linter layers: neither may
import the other. Entitlement is read through `platform/entitlements` like
everywhere else; estimated AI cost can never become billing truth by accident.
Internal layering: `ai` (mechanism) and `analytics` at the bottom, `rag` above
`ai`, `gateway` on top.

**One gateway entry point.** A product invokes a *registered skill* for an
organization at `POST /integrations/ai/invoke`. The gateway decides everything
before anything is retrieved or sent: organization active, product entitled
for writes (the same live decision as the entitlement API), AI granted by the
plan through the feature `<product>.ai.monthly_budget_micros` (absent or zero
means no AI), AI not switched off by the organization or the platform, the
skill resolvable *by this caller*, the input valid against the skill's schema.

**Providers are adapters, not SDKs.** `intelligence/ai/providers` holds one
small adapter per vendor (Anthropic Messages for generation, OpenAI for
embeddings and an optional second generation vendor). Adapters build a
request and parse a response; they are sandboxed by an import-linter contract
(no Django, no settings, no credentials store, no network). One module,
`ai/transport.py`, sends through egress - the only addition to the
"who may send" allowlist. Endpoints come from the adapter; a per-adapter
override exists for development and tests and production pins it empty, so no
staff member or product can point the gateway at a URL.

**Credentials by reference.** A provider row stores `credential_ref`; the key
lives in the secret manager as `W360_AI_CREDENTIAL_<REF>` and is read live per
call - the `SigningKeyProvider` pattern. No column, response, log or prompt
ever holds a key; staff see only whether one is configured.

**Models are registry data.** Capabilities, tier, limits, priority and
immutable, versioned prices live in the registry; a skill declares
requirements, the platform selects. A pinned model is exactly that model.
Fallback is the next candidate - the same vendor, or another vendor only when
the organization allowed it - and is recorded.

**Spend is reserved before, settled after, under a row lock.** Per organization
and month there is one budget row for the organization and one per product.
A reservation locks them (organization first), checks `spent + reserved +
estimate` against every limit and the in-flight count against the concurrency
cap, and writes the execution and its lease timer in the same transaction.
Settlement records actual usage at the pinned price. A caller that dies is
settled by its timer at the full reserved amount. If accounting cannot be
recorded, the request is refused (fail closed); Redis backs only the rate
limits in front, which degrade, never the quota.

**Estimated cost is not billing.** Integer micro-units of `USD`, rounded up
once per call, at an immutable price version. It controls spend; no invoice
is derived from it.

**The metering tables are exempt from the R-10 suspension write block**
(`tenancy_write_block_exempt`, the second use after `audit.AuditEvent`). They
record spend that already happened at a provider; new AI work for a
non-writable organization is refused before anything is reserved. Refusing to
record a call already made would under-count spend - the opposite of what
suspension is for. The retrieval tables carry the same exemption for the
equivalent reason: deletion and revocation are privacy obligations that must
complete while an organization is suspended (ADR-031). Recorded as debt
register D-023.

**AI is never an authority.** Model output is returned to the product that
asked. Nothing in `intelligence/` imports a module that writes permissions,
entitlements, seats, identity, organization state or anything commercial
(import-linter `ai-is-never-an-authority`), and the model is offered no tools.

## Consequences

- Products get governance - entitlement, quotas, audit, safety, failure
  isolation - by calling one endpoint, and cannot get provider access any
  other way.
- Adding a vendor is an adapter and a registry row; product code does not
  change.
- The platform owns no product's prompt: a product registers and versions its
  own skills in its own namespace.
- A request can over-count by at most one reservation after a crash; it cannot
  under-count.
- Provider-specific features arrive only when an adapter and a skill
  capability expose them (accepted in ADR-011).

## Alternatives rejected

- **`platform/ai` with a port implemented in `integration/`.** Would need
  runtime wiring across the platform/integration boundary for every call and
  still leave spend accounting beside commercial concerns. A top-level
  sibling states the boundary structurally.
- **Vendor SDKs.** Each brings its own HTTP stack that resolves and connects
  outside egress, and several overlap. The request shapes are small.
- **Quota in Redis.** Fast, but a cache outage would either stop AI or make it
  unlimited; the brief requires the quota to be authoritative.
- **Post-paid metering without reservation.** Eight concurrent requests at the
  edge of a budget would all pass.
