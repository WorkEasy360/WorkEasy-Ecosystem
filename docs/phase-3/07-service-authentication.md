# 07 — Service-to-Platform Authentication

Decision of record: [ADR-019](../architecture/adrs/ADR-019-product-service-authentication.md).

Two endpoints are reached by a product's server with no user and no browser:
the token endpoint and the entitlement verification endpoint. Both need to
know which product is calling — and on the entitlement endpoint the **caller's
identity determines the subject of the question**, so a spoofable credential
would give cross-product entitlement probing immediately.

## 1. `private_key_jwt` — recommended

RFC 7523. You sign a short-lived assertion with a private key you never send.
We store only the public half.

```json
{
  "iss": "<your client_id>",
  "sub": "<your client_id>",
  "aud": "https://app.workeasy360.com/api/v1/integrations/oauth/token",
  "iat": 1790000000,
  "exp": 1790000060,
  "jti": "<unique per assertion>"
}
```
Header: `{"alg": "RS256", "kid": "<your credential_id>"}`

Sent as:
```
client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
client_assertion=<the JWT>
```

Rules, each of which is checked:

| Rule | Why |
|---|---|
| `iss` and `sub` are both your client id, and must match | RFC 7523 §3. Refuses an assertion that names one client and is signed as another |
| `aud` is the **token endpoint URL** | Without it, an assertion you legitimately created for another service could be replayed here by whoever received it |
| `jti` unique | Spent on first use; a captured assertion cannot be replayed inside its own window |
| `iat` present and recent | Required, not optional — it is what the 300 s bound is measured against |
| `exp` within 300 s of now | Enforced, not advisory. A `jti` is remembered for 300 s; an assertion valid for longer would outlive the memory of having been spent and become replayable again |
| `kid` names your credential | How we select the verifying key without trying all of them |

The algorithm is taken from **your registered credential**, never from the
assertion header.

**Why this is recommended:** a compromise of the platform's database yields no
credential an attacker can use as your product. That is not true of any
shared-secret scheme.

## 2. `client_secret_basic` / `client_secret_post` — alternative

A 256-bit secret, returned **exactly once** at creation.

```
Authorization: Basic base64(urlencode(client_id) ":" urlencode(secret))
```

Both halves must be form-urlencoded before Basic encoding (RFC 6749 §2.3.1).
Not doing so is a bug that only appears for secrets containing `:` or `%`,
which makes it the worst kind to ship —
`test_a_secret_containing_reserved_characters_round_trips` covers it.

`client_secret_post` is also accepted, because some HTTP clients cannot set an
Authorization header and refusing would push the secret somewhere worse.

## 3. Why the secret is SHA-256 and not Argon2

This platform hashes **passwords** with Argon2id, and that is correct there: a
human-chosen password has perhaps 40 bits of entropy, so the work factor is
what stands between a stolen hash and the plaintext.

These secrets have 256 bits of entropy from a CSPRNG. There is nothing to
guess. A work factor buys **no security whatsoever**, while costing ~100 ms on
every call to the two endpoints that must stay available — and handing any
authenticated caller a CPU-exhaustion lever against them.

Fast hashing of a full-entropy generated credential is what OWASP recommends,
and the reasoning is written at the field in `models.py` so the next reader
does not "fix" it.

The first 8 characters are stored in the clear as a lookup handle, so
verification is one indexed row rather than a hash per stored credential. 48
bits of a 256-bit secret helps an attacker who already has it with nothing.

## 4. mTLS

Evaluated, not implemented. It is the strongest option and also an
infrastructure commitment — a CA, issuance, rotation, revocation, and TLS
termination that must not strip the client certificate. None of that exists
yet, and `private_key_jwt` provides the property that matters most at the
application layer. Revisit when a product needs FAPI-grade assurance.

## 5. Rotation is additive

```
1. create the new credential            ← the old one keeps working
2. deploy it to your fleet
3. verify it works
4. revoke the old one
```

Creating a credential does **not** revoke the existing one. A rotation that
required atomically swapping a secret across a fleet is a rotation nobody
performs — and a credential nobody rotates is a credential that is eventually
leaked.

```
POST   /api/v1/orgs/<slug>/integrations/<client_id>/credentials
GET    /api/v1/orgs/<slug>/integrations/<client_id>/credentials
DELETE /api/v1/orgs/<slug>/integrations/<client_id>/credentials/<credential_id>?reason=…
```

## 6. Revocation is immediate

No propagation delay and nothing to wait out: client authentication reads the
row on every request, and there is no cached credential state anywhere.

This is the deliberate contrast with **token** revocation, which is bounded by
the token lifetime because tokens are verified offline. A credential is
checked by us; a token is checked by you.

`test_a_credential_revoked_mid_flight_stops_working` proves a code issued
while the credential was live cannot be exchanged after revocation.

Revocation requires a reason and is **idempotent** — an operator responding to
an incident must not be stopped by an error saying the thing they wanted has
already happened.

## 7. Expiry

Every credential has one, defaulting to 365 days. A credential with no end
date outlives the person who created it and the reason it existed.

Use is recorded at **day** granularity. The question this answers is "is
anything still using this?" before revoking it; a per-second timestamp would
turn every token exchange into a database write for an answer nobody needs to
the second.

## 8. One failure response

Unknown client id, wrong secret, bad signature, revoked credential, expired
credential, wrong assertion audience, replayed assertion:

```
HTTP 401
WWW-Authenticate: Basic realm="workeasy360", charset="UTF-8"

{"error": "invalid_client",
 "error_description": "Client authentication failed.",
 "correlation_id": "…"}
```

Identical for all of them. Distinguishing them makes this a
client-enumeration oracle and tells an attacker when they have found a real
client id, which is most of the work.

The `client_secret` path deliberately **hashes even when no credential row was
found**, so the "no such client" path is not measurably faster than the
"wrong secret" path — the timing difference is the same oracle by another
route.

**One exception, after successful authentication:** a caller whose integration
is suspended is told `integration_suspended` (403). They have already proved
they hold a valid credential, so it discloses nothing they could not determine
anyway — and it is the one thing that lets their operator fix it.

## 9. Rate limits

| Bucket | Default | Scope |
|---|---|---|
| `token` | 120/min | per client |
| `verify` | 600/min | per client |
| `client_auth_failure` | **10/min** | per client (or IP if unidentified) |

The failure bucket is an order of magnitude tighter than the success path,
because a caller failing authentication ten times a minute is not a caller
with a configuration problem.

429 responses carry `Retry-After`.

## 10. Secrets never appear in logs or audit

`test_no_credential_secret_ever_reaches_the_audit_log` asserts it against the
**actual generated secret**, not against a pattern. `audit.redact` would strip
a secret-shaped key anyway, and nothing writes one.
