# 17 — Phase 3 Adversarial Audit: Findings

An independent adversarial review of the remote product integration boundary,
run against the code rather than against the documents, before Phase 3 is
frozen.

**Result: PASS.** No P0. No P1 that blocks Phase 4. One P1 found, reproduced
and fixed; four P2s fixed; four P2s accepted and recorded with owners.

The starting baseline was **912 passing** on a fresh database; the finish line
is **933 passed, 0 failed, 0 skipped**, also from a database migrated from
zero. Every finding below that was fixed has a regression test written to fail
against the code as it stood.

---

## Method

Where a control was claimed, the claim was reproduced as a test before it was
believed. Three things were deliberately *not* trusted:

- **Passing happy-path tests.** Standards conformance was checked against
  RFC 6749 §4.1, RFC 7636 §4.2, RFC 7523 §3 and OIDC Core §3.1.3.7, not
  against the suite.
- **Comments.** Two of the findings below are comments that described
  behaviour the code did not have — which is worse than an absent comment,
  because it stops the next reader looking.
- **Absence.** "Nothing logs the code verifier" was checked by looking at the
  redaction backstop's actual patterns, not by grepping for the current call
  sites.

---

## P1 — fixed

### P1-1 The authorization code was an unchecked access grant for 60 seconds

**What happened.** `redeem_code` verified the code's *bindings* — client,
redirect URI, PKCE challenge, expiry, single use — and nothing about the
subject's live state. Membership, organization status and entitlement were
checked once, at `/authorize`, and the answer was carried in the row.

Reproduced three ways. In each, a code was minted, the state was changed, and
the code was exchanged:

| Change between authorize and token | Observed | Expected |
|---|---|---|
| Membership set to `removed` | **200, fresh 300 s ID token** | refused |
| Organization set to `locked` | **200, fresh 300 s ID token** | refused |
| `Entitlement` and snapshot deleted | **200, fresh 300 s ID token** | refused |

**Why it is P1 and not P2.** The consequence is not only the extra 60 seconds.
`05 §7` stated a maximum revocation window of 330 s; the real worst case was
**390 s**, and an operator removing someone was being told the removal was
effective when a token could still be minted for them. A documented security
bound that is wrong is worse than a bound that is known to be loose.

`models.py` additionally carried the comment *"Re-validation at redemption is
explicit and is in `authorization.py`, where it can be read."* It was not, and
that sentence is why nobody had looked.

**Fix.** `authorization._revalidate_subject` re-runs the three checks before
the code is spent, through `resolve_subject` and `require_entitlement` — the
*same two functions* the authorization endpoint uses, so there is no second
definition of "is this person still allowed in?" to drift.

Three properties, each deliberate:

- **Uniform failure.** Everything becomes `invalid_grant`, like every other
  redemption failure. Naming the cause would tell whoever holds a stolen code
  which part of their attempt was wrong. The product restarts the flow and the
  authorization endpoint answers precisely, redirectably.
- **Before the spend, not after.** An unavailable entitlement store propagates
  as 503 and leaves the code live for the retry. Spending first would burn a
  legitimate user's sign-in on a database blip.
- **Never a denial on failure.** The rule `14 §9` already states for the
  entitlement endpoint, applied here.

**Cost.** The token endpoint's query budget moved from ≤ 12 to ≤ 26. It runs
once per product sign-in, next to a 28 ms RS256 signature. The same cost on a
per-request path would be indefensible, which is exactly why verification is
offline and this endpoint is reached once.

**Tests.** Four in `tests/security/test_ecosystem_security.py`, including
`test_the_unchanged_case_still_exchanges` so the check cannot pass by simply
being broken, and `test_a_failed_revalidation_read_is_a_503_and_never_a_denial`
which also asserts the code survives.

---

## P2 — fixed

### P2-1 A non-ASCII `redirect_uri` at the token endpoint returned 503

`hmac.compare_digest` raises `TypeError` on `str` arguments containing any
non-ASCII character. Both operands of the redirect-URI comparison come off the
wire, so `redirect_uri=https://exämple.test/cb` with a valid code produced:

```
TypeError: comparing strings with non-ASCII characters is not supported
→ 503 server_error, a stack trace at ERROR level,
  and ecosystem_token_issuance_failures_total incremented
```

Wrong on three counts: the protocol answer should be `invalid_grant`/400, the
platform's own token-issuance failure metric was being driven by client input,
and the error log was being filled by a request the endpoint should simply
refuse. The same reachable via `code_challenge`, which was stored unvalidated.

**Fix.** `_constant_time_equals` encodes both operands to UTF-8 first — still
constant time, every input representable. Three parametrised cases (non-ASCII
host, line separator, NUL).

### P2-2 `code_challenge` was not validated against RFC 7636 §4.2

