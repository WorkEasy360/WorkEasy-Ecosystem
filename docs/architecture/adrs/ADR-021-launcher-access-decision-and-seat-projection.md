# ADR-021 — The launcher's product-access decision, the seat read projection, and platform layer P4

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Architecture
**Depends on:** [ADR-005](ADR-005-product-entitlement-model.md), [ADR-017](ADR-017-products-are-separate-applications.md), [ADR-018](ADR-018-openid-connect-for-product-sso.md)

---

## Context

Phase 3's audit named the gap precisely and recommended where to close it:

> The gap is real: an organization holding CRM with 5 of 5 seats assigned
> still authenticates user 6 into CRM. … A product cannot enforce it on the
> platform's behalf either. … So "the product will handle it" is not
> available, and the gap closes in the platform or not at all.
> ([17-audit-findings.md](../../phase-3/17-audit-findings.md), "Seat trigger")

Phase 4's brief poses this as two questions that turn out to be the same
question asked twice: *"may this specific member launch this specific
product right now?"* — once at the OIDC authorization endpoint (Task 11),
and once at the app launcher, where "which apps can I open?" is exactly that
question asked in bulk, before any redirect exists to answer it through
(Task 9).

Two things already existed and needed no new design:

- `Product.licensing_mode` (`contracts/enums.LicensingMode`) already
  distinguished per-seat from per-organization products.
- `EntitlementService.has_seat()` already existed on the read contract in
  `contracts/ports/entitlements.py`, unused.

What did not exist was a single function that composed them, and a place
for that function to live that did not create a layering violation.

## Decision

### 1. `require_seat()` joins the OIDC flow directly

`platform/ecosystem/authorization.py` gains `require_seat()`, called
immediately after `require_entitlement()` in both `authorize()` and
`_revalidate_subject()` (the redemption-time re-check). It reads a
product's licensing mode through the existing `contracts/ports/catalog.py`
port (widened to carry `licensing_mode`, described below) and, for a
per-seat product, calls `EntitlementGate.has_seat()` (widened to appear on
the raising-form protocol, not only the read-only one). A new
`product_no_seat` OAuth error code - the `no_seat` `EntitlementReason` that
had been reserved since Phase 3 - is emitted, redirected to the product
exactly as `product_not_entitled` already is.

This is *not* delegated to the launcher's decision below, even though the
launcher answers the identical question. A token must never be issued to a
request the launcher never saw pre-flight (a product's own deep link,
skipping the launcher entirely) or whose launcher answer is now stale (a
seat unassigned in the seconds since the launcher rendered). The OIDC flow
re-asks, the same way it already re-asks entitlement at redemption.

### 2. `platform/launcher`, a new module, a new layer

`can_launch_product(ctx, product_code)` - Task 9's one authoritative
decision - needs three things no single existing module has all of:
entitlement and seat state (`platform/entitlements`), organization/
membership validity (already carried on `TenantContext`), and **whether a
launchable integration exists for the product at all** and where its
`launch_url` points (`platform/ecosystem`).

That last one is the problem. `platform/ecosystem` sits at P3
([ADR-018](ADR-018-openid-connect-for-product-sso.md)), and P3 is
"imported by nothing inside `platform/`" by design - it is where the OIDC
front door composes everything below it and stops. A new module reaching
into `platform/ecosystem` from beside it would be a same-layer import,
which `platform-internal-layers` forbids on principle, not by oversight.

`platform/launcher` is therefore placed in a new layer, **P4**, above
`platform/ecosystem`. It composes ecosystem *in addition to* everything
ecosystem itself composes - which is precisely what "the launcher decides
whether to send someone into a product ecosystem already knows how to
authenticate them into" means, structurally.

### 3. Reached directly, not through a `contracts/` port

`contracts/ports/catalog.py` and `contracts/ports/entitlements.py` exist to
let `platform/ecosystem` (and, in the future, a product) ask a question of
`commercial/` without importing it - the runtime-registry indirection is
there because the answer must cross the boundary a product must never see
past (ADR-005: a product never learns *how* it was purchased).

`platform/launcher` importing `platform/ecosystem` crosses no such
boundary. Both are platform code, deployed together, with nothing to hide
from each other - the same reason `platform/ecosystem` itself imports
`platform/entitlements` and `platform/organizations` directly rather than
through a port. Inventing one here would be the indirection pattern applied
somewhere it buys nothing, for a "second implementation of a question" cost
identical to the one `authorization.py`'s module docstring already warns
against.

