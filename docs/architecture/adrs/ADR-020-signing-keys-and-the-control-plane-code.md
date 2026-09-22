# ADR-020 — Signing keys live outside the database; the authorization code is control plane

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Architecture + Security
**Depends on:** [ADR-018](ADR-018-openid-connect-for-product-sso.md), [ADR-009](ADR-009-tenant-data-isolation.md)

---

## Context

Two storage decisions in the SSO design are consequential enough to record
separately from the protocol choice, because both are places where the
obvious implementation is wrong in a way that would not show up in a test.

## Decision 1 — the private signing key is never in the database

`SigningKey` rows hold the public half, the `kid`, the algorithm and the
lifecycle. They hold `private_key_ref` — **a name, not a key.** Resolving that
name to key material goes through `contracts/ports/signing.py`, whose default
implementation reads the process environment, populated in a deployment by
the secret manager.

This is the rule the system already applies to MFA secrets
(`identity.AuthFactor.secret_ref`), and it applies here for a strictly larger
reason: whoever holds this key can mint a token for any user, in any
organization, for any product.

Two properties follow, and the second is the one that matters operationally:

1. **A database dump yields no signing capability.** This is structural
   rather than a rule: `keys.jwks()` cannot leak a private key because the
   field does not exist. A "don't serialise this field" convention is one
   refactor from being violated; not having the field is not.
2. **Rotation requires no deployment.** An operator adds the new secret to
   the secret manager and runs `manage.py w360_signing_key`. Nothing is
   rebuilt and no code changes ([08](../../phase-3/08-key-management.md)).

### Rotation is a three-state sequence, not a swap

`pending` → `active` → `retiring` → `retired`. A key is **published before it
signs** and **stays published after it stops**. Compressing the first two
steps is the mistake that makes rotation an outage: the first token signed by
an unpublished key fails verification at every product whose key-set cache has
not refreshed yet.

Exactly one key may be `active`, enforced by a partial unique index rather
than by application discipline — two active keys would make "which key signed
this?" depend on query ordering.

### Rotation has no HTTP surface and no `AuditEvent`

It is an operator action on a terminal. The generate step produces private key
material that must reach the secret manager; an API that returned it would be
the worst endpoint in the system, and one that did not could not complete a
rotation.

It therefore has no actor, so it cannot write an `AuditEvent` — which needs a
`TenantContext`, meaning a user and a membership. A synthetic one would put an
invented person's identifier against a platform event, the same objection
`audit.record_system` already makes. And a platform key rotation is not a
tenant event: `AuditEvent` is tenant-scoped and customer-readable, so writing
it against whichever organization the operator happened to be in would put a
platform event in one customer's trail and no other's.

It is recorded in the **security-event stream**
([11-observability.md §9](../11-observability.md)) by `keys.py`, and
`test_key_rotation_is_recorded_in_the_security_log` asserts that, so "key
rotation is audited" is a checked claim rather than a sentence in a document.

## Decision 2 — `AuthorizationCode` is control plane, exempt from W360.E001

`ecosystem.AuthorizationCode` carries an `organization_id` column and is
**not** a `TenantScopedModel`. It is added to `TENANT_SCOPE_EXEMPT`.

### Why the exemption is necessary, not convenient

The token endpoint has **no tenant context and cannot have one.** There is no
session, no membership and no organization until the code is found — and the
code is what establishes them. A tenant-scoped manager would require the
answer in order to ask the question.

Encoding the organization in the code itself was considered and rejected: it
would leak the tenant identifier to whoever holds the code, before anything
about it has been validated, and it would make the code non-opaque.

### What protects the row instead

- It is reachable only by the SHA-256 of a **256-bit random secret**. The
  plaintext code is never stored, so a database dump yields nothing
  redeemable — the same rule `organizations.Invitation.token_hash` already
  establishes.
- It is bound to one client, one redirect URI and one PKCE challenge, so
  possession alone is not sufficient.
- It lives sixty seconds and is single-use, spent by a conditional UPDATE.

This is the same class as `identity.User` — control-plane-adjacent, carrying
a reference it does not derive access from — and the same precedent as
`events.OutboxEvent`, which carries `organization_id` as a plain routing
column for an infrastructure table drained across every tenant.

### Every other ecosystem table carries no tenant reference at all

`ProductIntegration`, `IntegrationCredential` and `SigningKey` describe *the
ecosystem*, not a customer's business. They are global reference data, exactly
like `catalog.Product`.

## Alternatives considered

**Encrypt the private key in the database with a key from the environment.**
Rejected: it moves the secret rather than removing it, and the decryption key
then has to live somewhere — usually the same environment, at which point the
database round trip has bought nothing but a longer incident report.

**Make `AuthorizationCode` tenant-scoped and have the token endpoint
enter the organization's scope first.** Rejected as circular: the scope is
what the lookup is for.

**Keep a static signing key and rotate by redeploying.** Rejected. A key
nobody can rotate without a release is a key that is still in place the day it
leaks, and [ADR-018](ADR-018-openid-connect-for-product-sso.md)'s offline
verification makes the rotation window the difference between a contained
incident and a forced ecosystem-wide re-authentication.

## Consequences

**Positive**

- A compromise of the platform's database does not yield the ability to mint
  tokens, and does not yield a redeemable authorization code.
- Rotation is an operational routine rather than a release, and is proven not
  to invalidate unexpired tokens.
- The exemption is narrow, documented, and pinned by the exemption list
  itself, so a second one requires the same deliberate act.

**Negative / accepted**

- `TENANT_SCOPE_EXEMPT` grows by one entry. Recorded as D-008 in the
  [debt register](../debt-register.md) with a review date.
- `AuthorizationCode` has no RLS policy. Accepted for the reasons above; the
  row holds no business data, is unreachable without a 256-bit secret, and
  lives for sixty seconds.
- An operator must have terminal access to rotate a key. That is the intended
  blast radius.

## Enforcement

| Rule | Enforced by |
|---|---|
| No private key material in the database or in any response | `test_the_private_key_is_never_stored_in_the_database`, `test_no_private_key_material_is_reachable_over_http` |
| A key is published before it signs | `test_a_pending_key_is_published_before_it_signs` |
| Rotation does not invalidate unexpired tokens | `test_a_token_signed_before_rotation_still_verifies_after` |
| Exactly one active key, under concurrency | `test_only_one_signing_key_survives_concurrent_promotion` |
| A key whose secret is missing cannot be promoted | `test_promotion_refuses_a_key_whose_secret_is_missing` |
| Rotation reaches the security-event log | `test_key_rotation_is_recorded_in_the_security_log` |
| Codes are stored only as a hash | `test_the_code_is_stored_only_as_a_hash` |
| One code yields one token set under concurrency | `test_one_authorization_code_yields_exactly_one_token_set` |