Any string was accepted, stored, and the browser redirected with a usable
code. It could never match a verifier, so the flow failed closed — but it
failed at the *token* endpoint a minute later, at the product, with
`invalid_grant` and no way for its developer to see why.

**Fix.** Validated at `/authorize` where the client can still act on the
answer: 43–128 characters from the unreserved set, `invalid_request`
otherwise. Six parametrised cases, including base64-instead-of-base64url
(`+`, `/`, `=`), which is the mistake an integrator actually makes.

### P2-3 `nonce` was silently truncated to 255 characters

OIDC Core §3.1.3.7 step 11 has the client compare the echoed nonce with the
one it sent. A shortened one is therefore a token the client is *obliged* to
reject — an unexplained login failure the platform caused and did not report.

**Fix.** Refused with `invalid_request` above 255 characters; the 255-character
case is asserted to round-trip intact.

### P2-4 The log-redaction backstop did not cover OAuth bearer material

The backstop's own docstring says it exists "because one day someone will",
which is only true of the field names it matches. `client_assertion` — a
credential that authenticates an entire product — matched nothing.
`code_verifier` matched nothing. Neither is logged today; the net had a hole
under exactly the values this phase introduced.

**Fix.** `assertion`, `verifier` and `authorization_code` added, with a canary
test that asserts four secrets are redacted **and** that `product_code` and
`reason_code` survive — a backstop that eats the fields making a log line
useful gets turned off.

### P2-5 A client assertion could outlive the memory of having been spent

`07 §3` documented "`exp` short | Maximum age 300 s". The code bounded the age
of `iat` *only when `iat` was present* — and `iat` was not required — and never
bounded `exp` at all. An assertion with `iat = now, exp = now + 86400` was
accepted and stayed valid for a day, while its `jti` was remembered for 300 s.
After that, a captured assertion was replayable.

**Fix.** `iat` required; `exp` must be within `OIDC_CLIENT_ASSERTION_MAX_AGE_S`
of now. Three tests, including one that an ordinary short-lived assertion still
authenticates.

---

## P2 — accepted, with owners

### P2-6 Rate-limit buckets are keyed on `REMOTE_ADDR` (D-009, R-8)

Confirmed: 70 unauthenticated requests carrying only a real `client_id` and a
registered redirect URI exhausted that product's `authorize` bucket and the
next legitimate sign-in got 429. Behind a proxy that does not rewrite
`REMOTE_ADDR`, every user shares one key, so the bucket is a per-product
ceiling of 60/minute that an anonymous caller can fill. Both inputs are public
by construction — a client id appears in every authorization URL.

**Not fixed, deliberately.** The obvious fix — read `X-Forwarded-For` — is
worse: a forwarded header is client-supplied, and trusting it without knowing
the proxy depth lets any caller spoof an address and evade the limit entirely.
The correct fix is topology-dependent and belongs with the deployment. It is
now stated as a deployment requirement ([14 §11](14-failure-mode-review.md))
rather than assumed, and carried as D-009.

Bounded: never crosses products, and a client that authenticates successfully
never touches the failure bucket.

### P2-7 `/authorize` with an unknown `client_id` is not rate limited at all

120 requests with unknown client ids returned 120 × 401 with no throttle. The
limiter runs after `resolve_client`, because there is no client to key the
bucket on before it. Each request is one indexed lookup; the amplification is
modest and the endpoint discloses nothing. Same root cause as P2-6 — an
IP-keyed pre-bucket cannot be added safely until the client IP is
trustworthy, and adding one now would *create* the cross-tenant denial P2-6
avoids.

### P2-8 Seat limits are still not enforced at sign-in — **trigger reached**

See §"Seat trigger" below.

### P2-9 `keys.retire_key` does not itself enforce the safe retirement window

The wait is enforced in the management command, which is the only caller and
the only operator path, and `--force` is the documented compromise lever.
Moving the guard into the service would be defence in depth; it would also
require three existing tests to pass `force=True` to say what they already
say. Left as is, recorded here so the placement is a decision rather than an
oversight.

---

## Cleanup (removed)

| Removed | Callers | Why it was not merely tidy |
|---|---|---|
| `protocol.ACCESS_DENIED`, `protocol.UNAUTHORIZED_CLIENT` | 0 | **Published** to every product in `workeasy360_error_codes`. An integrator writes a branch for a response the server cannot produce, and maintains it forever |
| `SigningKeyStatus.is_published` | 0 | The JWKS query filters in SQL and cannot use a Python property, so it could never be the thing enforcing the rule it described |
| `AuthorizationCode.is_live` | 0 | Read as "may this code be redeemed?" while checking two of the seven things redemption checks. A second, weaker definition of a security question is how the weaker one gets called |

