# 08 — Signing Key Management

Decision of record: [ADR-020](../architecture/adrs/ADR-020-signing-keys-and-the-control-plane-code.md).

The platform signs every identity token every product verifies. That private
key is the most valuable secret in the ecosystem: whoever holds it can mint a
token for any user, in any organization, for any product.

## 1. Where the key lives

**Not in the database.**

`SigningKey` rows hold the public half, the `kid`, the algorithm and the
lifecycle. They hold `private_key_ref` — *a name, not a key*. Resolving that
name goes through `contracts/ports/signing.py`, whose default implementation
reads the process environment, populated in a deployment by the secret
manager.

```
SigningKey(kid="Y0U6…", algorithm="RS256",
           public_key_pem="-----BEGIN PUBLIC KEY-----…",
           private_key_ref="k2",          ←  a name
           status="active")

secret manager:  W360_OIDC_SIGNING_KEY_K2 = "-----BEGIN PRIVATE KEY-----…"
```

Two properties follow:

1. **A database dump yields no signing capability.** This is structural, not a
   rule: `keys.jwks()` cannot leak a private key because the field does not
   exist. A "don't serialise this field" convention is one refactor from being
   violated; not having the field is not.
2. **Rotation needs no deployment.** Add the secret, run the command.

This is the same pattern `identity.AuthFactor.secret_ref` already establishes
for MFA secrets, for a strictly larger reason.

## 2. The four states

| State | In the JWKS? | Signing? | Meaning |
|---|---|---|---|
| `pending` | ✅ | ✗ | Published, not yet signing. Products' caches pick it up |
| `active` | ✅ | ✅ | The one key currently signing. **Exactly one** |
| `retiring` | ✅ | ✗ | No longer signing; still verifies tokens issued before the switch |
| `retired` | ✗ | ✗ | Gone. Tokens it signed no longer verify anywhere |

"Exactly one active" is enforced by a **partial unique index**, not by
application discipline — two active keys would make "which key signed this?"
depend on query ordering, and a half-finished rotation indistinguishable from
a finished one. Proven under concurrency by
`test_only_one_signing_key_survives_concurrent_promotion`.

## 3. Routine rotation

```bash
# 1. Generate. Published immediately; signs nothing.
manage.py w360_signing_key generate --ref k2

#    The private PEM is printed ONCE. Put it in the secret manager as
#    W360_OIDC_SIGNING_KEY_K2. It is not stored, not logged, not audited.

# 2. Wait for products' JWKS caches to refresh.
#    Cache-Control on /.well-known/jwks.json is max-age=300, so five
#    minutes is sufficient; an hour is comfortable.

# 3. Promote. The new key starts signing; the old becomes `retiring`.
manage.py w360_signing_key promote --kid <kid>

# 4. Wait out the longest token lifetime.
#    OIDC_ACCESS_TOKEN_TTL_S + OIDC_CLOCK_SKEW_S = 930s by default.
#    The command computes it and refuses to retire early.

# 5. Retire.
manage.py w360_signing_key retire --kid <old-kid>

manage.py w360_signing_key list      # at any point
```

### Why steps 1 and 3 are separate

**This is the whole reason rotation is not an outage.** Products cache the key
set. A key that started signing the moment it was created would produce tokens
naming a `kid` that no product has yet seen, and every one of them would fail
verification until its cache refreshed.

Publishing ahead of use, and keeping published after use, are the two overlaps
that make rotation invisible.
`test_a_pending_key_is_published_before_it_signs` and
`test_a_token_signed_before_rotation_still_verifies_after` hold both.

### Why promotion verifies resolvability first

`promote_key` resolves the private half **before** switching. Promoting a key
whose secret has not reached the secret manager yet would stop token issuance
dead — an outage minutes after an operation that appeared to succeed. A failed
promotion leaves the incumbent untouched; it is not a half-done one.

### Why retirement is guarded

