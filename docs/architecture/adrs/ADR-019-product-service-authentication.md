# ADR-019 — How a product authenticates itself to the platform

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Architecture + Security
**Closes:** part of gap G-2 in [16 §14](../16-ecosystem-product-boundary.md)
**Depends on:** [ADR-018](ADR-018-openid-connect-for-product-sso.md)

---

## Context

Two endpoints are reached by a product's *server*, with no user and no
browser: the token endpoint, which exchanges an authorization code, and the
entitlement verification endpoint, which answers "may this organization use
me?".

Both need to know which product is calling, and the answer has to be
trustworthy for a reason that goes beyond the usual: **the caller's identity
determines the subject of the question.** The entitlement endpoint has no
`product_code` parameter — it reads the product from the authenticated
credential. If client authentication can be spoofed, cross-product
entitlement probing follows immediately.

## Decision

Two mechanisms, one credential model, one lifecycle, one failure response.

### `private_key_jwt` (RFC 7523) — the default

The product signs a short-lived assertion with a private key it never sends.
The platform stores only the public half. Nothing secret crosses the network,
and **a compromise of the platform's database yields no credential an
attacker can use as the product** — which is not true of any shared-secret
scheme.

The assertion must name the token endpoint as its audience, carry matching
`iss` and `sub` (both the client id, per RFC 7523 §3), and carry a `jti` that
is spent on first use. It is verified with the algorithm recorded on the
credential, never the one in its own header.

### `client_secret_basic` / `client_secret_post` — the alternative

A 256-bit secret from `secrets.token_urlsafe`, returned exactly once at
creation and stored as a SHA-256 digest. Offered because "our product cannot
manage a key pair" is a real constraint, and refusing it would push those
teams toward something worse — a static token in a query string.

### SHA-256, not Argon2, for the secret digest

The rest of this platform hashes passwords with Argon2id, and that is correct
*there*: a human-chosen password has perhaps 40 bits of entropy, so the work
factor is what stands between a stolen hash and the plaintext.

These secrets have 256 bits of entropy from a CSPRNG. There is nothing to
guess. A work factor buys no security whatsoever, while costing ~100 ms on
every call to the two endpoints that must stay available — and handing any
authenticated client a CPU-exhaustion lever against them. Fast hashing of a
full-entropy secret is the correct choice and is what OWASP recommends for
generated API credentials.

### mTLS is not implemented

Evaluated and deferred. It is the strongest option and it is also an
infrastructure commitment — a certificate authority, issuance, rotation,
revocation, and termination that must not strip the client certificate. None
of that exists yet, and `private_key_jwt` provides the property that matters
most (no shared secret anywhere) at the application layer. Revisit when the
first product requires FAPI-grade assurance.

### One failure response, always

Unknown client id, wrong secret, bad signature, revoked credential, expired
credential, wrong assertion audience, replayed assertion — every one returns
the identical `invalid_client`, 401, same description. Distinguishing them
makes the endpoint a client-enumeration oracle and tells an attacker when
they have found a real client id, which is most of the work.

The one deliberate exception is **after** successful credential verification:
a caller whose integration is `suspended` is told so (`integration_suspended`,
403). They have already proved they hold a valid credential, so it discloses
nothing they could not determine anyway, and it is the one thing that lets
their operator fix it.

## Credential lifecycle

| Concern | Decision |
|---|---|
| **Creation** | `platform.integration.credential`, staff-only, AAL 2, audited. A secret is returned once and is not recoverable. |
| **Storage** | Public keys in the clear (they are public). Secrets as a SHA-256 digest, with the first 8 characters kept as a lookup handle so verification is one indexed row rather than a hash per stored credential. |
| **Rotation** | Additive. Creating a credential does **not** revoke the existing one, so a product rotates by adding, deploying, verifying, and only then revoking. A rotation requiring an atomic fleet-wide swap is a rotation nobody performs. |
| **Revocation** | Immediate, with no propagation delay: client authentication reads the row on every request and there is no cached credential state. This is the deliberate difference from token revocation, which is bounded by the token lifetime. |
| **Expiry** | Every credential has one, defaulting to 365 days. A credential with no end date outlives the person who created it and the reason it existed. |
| **Audit** | Creation and revocation write an `AuditEvent`. Use is recorded at **day** granularity, because the question is "is anything still using this?" before revoking — and a per-second timestamp would turn every token exchange into a write. |
| **Secrets in logs** | Never. Asserted against the actual generated secret by `test_no_credential_secret_ever_reaches_the_audit_log`. |

## Alternatives considered

**A single permanent API key per product.** Rejected, and it is what the
phase brief specifically warned against. It has no expiry, no rotation story
and no way to distinguish two deployments of the same product.

**HMAC request signing (AWS SigV4-style).** Rejected. It is a shared secret
with extra steps, it is easy to implement subtly wrong on the client side,
and it is not what a product team's existing OAuth library already does.

**Only `private_key_jwt`.** Tempting, and it is the default for good reason.
Rejected because `token_endpoint_auth_method` is a standard registration
field, supporting both is ordinary OAuth practice, and a team that cannot
manage a key pair will otherwise route around the platform rather than
adopt it.

## Consequences

**Positive**

- The recommended path leaves no usable credential in this platform's
  database.
- Cross-product entitlement probing is not merely forbidden but
  inexpressible: the question has no field in which to name another product.
- Revocation is immediate, which makes an incident response actually possible.

**Negative / accepted**

- Two mechanisms mean two code paths and a doubled security test matrix. Both
  are tested to the same standard, including the uniform-failure property.
- `jti` replay protection lives in the cache. A cache outage loses that one
  layer for the assertion's maximum age; the assertion is still
  signature-verified, audience-bound and expiry-bound, so what is lost is one
  layer of a defence in depth rather than the defence
  ([14 §4](../../phase-3/14-failure-mode-review.md)).

## Enforcement

| Rule | Enforced by |
|---|---|
| Every authentication failure is indistinguishable | `test_every_assertion_failure_is_indistinguishable`, `test_the_token_endpoint_is_not_a_client_oracle` |
| The algorithm comes from the credential, not the token | `test_the_algorithm_comes_from_the_credential_not_the_assertion` |
| Assertions are single use | `test_an_assertion_cannot_be_replayed`, and under concurrency |
| Revocation takes effect immediately | `test_a_revoked_credential_stops_working_immediately`, `test_a_credential_revoked_mid_flight_stops_working` |
| A secret is never readable after creation | `test_a_client_secret_is_returned_once_and_never_stored` |
| Session cookies authenticate nothing on these endpoints | `test_credentialed_endpoints_ignore_session_authentication` |
