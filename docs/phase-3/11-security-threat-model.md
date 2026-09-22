# 11 — Security Threat Model: Remote Product Integration

Scope: the boundary between Workeasy360 and an application it does not
control. Complements [12-security.md](../architecture/12-security.md), which
covers the platform itself.

**What changed about the trust model.** Before this phase every caller was
inside the deployment. Now there are three new externally reachable
endpoints, a class of caller that is an *authenticated non-person*, and a
signed artefact that is verified by code this team does not own and cannot
patch. Most of what follows is a consequence of that last point.

---

## Threats

### T-1 Stolen authorization code

**Impact: high.** A code redeems to an identity assertion.

Controls: 60-second lifetime · single-use, enforced by a conditional UPDATE ·
bound to the client, the exact redirect URI and the PKCE challenge · stored
only as SHA-256 · never logged.

An attacker holding a stolen code needs the client's credential *and* the
`code_verifier` from the browser that started the flow, within a minute,
before the legitimate exchange. PKCE is what makes a code in a referrer
header or browser history worthless.

**Residual:** an attacker with both the client credential and the verifier —
i.e. one who has already compromised the product. Not addressable at this
boundary.

### T-2 Stolen client credential

**Impact: high.** Read any organization's entitlement state; redeem codes
issued to that product.

Controls: `private_key_jwt` by default, so **the platform's database holds
nothing usable as the product** · secrets 256-bit, stored hashed, returned
once · mandatory expiry · immediate revocation with no cached state ·
assertions single-use by `jti`, audience-bound to the token endpoint ·
`client_auth_failure` limited to 10/min.

**Residual:** a credential stolen from the *product's* infrastructure works
until revoked. Bounded by E1–E3 in the certification checklist and by
expiry.

### T-3 Redirect hijacking / open redirect

**Impact: critical** — a full account-takeover primitive against every user of
a product.

Controls: **exact string matching**, nothing else · wildcards, query strings,
fragments and credential-carrying URLs refused at registration · https only
(loopback exempt outside production) · errors are rendered until the client
*and* the redirect URI are verified · changing a registered URI is
`dangerous` (AAL 2, staff, no impersonation) and audits the before and after
sets.

Seven normalisation and prefix variants are pinned by
`test_exact_matching_is_the_only_comparison`, and seven more end to end by
`test_an_unregistered_redirect_uri_is_never_redirected_to`.

**Residual:** a compromised staff account with AAL 2 could register a hostile
URI. Detected by the audit entry, which records both sets.

### T-4 Audience confusion — a CRM token used against Books

**Impact: critical.** Both tokens are signed by the same key, so the signature
check passes for both.

Controls: `aud` is the **client id**, not the product code · `product_code` is
a second, independently minted binding · the certification checklist makes
`aud` verification mandatory and provides V1 as a test vector.

**Residual: this control lives in the product.** The platform cannot make a
product check `aud`. It is why A3 is a checklist item with a vector rather
than a line of prose, and why `test_product_y_cannot_use_product_x_token`
exists to show the correct behaviour.

### T-5 Tenant switching

**Impact: critical.** Cross-tenant data access.

Controls: `organization_id` is signed into the token, so altering it breaks
the signature · it is fixed at *issue* time, after a verified membership,
never re-decided at exchange · the product supplies an organization but the
membership row is the authority · the opaque UUID is the identity, never the
slug.

**Residual:** product-side isolation is the product's (C1–C7). The platform
guarantees the claim is true, not that the product honours it.

### T-6 Token tampering

**Impact: critical.**

Controls: asymmetric signature · algorithm from the key record, never from
the token header · `kid` must resolve to a non-retired key · `iss`, `aud`,
`exp`, `nbf`, `iat`, `jti`, `typ` all required at verification.

Ten single-field forgeries are pinned by `test_a_tampered_token_never_verifies`.

### T-7 Authorization code replay

**Impact: high.** Two sessions from one authorization.

Controls: a conditional `UPDATE … WHERE redeemed_at IS NULL`, atomic in
PostgreSQL. A read-then-write would let several concurrent redemptions all
observe an unredeemed code under READ COMMITTED and all issue tokens — and
would pass a sequential test every time.

Proven with eight simultaneous redemptions:
`test_one_authorization_code_yields_exactly_one_token_set`.

### T-8 Client assertion replay

**Impact: medium.** Authenticate as the product for the assertion's window.

Controls: `jti` spent on first use via an atomic `cache.add` · maximum age
300 s · audience-bound to the token endpoint.

**Residual:** a cache outage loses this layer for the window. The assertion is
still signature-verified, audience-bound and expiry-bound, so what is lost is
one layer of a defence in depth rather than the defence.

### T-9 Organization enumeration

**Impact: medium.** Map the customer base — reachable by any authenticated
user of any product.

Controls: "no such organization", "not a member" and "membership revoked"
return **one identical response** · the entitlement endpoint returns the same
decision, reason and field set for an unknown organization as for an
unentitled one.

`test_the_authorization_endpoint_is_not_an_organization_oracle` asserts the
set of distinct responses across those cases has size 1.