### 4. What `platform/launcher` reads from `platform/ecosystem`

A new, narrow selector - `ecosystem.selectors.launch_target(product_code)` -
returns only `product_code`, `display_name`, `status` and `launch_url`.
**No client credential, no redirect URI set, no manifest scopes, no
integration id.** This is the "narrowest approved read projection" the
brief asks for, applied to the ecosystem boundary rather than the
commercial one: the launcher does not get the staff-only `IntegrationDTO`
(`ecosystem.selectors.integrations()`, gated by
`platform.integration.view` + staff), it gets a second, smaller, unauthorized-
for-any-member selector purpose-built for this one read.

### 5. `EntitlementDecision`-shaped, not a duplicate licensing system

`AppAccessState` (`ACTIVE | NO_SEAT | NO_ENTITLEMENT | INTEGRATION_SUSPENDED
| PRODUCT_UNAVAILABLE`) is a presentation-layer enum over the *existing*
`EntitlementDecision` and seat check - not a new commercial model, not a
cache of one, and not computed independently of what `require_seat` computes
for the same organization and product. Two implementations of "is this
allowed" is how they drift; there is one, in `platform/launcher/access.py`,
and both the launcher's read path and (indirectly, by construction) the
OIDC flow's write path answer from the same two calls:
`EntitlementGate.check()` and `EntitlementGate.has_seat()`.

## Alternatives considered

**Fold the launcher's logic into `platform/ecosystem`.** Rejected. Ecosystem
is the OIDC front door for an external, unauthenticated-until-proven-otherwise
caller; the launcher is an in-session, already-authenticated member browsing
their own organization. Conflating "what can a product ask" with "what can a
browser render" would grow one module past the point either job stays
readable, and would put app-directory listing traffic (frequent, read-heavy)
on the same module as the security-critical authorization boundary
(infrequent, must stay simple to audit).

**A `contracts/ports/launcher.py` indirection**, mirroring the
commercial-boundary pattern. Rejected for the reason in Decision §3: the
pattern's value is hiding `commercial/` from a caller that must never see it.
Applying it between two platform modules manufactures the appearance of a
boundary that is not there, at the cost of a Protocol, a registry and a
`register()` call that no caller ever needed to be able to swap.

**Compute seat/entitlement state independently in the launcher** rather than
reusing `EntitlementGate`. Rejected outright - this is the duplicate-billing-
system the brief explicitly forbids (Task 10: "Do not invent a duplicate
billing/licensing system"), and it is exactly how the launcher's answer and
the OIDC flow's answer would eventually disagree.

## Consequences

**Positive**

- One function answers "can this member launch this product" everywhere it
  is asked - Task 9's requirement is structurally true, not merely
  documented.
- The seat gap Phase 3 identified and deferred is closed at the one layer
  that can answer it (the platform), re-checked at the one moment that
  matters (redemption), exactly as entitlement already is.
- No new commercial or licensing model. `licensing_mode` and `has_seat` were
  already the platform's own vocabulary; Phase 4 wires them together rather
  than inventing a parallel one.

**Negative / accepted**

- A new platform layer (P4) is a real addition to a system every future
  platform module must now also respect. Accepted because the alternative -
  a same-layer import between `launcher` and `ecosystem`, or a port that
  crosses no real boundary - is worse in both cases, and P4 has exactly one
  member today.
- `require_seat()`'s cost is paid twice per sign-in (`authorize`, then again
  at `_revalidate_subject`), the same shape `require_entitlement` already
  has and the same trade-off Phase 3 accepted for it: affordable once per
  product sign-in, next to a signature that already costs an order of
  magnitude more (docs/phase-4/09-performance.md).

## Enforcement

| Rule | Enforced by |
|---|---|
| `platform.launcher` sits above `platform.ecosystem`, not beside it | import-linter `platform-internal-layers` |
| `platform.launcher` never imports `commercial`, `integration`, `products` or `interfaces` | import-linter `platform-internal-layers` (top-level contract already forbids platform -> those) |
| The launcher's decision and the OIDC flow's decision are the same two calls | `tests/architecture` - both call sites resolve to `EntitlementGate.check`/`has_seat`, asserted by reading the call, not by re-running the two paths and hoping they agree |
| The ecosystem selector the launcher reads carries no credential or redirect data | `test_launch_target_carries_no_credential_or_redirect_data` |
