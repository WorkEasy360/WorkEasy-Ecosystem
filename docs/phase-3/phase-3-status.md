# Phase 3 — Status

**PHASE 3 STATUS: PASS**

**Date:** 2026-09-22
**Baseline:** Phase 3R, 654 passing
**Result:** 933 passed · 0 failed · 0 skipped · fresh database

**Audited and re-confirmed 2026-09-22** by an independent adversarial review
against the code rather than the documents —
[17-audit-findings.md](17-audit-findings.md). One P1 and five P2s found,
reproduced and fixed; four P2s accepted with owners; three pieces of dead
code removed. The review's own baseline was the 912 recorded below.

---

## 1. The question this phase existed to answer

> **Can a completely independently developed product now integrate
> operationally with Workeasy360 without importing Workeasy360 implementation
> code or sharing its database?**

## **YES.**

The supported flow, in six steps:

1. **Register once.** The product submits a manifest — product code, launch
   URL, exact redirect URIs, requested scopes — and receives a random client
   id and a credential. Staff-approved, AAL 2, audited.
2. **Discover.** It hard-codes one issuer URL and reads
   `/.well-known/openid-configuration` for everything else.
3. **Authorize.** It sends the user to the authorization endpoint with PKCE,
   `state`, `nonce` and the organization from its launch URL. The platform
   verifies the client, the exact redirect URI, the session, the **active
   membership**, the organization status and the **entitlement** — then mints
   a single-use 60-second code.
4. **Exchange.** It redeems the code once, authenticating with a signed
   assertion (`private_key_jwt`) or a client secret.
5. **Verify offline.** It checks the ID token's signature against the cached
   JWK Set, plus `iss`, `aud`, `exp`, `nbf`, `typ` and `nonce` — **with no
   call to Workeasy360**. It now holds a stable `sub` and `organization_id`.
6. **Serve.** It runs its own session and its own store, keyed on
   `organization_id`, and re-asks "may this organization still use me?" on the
   advertised cadence.

Proven by `tests/integration/test_reference_product_client.py`, whose client
imports nothing from `workeasy360` — asserted by reading its AST.

---

## 2. Exit gate

| Requirement | Status | Evidence |
|---|---|---|
| A remote product can register securely | ✅ | `test_registry.py` (37) |
| `ProductIntegration` is separate from commercial `Product` | ✅ | Joined by code, no FK either way — `test_the_integration_is_separate_from_the_commercial_product` |
| Products remain outside this repository | ✅ | `products/` empty, unconditional |
| Authorization Code flow is standards-based | ✅ | OIDC + OAuth 2.1 practice, ADR-018 |
| PKCE works where required | ✅ | Required; S256 only; `plain` refused, not defaulted |
| Redirect URIs are exact-validated | ✅ | 7 unit + 7 end-to-end hostile variants |
| Tokens are asymmetrically signed | ✅ | RS256 / ES256; private key never in the database |
| JWKS is available | ✅ | `/.well-known/jwks.json`, 1 query, flat in key count |
| Signing-key rotation is tested | ✅ | Pre/post validity, publish-before-sign, retire, unknown kid, concurrent promotion |
| Product-specific audience isolation works | ✅ | Platform verifier, product verifier, userinfo |
| Organization identity is bound into authentication | ✅ | Signed `organization_id`, fixed at issue |
| Membership validity is checked | ✅ | Active membership required; the claim is a hint, the row is the authority |
| Entitlement is checked before access | ✅ | Step 11 of the authorization flow |
| Remote entitlement verification exists | ✅ | `POST /integrations/entitlements/verify` |
| A product cannot query another product's entitlement | ✅ | **There is no field in which to name one** |
| Service-to-platform authentication is secure | ✅ | `private_key_jwt` default; ADR-019 |
| Product credentials can rotate / revoke | ✅ | Additive rotation; immediate revocation |
| External trust boundaries are rate-limited | ✅ | 4 targeted buckets; **nothing else throttled** |
| Replay protections work under concurrency | ✅ | 8 threads, one code, one token set |
| No product-specific branching | ✅ | AST scan, platform-wide and module-specific |
| `products/` remains empty | ✅ | Unconditional fitness test |
| Product outages cannot affect readiness | ✅ | Deep probe only; readiness tested with an empty registry |
| Reference external-client test proves integration | ✅ | 15 tests, imports nothing |
| Integration / certification documentation exists | ✅ | [09](09-product-integration-guide.md), [10](10-product-certification-checklist.md) |
| No unresolved P0 | ✅ | None found |
| No Phase-4-blocking P1 | ✅ | See §4 |
| Full platform regression passes | ✅ | **933 / 0 / 0**, fresh database (912 at phase close, +21 from the audit) |

---

## 3. Report

**1. Tests executed** — the full suite from a database migrated from zero.

**2. Pass / fail / skip** — **933 / 0 / 0**, 292 s. Baseline 654; the phase
delivered 912 and the audit in [17](17-audit-findings.md) added 21 regression
tests, one per finding it fixed.

**3. Product-integration models** — 4: `ProductIntegration`,
`IntegrationCredential`, `SigningKey`, `AuthorizationCode`. All control plane.

