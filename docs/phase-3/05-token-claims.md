# 05 — Token Claims

Every claim below had to justify itself against one question: **could the
product do its job without this?** Claims that could not answer it are not
issued. The list is pinned by
`test_the_token_claim_set_is_exactly_what_is_documented` against the
discovery document, so this file cannot drift from what is actually minted.

## 1. The ID token

```json
{
  "iss": "https://app.workeasy360.com",
  "sub": "8f3a…",
  "aud": "kQ7x…",
  "iat": 1790000000,
  "nbf": 1790000000,
  "exp": 1790000300,
  "jti": "3c9f…",
  "typ": "id_token",
  "nonce": "…",
  "organization_id": "b21c…",
  "product_code": "crm",
  "aal": 2
}
```

### Standard claims

| Claim | Meaning | Why a product needs it |
|---|---|---|
| `iss` | The platform's configured issuer | Reject a token from another provider. Compared by **exact string equality** (OIDC Core §3.1.3.7) — a trailing slash breaks every product, which is why production refuses an issuer with one |
| `sub` | Stable opaque user id | The user's identity. See §3 |
| `aud` | The **client id**, not the product code | Reject a token minted for another product. This is the audience-confusion control |
| `iat` / `nbf` / `exp` | 300-second window | Bound staleness |
| `jti` | Unique per token | Detect a replay within the lifetime |
| `typ` | `id_token` | Refuse an access token presented as an identity assertion — both verify against the same key with the same audience |
| `nonce` | Echoed from the request | Detect a token replayed from another session |
| `auth_time` | When the person last entered their credentials at Workeasy360 (seconds since the epoch), present whenever the session's authentication time is known (ADR-044) | After `prompt=login`, check it is no older than your request - a round trip on a still-open session proves nothing. Base "recently authenticated" on it, not on when your callback ran |

`iat`, `nbf` and `exp` come from **one** clock reading. Three calls to
`time.time()` can straddle a second boundary and produce `iat > nbf`, which
some strict verifiers reject.

### Ecosystem claims

| Claim | Why it is issued |
|---|---|
| `organization_id` | The ecosystem tenant identity. The product cannot derive it, and it is what every row it writes will be keyed on |
| `product_code` | The entitlement subject that was verified at issuance. Redundant with `aud` by construction — both are minted from the same registration row, so they cannot disagree — and a verifier checking both catches a misconfigured audience allowlist |
| `aal` | The assurance level the session reached. Named by [16 §8](../architecture/16-ecosystem-product-boundary.md) as one of the three claims a token must carry, and a product cannot derive it. Lets a product gate its own sensitive actions on step-up without inventing a second factor |

### Scope-gated claims

Present only if the integration is **registered** for the scope *and* asked
for it on this request:

| Scope | Claims |
|---|---|
| `profile` | `name` |
| `email` | `email`, `email_verified` |
| `organization` | `organization_name` |

A claim with an empty value is omitted rather than sent as `""` (OIDC Core
§5.3.2) — a product rendering `""` as a display name shows a blank where it
expected a person.

## 2. What is deliberately not issued

| Not issued | Why |
|---|---|
| `membership_id` | **Considered and rejected.** A product keys its data on `(sub, organization_id)` and has no use for the platform's join row. Issuing it would invite products to store and reason about a platform-internal identifier |
| Roles or permissions | Platform authorization is the platform's. A product owns its own domain permissions ([10 §6](10-product-certification-checklist.md)) |
| Plan, subscription, seats, price, currency, renewal date | A product asks "entitled?" and gets yes/no ([ADR-005](../architecture/adrs/ADR-005-product-entitlement-model.md)). It must never branch on how a capability was purchased |
| Organization name or slug, as an identity | Display strings. `organization_id` is the identity; a name is something a customer can change. The name is released only as `organization_name` behind the `organization` scope (added 2026-10-12), so a product provisioning a tenant on a first sign-in can label it. A product must not key on it or keep it in sync |
| Any other user PII | Behind scopes, or not at all |
| Anything about other organizations the user belongs to | The token concerns exactly one tenant |

`test_the_token_claim_set_is_exactly_what_is_documented` asserts
`membership_id` is absent, because the argument above is only worth writing
down if something checks it.

## 3. `sub` is `identity.User.id`, reused

An external identifier abstraction was evaluated and rejected as unnecessary.

`identity.User.id` is already a UUID, already stable for the life of the
account, and already meaningless to an observer — the three properties a
subject identifier needs. A second identifier would be one more thing to keep
in step, one more table to join, and one more place for the two to disagree,
for no property gained.

**Stability guarantee:** `sub` is stable for the life of the account and is
never reused. A product may use it as a primary key.

