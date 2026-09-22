# 15 — Cleanup Review

Run after implementation, over the code this phase added. Only **verified**
waste was removed; nothing was deleted on suspicion.

## Method

An AST scan over every top-level declaration in `platform/ecosystem`,
`contracts/values/manifest.py` and the two new ports, counting references
across the whole of `backend/`. 206 declarations; 8 referenced only at their
own definition. Each was then examined by hand, because "referenced once" is
a starting point, not a verdict — Django hooks and framework entry points are
legitimately called by name from outside Python.

## Findings

### Removed: three dead signing-key service wrappers

`services.rotate_signing_key`, `promote_signing_key` and `retire_signing_key`
existed with authorization, a `platform.integration.key` permission and audit
entries. **Nothing called them.** The management command called `keys.py`
directly, because it could not build the `TenantContext` that `audit.record`
requires.

They were removed rather than wired up, and the decision is worth recording:
wiring them up meant building an HTTP endpoint, and the generate step returns
the platform's private signing key. An API that returned it over the network
would be the worst endpoint in the system; one that did not could not complete
a rotation.

Removed with them:

- the `platform.integration.key` permission, which nothing could then reach;
- its entry in the test fixture's grant list.

**What replaced them:** an explicit note in `services.py` stating that key
rotation has no service layer and why, plus
`test_key_rotation_is_recorded_in_the_security_log`, which asserts the three
rotation events reach the security-event stream and that no key material
appears in them. "Key rotation is audited" became a checked claim rather than
a sentence in a document.

### Fixed: an unbounded table

`authorization.prune_expired` existed and nothing called it, so
`ecosystem_authorization_code` grew a row per product sign-in forever — and
the redemption lookup, which a user waits for, got slower with every login
that ever happened.

This one was **not** dead code; it was a missing caller. Added
`platform/ecosystem/tasks.py` with an hourly Beat entry on the `heavy` queue,
and `test_expired_authorization_codes_are_pruned` covering both the deletion
and the deliberate one-hour retention past expiry (so a replay attempt still
finds the row and is logged as a replay).

### Removed in the Phase 3 audit: two unemittable error codes

`protocol.ACCESS_DENIED` and `protocol.UNAUTHORIZED_CLIENT` were declared,
included in `ERROR_CODES`, and **published to every product** in the discovery
document's `workeasy360_error_codes`. Nothing raised either.

That is worse than ordinary dead code, because the list is a contract. An
integrator reading it writes a branch for `access_denied` and maintains it
forever for a response the server cannot produce. The module docstring already
said the list was "asserted by a test"; no such test existed, which is how two
entries drifted into it.

Both removed, and
`test_every_published_error_code_is_one_the_platform_can_emit` added: it walks
the AST of every ecosystem source file and the ecosystem views, collects the
error-code constants actually referenced at a raise site, and asserts that set
equals both `ERROR_CODES` and the published list — in **both** directions, so a
code that is emitted without being published fails too. The `ERROR_CODES`
literal itself is excised from the scan, or the test would assert that the
list matches itself.

### Removed in the Phase 3 audit: two unused predicates

| Removed | Callers | Why it mattered |
|---|---|---|
| `SigningKeyStatus.is_published` | 0 | The JWKS query filters on `status != retired` in SQL and cannot use a Python property, so the predicate could never be the thing enforcing the rule it described |
| `AuthorizationCode.is_live` | 0 | The dangerous kind. It read as "may this code be redeemed?" while checking two of the seven things `redeem_code` checks — no client, no redirect URI, no PKCE challenge, no membership, no entitlement. A second, weaker definition of a security question is how the weaker one eventually gets called |

Replaced by a comment at the former site of `is_live` saying where the real
answer lives, because "there is deliberately no predicate here" is the part a
future reader needs.

### Kept, with the coverage that justified them

`reset_product_directory` and `reset_signing_key_provider` were unreferenced.
The tempting fix was to delete them. The better one was to notice what their
absence implied: **the fail-closed path had no test.** Added
`test_signing_fails_closed_when_no_provider_is_registered` and
`test_registration_fails_closed_when_no_product_directory_is_registered`,
which assert that a missing registration raises rather than falling back — the
property both ports exist to have.