**4. Migrations** — 1 (`ecosystem.0001_initial`), 4 tables. No RLS policies:
none of the four is tenant-owned (ADR-020). Zero product-domain tables in the
schema, verified directly.

**5. Remote endpoints** — 11:

```
GET  /.well-known/openid-configuration          public
GET  /.well-known/jwks.json                     public
GET  /api/v1/integrations/oauth/authorize       session
POST /api/v1/integrations/oauth/token           client credential
GET  /api/v1/integrations/oauth/userinfo        access token
POST /api/v1/integrations/entitlements/verify   client credential
     …/orgs/<slug>/integrations                 staff, AAL 2   (GET, POST)
     …/orgs/<slug>/integrations/<id>            staff, AAL 2   (GET, PUT)
     …/orgs/<slug>/integrations/<id>/status     staff, AAL 2   (POST)
     …/orgs/<slug>/integrations/<id>/credentials        (GET, POST)
     …/orgs/<slug>/integrations/<id>/credentials/<cid>  (DELETE)
```

**6. Protocol** — OpenID Connect, Authorization Code + PKCE (S256).
No implicit, no hybrid, no password grant, no refresh tokens, no dynamic
registration. ADR-018.

**7. Dependencies** — added `PyJWT==2.14.0` and `cryptography==50.0.1`;
removed none. `pip-audit --strict`: no known vulnerabilities. Alternatives
(Authlib, django-oauth-toolkit) evaluated in ADR-018.

**8. Token claims** — `iss sub aud iat nbf exp jti typ` + `organization_id`,
`product_code`, `aal`, `nonce`; `email`/`name` behind registered scopes.
`membership_id` considered and **deliberately not issued**. [05](05-token-claims.md).

**9. Signing / key architecture** — asymmetric, `kid`-selected, JWKS-published.
**The private half is never in the database** — a secret-manager reference
resolved through a contracts port. Four states with two deliberate overlaps.
ADR-020.

**10. Key rotation** — tested across pre/post-rotation validity,
publish-before-sign, retirement, unknown kid, retired key, missing secret and
concurrent promotion. A product survives rotation with **no redeployment**,
proven end to end.

**11. Organization context** — signed into the token, fixed at issue after a
verified membership. Named by opaque UUID, never slug. A product naming an
arbitrary organization gets `membership_inactive`, identical to the response
for an organization that does not exist.

**12. Entitlement API** — `allow`/`deny` plus a closed reason set. No
commercial detail crosses the boundary (asserted against 10 forbidden terms).
An unknown organization is indistinguishable from an unentitled one. **A
platform failure returns 503, never `deny`.** p50 7.5 ms / p95 8.3 ms.

**13. Product-service authentication** — `private_key_jwt` (default) and
`client_secret_basic`/`_post`. 256-bit secrets, SHA-256 at rest (reasoning in
[07 §3](07-service-authentication.md)), returned once, mandatory expiry,
additive rotation, immediate revocation, uniform failure. ADR-019.

**14. Cross-product isolation** — a CRM token is rejected by Books; a CRM code
cannot be exchanged by Books; a CRM client cannot query Books' entitlement;
Books cannot register CRM's redirect URI for itself.

**15. Redirect / PKCE / replay** — exact matching only, 14 hostile variants
refused; PKCE required and S256-only; codes single-use under 8-way
concurrency; client assertions single-use by `jti`.

**16. Rate limits** — 4 targeted buckets, per-client, 429 with `Retry-After`,
degrading to a per-process limiter when the cache is down. Unrelated platform
traffic is provably unaffected.

**17. Concurrency** — 8 tests against real PostgreSQL. One code → exactly one
token set; one integration per product; serialised transitions; one active
signing key; assertion replay refused. [12](12-concurrency-review.md).

**18. Security findings** — 15 threats modelled, all controlled or with
residual risk owned. **Step-up trigger REACHED and wired.** **SSRF-helper
trigger NOT REACHED**, mechanically asserted. [11](11-security-threat-model.md).

**19. Performance** — entitlement verify p50 7.5 ms / p95 8.3 ms / p99 8.6 ms.
Query budgets asserted on six paths; two N+1 guards. RS256 signing 27.7 ms p50
(once per authentication, not per request); ES256 1.3 ms.
[13](13-performance-review.md).

**20. Failure / resilience** — a product outage cannot affect the platform;
RabbitMQ is not on the authentication path at all; Redis degrades capacity,
not correctness; a missing signing key fails token issuance with 503 and
leaves the instance **ready**. [14](14-failure-mode-review.md).

**21. Observability** — 13 bounded metrics (no organization, user or token id
as a label — the cardinality guard rejects them at call time). Structured
events for authorization, issuance, invalid client, invalid redirect, code
replay, cross-client redemption, rate limiting, lifecycle and key rotation.
Correlation id on every error response. Tokens, codes, secrets and key
material never logged, asserted against the real values.

**22. Product conformance** — [10](10-product-certification-checklist.md): 7
sections, 40 evidence-backed items, 12 test vectors. Section C (tenant
isolation) closes G-8's responsibility boundary.