**Pairwise subjects** (`subject_types_supported: ["public"]`) were considered
and not implemented: every product in this ecosystem already knows the user's
organization, so per-product subject pseudonymisation would prevent no
correlation that matters while breaking the ability to answer "is this the
same person?" during support.

## 4. The access token

```json
{
  "iss": "…", "sub": "…", "aud": "<client_id>",
  "iat": …, "nbf": …, "exp": …, "jti": "…",
  "typ": "access_token",
  "scope": "openid email"
}
```

**No `organization_id`, deliberately.** It addresses a *user profile*
resource, which is global. Putting a tenant claim on it would suggest it
conveys tenant access, and a product reading the organization from the wrong
token is exactly the confusion `typ` exists to prevent.

## 5. Lifetimes, and the trade-off they encode

| Token | Lifetime | Why |
|---|---|---|
| Authorization code | **60 s** | A bearer credential in a URL, in browser history, possibly in a referrer header. RFC 6749 §4.1.2 permits ten minutes; there is no reason to take it |
| ID token | **300 s** | Exchanged **once** for the product's own session, not presented per request — so it can be short. This is the maximum revocation window |
| Access token | **900 s** | Addresses only `/userinfo` |
| Clock skew | **±30 s** | Well inside NTP drift, an order of magnitude under the ID token lifetime. A generous window silently extends the life of *every* expired token by that amount |

## 6. There are no refresh tokens

A refresh token is a long-lived credential whose entire purpose is to extend
access past the point where the platform last checked anything. That is the
opposite of a bounded revocation window, which is the property this whole
design rests on.

A product that needs a longer session **issues its own**, which it does
anyway — it has its own users, its own cookie and its own idle timeout. A
product that needs to know the user is still valid re-runs the flow (silent,
if the platform session is live) or asks the entitlement endpoint, which is a
**live read with no staleness at all**.

The cost is that a product cannot mint a new ID token without the user's
browser. That is the correct constraint: an ID token asserts that a person
authenticated, and a product should not be able to manufacture that assertion
on its own.

## 7. Revocation, stated precisely

Because verification is offline, revoking something does not reach in and
invalidate an issued token.

| Event | Effect on a **new** sign-in | Effect on an **unredeemed code** | Effect on an **issued ID token** | Effect on the entitlement endpoint |
|---|---|---|---|---|
| User deactivated | Refused immediately | Refused at exchange | Valid until `exp` (≤ 300 s) | n/a |
| Membership removed | Refused immediately | Refused at exchange | Valid until `exp` (≤ 300 s) | n/a |
| Organization suspended | Refused immediately | Refused at exchange | Valid until `exp` | `write_allowed: false` immediately |
| Entitlement revoked | Refused immediately | Refused at exchange | Valid until `exp` | `deny` immediately |
| Integration suspended | Refused immediately | Refused at exchange | Valid until `exp` | `integration_suspended` immediately |
| Credential revoked | n/a | Refused at exchange | n/a | Refused immediately |
| Signing key retired | n/a | n/a | **Invalid immediately** | n/a |

**The "unredeemed code" column is why the window is 300 seconds and not 360.**
A code lives for 60 seconds, and for most of this design's life it carried
the authorization decision *without re-asking it* — so a membership removed
during those 60 seconds still produced a fresh 300-second identity token, and
the real worst case was 390 seconds rather than the 330 stated here. The
token endpoint now re-runs the membership, organization-status and
entitlement checks before spending a code
(`authorization._revalidate_subject`), through the same two functions the
authorization endpoint uses. The failure is a uniform `invalid_grant`; the
product restarts the flow and the authorization endpoint tells it precisely
why. Asserted by four tests in `tests/security/test_ecosystem_security.py`,
including one that the *unchanged* case still exchanges, so the check cannot
pass by simply being broken.

**Maximum revocation window: 300 seconds**, plus 30 seconds of permitted
clock skew — 330 seconds worst case — *for the identity assertion only*.

**For continued access, the window is the product's revalidation cadence**,
advertised as `workeasy360_entitlement_revalidate_after` (default 900 s). A
product that honours it bounds stale access at 15 minutes without calling the
platform per request.

The emergency lever is retiring the signing key with `--force`, which
invalidates every unexpired token instantly and forces every user to sign in
again. It is the right response to a key compromise and the wrong one to
anything else ([08 §6](08-key-management.md)).

This is the security/availability trade-off in full: **instant revocation
would require a platform call on every product request**, which would make
every product's availability depend on the platform's and create exactly the
cascading failure the ecosystem boundary exists to prevent
([16 §10](../architecture/16-ecosystem-product-boundary.md)). Five minutes of
stale identity is the price, and it is deliberately paid.