### T-10 Client / product enumeration

**Impact: medium.** Finding a real client id is most of the work in credential
stuffing.

Controls: one `invalid_client` for every cause, same status, same description
· the secret path hashes even when no row was found, so timing does not
distinguish · client ids are random, not derived from the product code.

`test_the_token_endpoint_is_not_a_client_oracle` compares the full response
bodies.

### T-11 SSRF via a manifest URL

**Impact: high if present.** `launch_url`, `health_url` and redirect URIs are
all product-supplied.

**Control: the platform makes no outbound request to any of them.** There is
no egress allowlist to get wrong because there is no egress.

`test_the_ecosystem_module_makes_no_outbound_http_request` asserts this by
import, so adding an HTTP client to the module fails the build rather than
quietly reopening the question.

**Trigger status: NOT REACHED.** The SSRF-safe-fetch helper described in
[12-security.md §9](../architecture/12-security.md) is not needed in this
phase. It becomes required the moment anything fetches a registered URL —
health polling, webhook delivery, or icon fetching — and that is the first
thing to build alongside it.

### T-12 Denial of service against the auth endpoints

**Impact: medium.** Every product's sign-in depends on them.

Controls: four **targeted** buckets (authorize, token, verify,
client-auth-failure) and nothing else · per-client scoping, so one product
cannot consume another's budget · 429 with `Retry-After` · fast credential
hashing, so a caller cannot force ~100 ms of CPU per request.

`test_unrelated_platform_traffic_is_not_throttled` exhausts every ecosystem
bucket and asserts an ordinary tenant endpoint is unaffected — a global
throttle would make one product's retry loop an unrelated customer's outage.

**Residual:** volumetric attacks are the WAF/CDN's job
([09-failure-resilience.md §4.6](../architecture/09-failure-resilience.md)).

### T-13 Cross-product entitlement probing

**Impact: medium.** Commercial intelligence about other customers.

Control: **the question has no field in which to name another product.** The
subject comes from the authenticated credential. This is stronger than
validating a supplied code against the caller.

### T-14 Signing-key compromise

**Impact: critical.** Mint a token for any user, in any organization, for any
product.

Controls: the private half is **not in the database** — a dump yields no
signing capability, structurally · secret-manager resolution through a port ·
rotation without deployment · a documented `--force` procedure that
invalidates every unexpired token within one JWKS cache period · `jti` logged
on every issuance, so the exposure window is auditable.

Runbook: [08 §6](08-key-management.md).

### T-15 Privilege escalation through the control plane

**Impact: critical.** Registering an integration, or changing a redirect URI,
compromises every user of that product.

Controls: every write permission is `dangerous` → **AAL 2 required and
blocked outright under impersonation** · plus `require_platform_staff`, which
a customer administrator cannot grant themselves · every mutation audited ·
no HTTP surface at all for signing-key rotation.

`test_every_integration_write_permission_requires_step_up` asserts the
`dangerous` flag on every write permission, so a new one added without it
fails the build.

---

## Step-up trigger

**TRIGGER REACHED: YES. Wired.**

The `dangerous` mechanism already existed and was already enforced in
`authz.services.check` — AAL 2, no impersonation, no read-only organization.
The actions added by this phase are the highest-value control-plane writes in
the platform, so leaving them at AAL 1 would have been leaving a working
control switched off.

`platform.integration.view` is deliberately **not** dangerous: client ids and
redirect URIs are public by construction, and gating the read would push
operators toward keeping a copy somewhere worse.

## SSRF-helper trigger

**TRIGGER NOT REACHED**, mechanically asserted. See T-11.

## Residual risk summary

| # | Residual | Owner | Mitigation |
|---|---|---|---|
| R-1 | A product that fails to verify `aud` accepts another product's token | Product | Checklist A3 + vector V1 |
| R-2 | A product that fails to scope by `organization_id` leaks tenant data | Product | Checklist C1–C7 |
| R-3 | A product that treats `503` as `deny` locks out paying customers | Product | Checklist D4 |
| R-4 | Revocation is bounded (≤ 330 s identity, ≤ 900 s access), not instant | Platform, accepted | [05 §7](05-token-claims.md); `--force` key retirement is the emergency lever. An unredeemed code does not extend the window — the token endpoint re-validates before spending one |
| R-8 | Rate-limit buckets are keyed on `REMOTE_ADDR`, which is the proxy behind a load balancer | Platform, P2 | [14 §11](14-failure-mode-review.md). Per-product, never cross-product; the deployment requirement is stated rather than assumed |
| R-5 | Credential-assertion replay protection is lost during a cache outage | Platform, accepted | Signature, audience and expiry still hold |
| R-6 | Seat limits are not enforced at sign-in | Platform, P2 | [06 §7](06-entitlement-api.md) |
| R-7 | A compromised staff account at AAL 2 can register a hostile redirect URI | Platform, accepted | Audited with before/after; detection, not prevention |

R-1 through R-3 are the reason [10](10-product-certification-checklist.md)
demands evidence rather than assurances.
