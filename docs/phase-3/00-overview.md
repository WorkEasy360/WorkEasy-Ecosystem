# Phase 3 — Remote Product Integration & SSO Foundation

**Date:** 2026-09-22
**Baseline:** Phase 3R (`34cf7a3` + scope audit), 654 tests passing
**Result:** 912 tests passing, 0 failed, 0 skipped
**Decisions of record:** [ADR-018](../architecture/adrs/ADR-018-openid-connect-for-product-sso.md) · [ADR-019](../architecture/adrs/ADR-019-product-service-authentication.md) · [ADR-020](../architecture/adrs/ADR-020-signing-keys-and-the-control-plane-code.md)

---

## 1. What this phase changed

Phase 3R left the boundary **correct but incomplete**: products were properly
outside the repository, and consequently no product could actually be served.
[ADR-017](../architecture/adrs/ADR-017-products-are-separate-applications.md)
said so in as many words — "the boundary is correct but incomplete, and that
is the next phase's work".

This phase built the remote form of the contracts that already existed
in-process. It turned

> "separate products are structurally possible"

into

> "a completely independently developed product can securely integrate."

The proof is `backend/tests/integration/test_reference_product_client.py`: a
simulated external product that **imports nothing from `workeasy360`**,
discovers every endpoint from `/.well-known/openid-configuration`, completes
the authorization code flow, verifies the identity token offline against the
published key set, confirms entitlement over HTTP, and keeps its own data in
its own store keyed by `organization_id`.

## 2. What was built

| Capability | Where | Gap closed |
|---|---|---|
| Product integration registry and lifecycle | `platform/ecosystem/models.py`, `services.py` | G-3 |
| Versioned product manifest, strictly validated | `contracts/values/manifest.py` | G-3 |
| OpenID Connect provider: discovery, JWKS, authorize, token, userinfo | `platform/ecosystem/`, `interfaces/api/v1/ecosystem_views.py` | G-1 |
| Asymmetric signing with `kid` rotation | `platform/ecosystem/keys.py` | G-1 |
| Remote entitlement verification | `platform/ecosystem/verification.py` | G-2 |
| Service-to-platform client authentication | `platform/ecosystem/clientauth.py` | G-2 |
| Targeted rate limiting on the external boundary | `platform/ecosystem/ratelimit.py` | carried debt |
| Step-up on dangerous control-plane actions | `platform/ecosystem/permissions.py` | carried trigger |

## 3. The one-paragraph architecture

The platform is an **OpenID Provider**. Each product registers once — a
manifest naming its product code, launch URL, exact redirect URIs and
requested scopes — and receives a random client id and a credential. A user
is sent from the launcher to the product, which starts an authorization code
flow with PKCE. The platform verifies, in order: the client, the exact
redirect URI, PKCE, an authenticated session, a named organization, an
**active membership** in it, the organization's status, and the
**entitlement**. Only then does it mint a single-use 60-second code. The
product exchanges it, authenticating itself with a signed assertion, and
receives an ID token it verifies **offline** against the published JWK Set.
From there the product runs its own session and re-asks "may this
organization still use me?" on the documented cadence.

Nothing in that path imports a platform module, shares a database, or knows
what anything cost.

## 4. The documents

| Doc | Covers |
|---|---|
| [01 Product Registry](01-product-registry.md) | `ProductIntegration`, why it is not `catalog.Product`, lifecycle |
| [02 Product Manifest](02-product-manifest.md) | The submitted contract and every validation rule, with its reason |
| [03 SSO Architecture](03-sso-architecture.md) | The flow, the trust boundaries, where the browser UI lives |
| [04 OIDC Contract](04-oidc-contract.md) | Endpoints, parameters, responses, error codes — the machine-readable contract |
| [05 Token Claims](05-token-claims.md) | Every claim, why it is there, and what was deliberately left out |
| [06 Entitlement API](06-entitlement-api.md) | The remote access decision and what it refuses to disclose |
| [07 Service Authentication](07-service-authentication.md) | How a product proves it is itself; credential lifecycle |
| [08 Key Management](08-key-management.md) | Rotation procedure, and the compromise runbook |
| [09 Product Integration Guide](09-product-integration-guide.md) | **Start here if you are building a product** |
| [10 Certification Checklist](10-product-certification-checklist.md) | What a product must prove before it joins the ecosystem |
| [11 Security Threat Model](11-security-threat-model.md) | 15 threats, their controls and the residual risk |
| [12 Concurrency Review](12-concurrency-review.md) | Every race, and what makes it deterministic |
| [13 Performance Review](13-performance-review.md) | Measured latency and query budgets |
| [14 Failure Mode Review](14-failure-mode-review.md) | What happens when each dependency is down |
| [15 Cleanup Review](15-cleanup-review.md) | What was removed and why |
| [16 Test Report](16-test-report.md) | What is covered, and what is not |
| [17 Audit Findings](17-audit-findings.md) | The adversarial review run before the phase was frozen: what it found, what it fixed, what it accepted |
| [Status](phase-3-status.md) | The exit gate, answered item by item |

## 5. What was deliberately not built

Named here so the next phase starts from a known set rather than
rediscovering it.

- **The browser UI** — login page, organization chooser, consent screen. The
  authorization endpoint returns `login_required` and
  `organization_required`, which are the standard signals for a client to
  send the user to the provider. See [03 §9](03-sso-architecture.md).
- **The app launcher.** Out of scope by the phase brief; the registry now
  holds everything it will need (`launch_url`, `display_name`, status).
- **Refresh tokens.** A decision, not an omission — [05 §6](05-token-claims.md).
- **Outbound webhook delivery** (G-5), **HTTP surfaces for files and
  notifications** (G-6), **remote permission declaration** (G-7). Unchanged
  from Phase 3R.
- **Seat enforcement at sign-in.** Analysed and deliberately deferred; the
  reasoning and the resulting contract gap are in
  [06 §7](06-entitlement-api.md).
- **Any product.** `products/` remains empty, unconditionally.