`protocol.py` claimed the code list was "asserted by a test". It was not —
which is how two entries drifted into it. It is now:
`test_every_published_error_code_is_one_the_platform_can_emit` walks the AST of
every ecosystem source and the ecosystem views, collects the error-code
constants referenced at a raise site, and asserts that set equals both
`ERROR_CODES` and the published list, in both directions. The `ERROR_CODES`
literal is excised from the scan, or the test would assert that the list
matches itself. Verified non-vacuous by re-injecting `access_denied` and
watching it fail with a named diagnostic.

---

## Seat trigger

**TRIGGER REACHED: YES. Recommended phase: the one that builds the app
launcher.**

The gap is real: an organization holding CRM with 5 of 5 seats assigned still
authenticates user 6 into CRM. What the audit adds to the existing P2-1 note
is the part that decides the phase:

> **A product cannot enforce it on the platform's behalf either.** The ID
> token deliberately carries no `membership_id`, and the entitlement endpoint
> takes an organization and nothing else. There is no field anywhere in the
> remote contract in which a product could ask, or be told, whether *this
> user* holds a seat.

So "the product will handle it" is not available, and the gap closes in the
platform or not at all.

**Not a security gap**, which is why it is not P1: a user reaching a product
without a seat is an authenticated member of an organization that genuinely
holds the product. Nothing is escalated and no tenant boundary is crossed. It
is a commercial licensing gap.

**Why the launcher phase.** That surface already has to answer "which products
can *this person* open?", which is the same question, and it is where "ask your
administrator for a seat" is naturally rendered. Closing it needs either a
per-member identifier in the token or a `sub` on the entitlement endpoint —
both contract changes, and `no_seat` is already reserved so neither is
breaking.

---

## Verified, no finding

Checked adversarially and found correct. Listed because "we looked" is the
useful half of an audit.

| Area | Evidence |
|---|---|
| Canonical issuer | From settings only. `X-Forwarded-Host: evil.example` + `X-Forwarded-Proto` leaves the discovery document untouched. No `build_absolute_uri` anywhere in the module |
| Open redirect | Exactly two `HttpResponseRedirect` sites, both reachable only with a `ClientResolution`, which only `resolve_client` produces, which exact-matches. Error redirects covered by the same type |
| Algorithm confusion | Algorithm taken from the *key record* at every verify, never from the token or assertion header. `none` and HMAC both unreachable |
| Code replay | Conditional `UPDATE ... WHERE redeemed_at IS NULL`; 8-way concurrency yields exactly one token set |
| Cross-product isolation | CRM token rejected by Books; CRM code not redeemable by Books; no field in which to name another product's entitlement |
| Organization binding | Fixed at issue from a verified membership row, re-verified at redemption. Named by opaque UUID, never slug |
| Expired / revoked credentials | Refused on the next request, no cache anywhere |
| SSRF | No HTTP client importable in the module, asserted structurally. Trigger **NOT REACHED** |
| CSP | No HTML surface exists — zero templates in the repository. Trigger **NOT REACHED** |
| RabbitMQ on the auth path | Celery appears only in `tasks.py` (the hourly prune) |
| Product-specific branching | AST scan for comparisons against nine product codes; every textual hit is a docstring |
| `products/` | Ten `__init__.py` files, asserted unconditionally |
| Reference client independence | AST import scan; imports `jwt` and nothing from `workeasy360` |
| AAL 2 bypass | No Django admin is installed, so there is no second write path to the registry |
| Metric cardinality | No ecosystem metric carries a client id, organization id, user id, jti or URL as a label |
| Token size | 1 269 B at maximum claims — see [13](13-performance-review.md) |
| Dependencies | `PyJWT==2.14.0`, `cryptography==50.0.1`, both exactly pinned. `pip-audit --strict`: clean. `bandit -ll`: clean |

### Observations that are not findings

- **`/userinfo`'s audience check is tautological** — the expected audience is
  read from the token being verified. It is not exploitable: the signature,
  issuer, expiry and `typ` are all verified against platform keys, and the
  endpoint returns only the token subject's own profile gated by the token's
  own scope. The audience claim has no work to do when the resource is the
  user rather than a product.
- **A suspended integration's already-issued access token still works at
  `/userinfo`** until it expires. That is the documented offline model
  (`05 §7`: "Integration suspended → valid until `exp`"), consistent rather
  than anomalous. New authentication, token exchange and entitlement calls all
  stop immediately.
- **`decision_for_organization` costs one extra query for an organization with
  no snapshot**, making "unknown organization" marginally slower than "known
  but unentitled". Both return the identical body. The timing difference is
  far below what is measurable across a network and is noted only because the
  module's docstring promises indistinguishability.
- **`check --deploy` reports six warnings under `config.settings.migration`.**
  That module exists to run migrations, not to serve traffic; the production
  posture is asserted separately and positively by
  `test_production_accepts_a_valid_configuration`.
