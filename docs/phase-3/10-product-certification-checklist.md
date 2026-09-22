# 10 — Product Integration Certification Checklist

**Written for: the product team submitting, and the Workeasy360 reviewer
signing off.**

A product that leaks tenant data damages the ecosystem's trust, not only its
own. The platform's five-layer isolation model protects **platform** data;
everything in a product's store is the product's responsibility, and this
checklist is how that responsibility stops being an assumption
([16 §9](../architecture/16-ecosystem-product-boundary.md), gap G-8).

Each item names **evidence** — a test in your repository, not a statement.
Submit the test names and their output.

---

## A. Token verification

| # | Requirement | Evidence |
|---|---|---|
| A1 | The issuer is configured, not derived from a token or a header | Config review |
| A2 | The `iss` claim is compared to it by exact string equality | A token with `iss` altered is rejected |
| A3 | The `aud` claim is compared to your own client id | **A token minted for another product is rejected.** See vector V1 |
| A4 | The signing key is selected by `kid` from the cached JWK Set | A token with an unknown `kid` is rejected |
| A5 | The algorithm comes from the **key set entry**, never the token header | An `alg: none` token is rejected; an HMAC-signed token using our public key as the secret is rejected. Vectors V2, V3 |
| A6 | `exp` and `nbf` are verified with ≤ 60 s leeway | An expired token is rejected. Vector V4 |
| A7 | `typ` is checked to be `id_token` | An access token presented as an identity assertion is rejected. Vector V5 |
| A8 | `nonce` is compared to the one you generated | A token carrying a different nonce is rejected |
| A9 | An unknown `kid` refreshes the JWK Set **once**, then fails | The refresh is not unconditional (request amplification) |
| A10 | Signature verification is **offline** — no platform call per request | Trace or code review |

## B. The authorization flow

| # | Requirement | Evidence |
|---|---|---|
| B1 | `state` is generated per request, stored server-side or in the session, and compared on callback | A callback with a mismatched `state` is refused |
| B2 | The `state` comparison is constant-time | Code review |
| B3 | `code_verifier` is 43–128 characters from a CSPRNG | Code review |
| B4 | `code_challenge_method` is `S256` | Code review |
| B5 | The `code_verifier` is stored per authorization and never reused | Code review |
| B6 | Every callback URL you use is registered, exactly | Registration matches deployment |
| B7 | The authorization code is exchanged once, server-side, over TLS | It never reaches your frontend |
| B8 | You handle every documented error code with a distinct user outcome | See [04 §7](04-oidc-contract.md) |

## C. Tenant isolation — the section that matters most

**Read this paragraph before the table.** Workeasy360 signs `organization_id`,
and a signature is a strong guarantee about exactly one thing: *which* tenant
the bearer is acting for. It says nothing whatsoever about whether your
database honours that. We cannot enforce isolation inside a store we do not
have, cannot reach and do not know the schema of — so every row you return to
the wrong tenant is returned under a claim we signed, which makes the breach
look authoritative.

Divided plainly: **the platform owns the ecosystem identity; the product owns
the enforcement.** Nothing in section A substitutes for anything in section C.
A product that verifies every token perfectly and then serves a list endpoint
without a tenant filter has a cross-tenant data breach and a flawless OIDC
implementation.

| # | Requirement | Evidence |
|---|---|---|
| C1 | **Every** row in your store carries `organization_id` | Schema review: no tenant-owned table without it |
| C2 | Every read is scoped by `organization_id` from the **verified token**, never from a request parameter | Code review + C3 |
| C3 | A request authenticated for organization A **cannot** read a row belonging to B — including by direct id | An isolation test per tenant-owned table, asserting an explicit A-reads-B attempt returns not-found |
| C4 | An unknown `organization_id` is treated as **not found**, never as "allow" | Test |
| C5 | Isolation holds for list endpoints, direct-id lookups, search, exports and background jobs | Test per path. Background jobs are where this is most often missed |
| C6 | `organization_id` is taken from the token, never from a header, body or query parameter | Code review |
| C7 | The same user in two organizations sees two disjoint data sets | Test |