**23. Unresolved P1 / P2** — see §4.

**24. Architecture deviations / ADRs** — three ADRs (018, 019, 020); one new
import contract; one new `TENANT_SCOPE_EXEMPT` entry (D-008). No existing
check weakened; two were made **more precise** and one gained a canary test.

**25. Cleanup** — 3 dead functions and 1 unreachable permission removed; 1
misleading predicate removed; 1 unbounded table fixed; 2 over-broad checks
narrowed with proof. [15](15-cleanup-review.md).

**26. Dependencies** — +2, −0.

**27. Files / LOC** — 19 new source and test files plus a new package; 49
files modified. Ecosystem module 4,007 source + 3,069 test; contracts +787;
interfaces +627; cross-cutting tests +2,287.

**28. Fresh database** — `docker compose down -v`, migrate from zero, full
suite: **933 / 0 / 0**. Zero product-domain tables.

**29. Decisions proposed to freeze for Phase 4** — §5.

---

## 4. Carried forward

**P0: none.**

**P1: none that block Phase 4.** The app launcher — Phase 4's likely subject —
needs `launch_url`, `display_name` and status, all of which the registry now
holds.

| # | Item | Severity | Note |
|---|---|---|---|
| P2-1 | Seat limits not enforced at sign-in | P2 | **Trigger reached.** Needs licensing mode, which is commercial data the platform may not read — *and* the remote contract gives a product no field in which to ask, so it cannot be delegated. Not a security gap. Belongs with the launcher. `no_seat` reserved so adding it is not breaking. [06 §7](06-entitlement-api.md), [17](17-audit-findings.md) |
| P2-2 | No browser UI for login / org chooser | P2 | Endpoints return `login_required` and `organization_required`. Frontend work, excluded by the brief |
| P2-3 | No RP-initiated logout | P2 | `post_logout_redirect_uris` validated and stored |
| P2-4 | Rate-limit buckets are keyed on `REMOTE_ADDR` | P2 | Behind a proxy that does not rewrite it, the `authorize` bucket is a per-product ceiling an anonymous caller can fill. Never crosses products. Reading `X-Forwarded-For` blindly would be worse. Deployment requirement stated in [14 §11](14-failure-mode-review.md); carried as D-009 / R-8 |
| P2-5 | `/authorize` with an unknown `client_id` is unthrottled | P2 | The limiter runs after client resolution because there is nothing to key on before it. One indexed lookup per request; discloses nothing. Same root cause as P2-4 |
| G-4 | App directory / launcher metadata API | P2 | Unchanged |
| G-5 | Outbound webhook delivery | P2 | Unchanged. **Will reach the SSRF trigger** |
| G-6 | HTTP surface for files / notifications | P2 | Unchanged |
| G-7 | Remote permission declaration | P2 | Unchanged. [10 §6](10-product-certification-checklist.md) documents the boundary |

**Closed this phase:** G-1 (SSO), G-2 (entitlement HTTP), G-3 (registry and
manifest), G-8 (certification checklist).

---

## 5. Decisions proposed to freeze for Phase 4

1. **The platform is an OpenID Provider.** Products are confidential OIDC
   clients. Changing this requires a new ADR superseding ADR-018.
2. **Verification is offline.** Products verify signatures against the
   published key set and do **not** call the platform per request. This is the
   anti-cascading-failure property; instant revocation is not on offer.
3. **An authorization code is a record of a decision, not a licence to skip
   making it again.** Membership, organization status and entitlement are
   re-checked before a code is spent, so the documented revocation window is
   the token lifetime and does not silently include the code lifetime. A code
   valid at redirect may legitimately return `invalid_grant` seconds later;
   products restart the flow ([17](17-audit-findings.md)).
4. **The client id is the audience.** Not the product code.
5. **Redirect URIs are compared by exact string equality.** No prefix, no
   normalisation, no wildcards, ever.
6. **`product_code` is never a request parameter** on the entitlement API. The
   authenticated client determines the subject.
7. **A platform failure is never reported as a denial.** 503, always.
8. **The private signing key is never in the database.** A reference, resolved
   through the port.
9. **A key is published before it signs and stays published after it stops.**
10. **No refresh tokens.** A product issues its own session.
11. **Key rotation has no HTTP surface**, and is recorded in the
    security-event stream rather than the tenant audit log.
12. **Control-plane integration writes are `dangerous`** — AAL 2, staff, no
    impersonation.
13. **The platform makes no outbound request to a product-supplied URL.** The
    first feature that changes this must bring the SSRF-safe fetch helper with
    it.
14. **Rate limiting on this boundary is targeted**, never global.
15. **`products/` stays empty.** Unchanged from ADR-017 and not negotiable
    without superseding it.

---

## 6. Scope discipline

Not built, per the brief: CRM, Books, Inventory, Projects, Marketing, Support,
HR, Payroll, Commerce · the app launcher · the Super Admin, reseller,
white-label and affiliate frontends · AI functionality · the workflow builder ·
payment-provider integration.

`products/` contains ten `__init__.py` files and nothing else, asserted
unconditionally.

**Phase 3 is complete. Phase 4 has not been started.**