`retire` refuses while tokens signed by that key may still be valid, and names
the remaining seconds. Retiring early rejects unexpired tokens at every
product, which a user experiences as random authentication failures with no
pattern.

The active key cannot be retired at all — the caller almost certainly meant to
promote a replacement first.

## 4. Rotation has no HTTP surface

Three reasons, in order of weight:

1. The generate step produces private key material that must reach the secret
   manager. An API that returned the platform's private signing key over the
   network would be the worst endpoint in the system; one that did not return
   it could not complete a rotation.
2. There is no actor. `audit.record` needs a `TenantContext` — a user and a
   membership — and a terminal session has neither.
3. A rotation is not a tenant event. `AuditEvent` is tenant-scoped and
   customer-readable; writing a platform key rotation against whichever
   organization an operator happened to be in would put it in one customer's
   trail and no other's.

It is recorded in the **security-event stream**
([11-observability.md §9](../architecture/11-observability.md)):

```
ecosystem.signing_key.created    kid, algorithm, status
ecosystem.signing_key.promoted   kid, previous_kid
ecosystem.signing_key.retired    kid
```

`test_key_rotation_is_recorded_in_the_security_log` asserts all three fire and
that no key material appears in them — so "key rotation is audited" is a
checked claim rather than a sentence in this document.

## 5. Algorithms

| | Signing cost (measured) | Notes |
|---|---|---|
| **RS256** (default) | ~27.7 ms p50 | OpenID Connect Core **requires** every implementation to support it, so it is the one algorithm an independently written product is guaranteed to verify |
| ES256 | ~1.3 ms p50 | Smaller keys and tokens, ~20× faster to sign. Fully supported; set per key |

Signing happens once per authentication, not per request, so 27 ms is not on
any hot path. A deployment signing at high volume should prefer ES256; the
code path is identical and `test_both_supported_algorithms_sign_and_verify`
covers both.

HMAC algorithms and `none` are **not** in `SUPPORTED_ALGORITHMS`, and the
verifier is always given the algorithm from the key record rather than from
the token. That is what closes the algorithm-confusion attack.

## 6. Key compromise — incident runbook

If a private signing key is disclosed, an attacker can mint a token for any
user in any organization for any product. Treat as a platform-wide P0.

```bash
# 1. Generate a replacement and place the secret. Do NOT wait for caches.
manage.py w360_signing_key generate --ref k-incident
#    ... store the printed PEM in the secret manager

# 2. Promote it immediately.
manage.py w360_signing_key promote --kid <new-kid>

# 3. Retire the compromised key, bypassing the safety wait.
manage.py w360_signing_key retire --kid <compromised-kid> --force
```

**What `--force` does:** removes the key from the JWKS at once. Every
unexpired token it signed stops verifying everywhere within one JWKS cache
period (≤ 300 s). Every affected user must sign in again.

That is the correct response to a compromise and the wrong one to a routine
rotation, which is why it is a separate flag rather than a shorter timer.

**Expected impact:** every product session established in the last 5 minutes
is invalidated; products that already exchanged their token for a local
session are unaffected until that session ends. Products following
[10](10-product-certification-checklist.md) will refresh the JWKS and reject
the old `kid` without any change on their side.

**After the incident**
- rotate the secret-manager credential that leaked the key;
- audit `ecosystem.token.issued` for the compromised `kid` over the exposure
  window — the `jti` is logged for exactly this;
- rotate every product credential if the same storage was exposed
  ([07 §5](07-service-authentication.md));
- record the incident and the exposure window.

## 7. Bootstrapping a new environment

```bash
manage.py w360_signing_key generate --ref k1
#   ... store W360_OIDC_SIGNING_KEY_K1
manage.py w360_signing_key promote --kid <kid>
```

Until a key is promoted, **token issuance fails with 503 and everything else
works**. That is deliberate: a missing signing key must not fail readiness and
de-pool an instance whose login, billing and organization management are
perfectly healthy. `/healthz/deep` reports `NO ACTIVE SIGNING KEY`.