C3 is the one to spend time on. "We filter by organization in the ORM" is not
evidence; a test that tries the attack and fails is.

## D. Entitlement handling

| # | Requirement | Evidence |
|---|---|---|
| D1 | Entitlement is checked when a user arrives | Test |
| D2 | It is re-checked at least as often as `revalidate_after` | Test or code review |
| D3 | `write=true` is used before a privileged or irreversible operation | Test |
| D4 | **`503 temporarily_unavailable` is retried, not treated as a denial** | Test. This is the failure mode that turns our incident into your outage |
| D5 | `deny` reasons drive distinct user outcomes | Test |
| D6 | You never branch on plan, price, subscription state or seat count | Code review. You will not be given them |
| D7 | Entitlement answers are not cached beyond `revalidate_after` | Code review |

## E. Credentials and secrets

| # | Requirement | Evidence |
|---|---|---|
| E1 | Your private key or client secret comes from a secret manager, not source control | Config review + secret scan of your repository |
| E2 | It never appears in logs, traces or error responses | Log review |
| E3 | You can rotate it without downtime, using the additive procedure | Rehearsed, with the date recorded |
| E4 | Assertion `jti` values are unique per assertion | Code review |
| E5 | Tokens and authorization codes never appear in your logs | Log review |

## F. Independence

| # | Requirement | Evidence |
|---|---|---|
| F1 | No import of any `workeasy360` module | Dependency manifest + grep |
| F2 | No connection to a Workeasy360 database, with any credential | Config review |
| F3 | Your schema and migrations are yours alone | Repository review |
| F4 | **Your product serves existing sessions while the platform is unreachable** | Test with the platform blocked at the network level |
| F5 | New sign-in fails safely and legibly during a platform outage | Test |
| F6 | Your outage cannot affect the platform or another product | By construction; confirm no shared infrastructure |
| F7 | Every platform call has explicit connect and read timeouts | Code review |

F4 is the reason offline verification exists. A product that goes down when
the platform does has given away the property it was designed to have.

## G. Operations

| # | Requirement | Evidence |
|---|---|---|
| G1 | `health_url` returns a machine-readable document | Reachable |
| G2 | You survive a platform signing-key rotation with no deployment | Rehearsed against staging during a rotation |
| G3 | You have a contact for integration incidents | `contact_email` in your manifest |

---

## Test vectors

Ask Workeasy360 staging for a fixture set. Each vector is a token your
verifier **must reject**; the platform's own suite runs the same set against
its own verifier in `tests/security/test_ecosystem_security.py`.

| # | Vector | Must fail on |
|---|---|---|
| V1 | Valid token for a **different** product's client id | `aud` (A3) |
| V2 | Header rewritten to `alg: none` | Signature / algorithm (A5) |
| V3 | HMAC-signed with our published public key as the secret | Algorithm from key set (A5) |
| V4 | Expired by 1 hour | `exp` (A6) |
| V5 | An **access token** presented as an ID token | `typ` (A7) |
| V6 | `organization_id` altered | Signature |
| V7 | `sub` altered | Signature |
| V8 | `product_code` altered to another product | Signature |
| V9 | `iss` altered | `iss` (A2) |
| V10 | Signed by a **retired** key | Unknown `kid` (A4) |
| V11 | Valid token, wrong `nonce` | `nonce` (A8) |
| V12 | `exp` extended by an hour, original signature | Signature |

A verifier that accepts any of these has a finding, not a warning.

## Sign-off

| | |
|---|---|
| Product | |
| Version / commit | |
| Submitted by | |
| Reviewed by | |
| Date | |
| Sections passed | A ☐ B ☐ C ☐ D ☐ E ☐ F ☐ G ☐ |
| Vectors rejected | V1–V12 ☐ |
| Exceptions granted | (each needs a named owner and an expiry) |

Re-certification is required on a change to authentication, tenant scoping or
entitlement handling, and at least annually.
