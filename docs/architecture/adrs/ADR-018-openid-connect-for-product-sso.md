# ADR-018 — OpenID Connect for remote product SSO, served from `platform/ecosystem`

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Architecture + Security
**Closes:** gaps G-1 and G-3 in [16 §14](../16-ecosystem-product-boundary.md)
**Depends on:** [ADR-017](ADR-017-products-are-separate-applications.md)

---

## Context

[ADR-017](ADR-017-products-are-separate-applications.md) made every business
product a separate application in its own repository. That left the ecosystem
correct but unusable: a user signing in to CRM must be authenticating to
*Workeasy360*, and there was no token a separate application could present or
verify ([16 §8](../16-ecosystem-product-boundary.md)).

Identity, membership resolution and AAL all already existed and were correct.
They were **in-process**: `TenantContext` is resolved by middleware from a
session, inside this application. What was missing was an additional front
door onto an unchanged model.

Two decisions had to be made together, because the second constrains the
first: *which protocol*, and *where in the module map the code lives*.

## Decision

### 1. OpenID Connect, Authorization Code flow with PKCE

The platform is an OpenID Provider. Products are confidential clients using
the authorization code flow with PKCE (S256), asymmetric signing, and a
published JWK Set.

Explicitly **not** supported, in each case because OAuth 2.1 removes it or
because it would undermine a property the ecosystem depends on:

| Not supported | Why |
|---|---|
| Implicit and hybrid flows | Put tokens in URLs, in browser history and in referrer headers |
| Resource owner password grant | A product would receive the user's Workeasy360 password, which is the one thing the whole design forbids |
| `plain` PKCE | Sends the verifier in the same request as the challenge and protects nothing |
| Dynamic client registration (RFC 7591) | Would let anyone create a client. Registration is an authenticated, audited, staff-performed act |
| Refresh tokens | A long-lived credential whose purpose is to extend access past the last time the platform checked anything — the opposite of a bounded revocation window ([05 §6](../../phase-3/05-token-claims.md)) |

### 2. PyJWT for JOSE, and nothing else delegated

`PyJWT==2.14.0` with `cryptography==50.0.1` provides RFC 7515/7517/7519
signing, verification and JWK Set rendering. The authorization flow itself is
**not** delegated to a library.

That split is deliberate. The cryptography is the part where a bespoke
implementation is indefensible, and it is fully covered by a mature library.
The flow is the part that has to bind organization context, membership
validity and product entitlement — this platform's domain, which no OAuth
library models. Adapting a framework's client and token abstractions onto
`ProductIntegration`, `Membership` and `Entitlement` would have been more code
than the ~600 lines the state machine actually takes, and the result would
have been harder to audit.

### 3. The module lives at `platform/ecosystem`, P3

Not a new top-level package, and not beside `commercial/`.

Everything the front door owns is identity, tenancy, access and audit. It
composes `authz` (staff + dangerous permissions), `entitlements` (the access
decision), `audit` (every control-plane write), `organizations` (membership
resolution) and `identity` (the subject) — all P2 or below, so P3 is exactly
where it sits, alongside `files` and `notifications`. Nothing inside
`platform/` imports it, so P3 stays a leaf.

The consequence that matters: **F-4 still forbids it from importing
`commercial/`.** That is not an inconvenience to work around, it is the rule
that keeps a product from learning how a capability was purchased
([ADR-005](ADR-005-product-entitlement-model.md)). The one thing it genuinely
needs from the catalog — "is this product code real?" — is reached through
`contracts/ports/catalog.py`, the same runtime-registry pattern
`get_gate()` and `register_probe()` already use, and which
[16 §4.1](../16-ecosystem-product-boundary.md) says to preserve.

A dedicated import-linter contract states the rule separately from F-4, so a
future refactor that moves this module cannot lose it silently.

## Alternatives considered

**django-oauth-toolkit.** A complete OAuth2 provider for Django. Rejected: it
brings its own `Application` model that would compete with
`ProductIntegration`, its own migrations and its own tenancy-unaware
assumptions, and it integrates most naturally with Django REST Framework —
which Phase 3R removed as unused. Adapting it to bind organization context and
entitlement into code issuance would have meant fighting the framework at
exactly the point where the security lives.

**Authlib.** Mature and well maintained, with both JOSE primitives and an
`AuthorizationServer` framework. Rejected narrowly: only the JOSE half would
have been used, and it is the larger dependency of the two candidates. "Do not
keep dependencies for later" applies — PyJWT provides precisely the surface
that is used and nothing else. Authlib remains the obvious second choice if
PyJWT's maintenance posture ever changes.

**SAML.** Rejected. Heavier, XML-canonicalisation-bound, and worse supported
by the languages a product might be written in. OIDC is what a product team
will already know.

**A bespoke signed assertion.** Rejected outright. It would have meant this
team specifying a token format, and every product implementing a verifier
against a specification nobody else has reviewed. OIDC means a product uses
its language's existing library and a published discovery document.

**A new `ecosystem/` top-level package**, as [16 §7](../16-ecosystem-product-boundary.md)
sketched. Rejected: it would need a place in the top-level layering contract,
and anywhere it could sit that permits importing `commercial/` would weaken
the boundary this module exists to serve. `platform/` at P3 gives the right
dependencies and the right prohibitions with no change to the layer map.

## Consequences

**Positive**

- A product integrates by reading one URL. Every endpoint is discovered from
  `/.well-known/openid-configuration`, so the platform can move one without a
  coordinated release across nine repositories.
- Signatures are verified **offline** against the published key set, so a
  product serves its users during a platform outage and does not call
  Workeasy360 per request. No cascading failure.
- Audience isolation is structural: a token minted for CRM names CRM's client
  id, and Books rejects it with the library check every OIDC client already
  performs.
- Adding a product requires no platform change at all.

**Negative / accepted**

- Two runtime dependencies where there were none. Both are widely deployed,
  actively maintained and audited by `pip-audit --strict` on every build.
- Offline verification means **revocation is bounded, not instant**. A
  disabled user keeps a valid ID token until it expires. The mitigation is the
  300-second lifetime and the live entitlement endpoint, and the trade-off is
  stated in full in [09 §6](../../phase-3/09-product-integration-guide.md).
- The authorization endpoint needs a browser UI — a login page and an
  organization chooser — which does not exist in this repository. Until it
  does, an unauthenticated request returns `login_required`, which is the
  standard signal for a client to send the user to the provider.

## Enforcement

| Rule | Enforced by |
|---|---|
| `platform/ecosystem` never imports `commercial`, `integration`, `products` or `interfaces` | import-linter `ecosystem-never-sees-commercial` |
| The catalog is reached through the contracts port | Same contract, plus `test_a_product_code_the_catalog_does_not_know_is_refused` |
| Only the authorization code grant is served | `test_an_unsupported_grant_type_is_refused` |
| Only S256 PKCE | `test_plain_pkce_is_refused_rather_than_defaulted` |
| The discovery document matches the routes | `test_every_endpoint_the_discovery_document_names_resolves` |
| The advertised claim set matches what is issued | `test_the_token_claim_set_is_exactly_what_is_documented` |
| No product code is hardcoded in the module | `test_no_product_code_is_hardcoded_in_the_ecosystem_module` |