The waste was the missing coverage, not the hooks.

### Kept, correctly

| Item | Why |
|---|---|
| `Command.add_arguments` | Django calls it by name |
| `EcosystemConfig` | Referenced by string from `INSTALLED_APPS` |

### Removed: a misleading predicate

`protocol.NON_REDIRECTABLE` and `OAuthError.is_redirectable` modelled
redirectability as a property of the **error code**. It is not: RFC 6749
§4.1.2.1 ties it to *when* the failure happened, and the same code
(`invalid_request`) is rendered for a missing `redirect_uri` and redirected
for a missing `code_challenge`.

Keeping it would have been worse than not having it — it was wrong in both
directions at once and would have been trusted. Replaced by the structural
split between `resolve_client()` and `parse_request()`, and by a comment in
`protocol.py` explaining why no such set exists.

This one was found by a **failing test**, not by the scan:
`test_a_suspended_integration_cannot_authenticate` expected a redirect and got
a rendered error.

### Removed: an invented header

An early draft of the reference client sent client assertions in
`X-Client-Assertion` headers so the entitlement endpoint could take a JSON
body. That would have been a proprietary authentication mechanism in a phase
whose point is not having one.

The endpoint now takes a form-encoded request exactly like the token endpoint,
so there is **one** client-authentication code path
([06 §3](06-entitlement-api.md)).

## Two over-broad checks I wrote, then narrowed

Both failed against correct code, which is the useful kind of failure.

**The outbound-I/O guard** forbade the package root `urllib`, catching
`urllib.parse` — string parsing, and exactly what the manifest validator
needs. Narrowed to name the fetching modules precisely (`urllib.request`,
`http.client`, `socket`, …). Matching on the root would have forbidden the
safe one and taught the next reader that the rule is arbitrary.

**The repository secret scanner** flagged a bare `-----BEGIN PRIVATE KEY-----`
in a test asserting that no key reaches a log. Narrowed to require the header
**and** a base64 body — which is what a committed key actually looks like.

That narrowing is only acceptable if the pattern still catches what it exists
for, so `test_the_secret_scanner_detects_a_real_key` now asserts it against a
genuinely generated RSA key, in both plain and escaped-newline form, and
asserts the false positive stays ignored. The check is more precise, not more
permissive.

## Dependencies

| Added | Version | Why | Alternative rejected |
|---|---|---|---|
| `PyJWT` | 2.14.0 | RFC 7515/7517/7519 signing, verification, JWK Set | Authlib — larger, and only its JOSE half would be used |
| `cryptography` | 50.0.1 | PyJWT's asymmetric backend; also used directly for key generation | — |

**Removed: none.** The Phase 3R audit had already removed
`djangorestframework` and `python-dotenv`; nothing else became unused.

`cryptography` is pinned explicitly rather than taken via `PyJWT[crypto]`,
because `keys.py` imports it directly to generate and serialise key pairs. It
is a direct dependency and the requirements file should say so.

`pip-audit --strict`: **no known vulnerabilities**.

## Speculative surface, assessed

Two manifest fields are validated but unused, and were **kept** after
consideration:

- `post_logout_redirect_uris` — RP-initiated logout is not implemented
- `health_url` — nothing fetches it

Both are single validated fields on a document the product submits anyway.
Removing a field from a versioned external contract and adding it back later
is a version bump for every registered product; adding an unused validated
field is not. And validating them now means a product cannot register an
unsafe value today that becomes reachable when the feature lands.

Both are called out explicitly in [02 §5](02-product-manifest.md) rather than
left for a reader to discover.

The rest of the module was checked for the same shape and found clean: no
unused manifest field beyond those two, no unused setting, no unused index, no
duplicate JWT parsing path (`tokens.verify` and `clientauth` share the same
ordering but verify different artefacts against different key tables), no
compatibility shim, no middleware added.

## Regression

Full suite re-run after every removal above: **912 passed, 0 failed, 0
skipped**, 12/12 import contracts, `ruff`, `mypy`, `bandit`, `pip-audit`,
`manage.py check` and `makemigrations --check` all clean.
