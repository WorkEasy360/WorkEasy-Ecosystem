# 14 — Failure Mode Review

Extends the degradation matrix in
[09-failure-resilience.md §5](../architecture/09-failure-resilience.md) to the
remote integration boundary.

The governing rule, from
[16 §10](../architecture/16-ecosystem-product-boundary.md): **one product
being down must never degrade the platform or another product**, and the
platform being down must not immediately take every product with it.

## The matrix

| Down | Discovery / JWKS | New sign-in | Token exchange | Entitlement verify | Existing product session | Platform itself |
|---|---|---|---|---|---|---|
| **A product** | ok | ok | ok | ok | that product only | **ok** |
| **PostgreSQL** | discovery ok, JWKS fails | fails closed | 503 | **503, never `deny`** | **continues** | down anyway |
| **Redis** | ok | ok | ok | ok | continues | ok (slower) |
| **RabbitMQ** | ok | **ok** | **ok** | **ok** | continues | ok |
| **Secret manager** | ok | ok | **503** | ok | **continues** | ok |
| **No active signing key** | ok | ok | **503** | ok | continues | **ready** |
| **The whole platform** | — | fails closed | — | product honours last answer | **continues to token expiry / revalidation window** | — |

---

## 1. A product is down

Nothing happens to the platform. Structurally, not by policy: the platform
holds no reference to any product, makes no outbound call to one, and never
fetches a registered URL.

The ecosystem health probe is registered in the **`deep`** group only. Two
tests hold this:

- `test_product_health_never_affects_platform_readiness` asserts against the
  live probe registry that no ecosystem probe is in `startup` or
  `registries` — and that one *is* in `deep`, so the check is not vacuous.
- `test_readiness_does_not_touch_the_integration_registry` asserts that a
  deployment with **no signing key and no integrations at all** — the state a
  fresh install is in — is still `ready`.

## 2. PostgreSQL is down

The platform is down anyway, so the interesting question is what products do.

**Existing product sessions continue.** They verify signatures offline
against a cached key set and hold their own session cookie. This is the
central anti-cascading-failure property and the reason offline verification
was chosen.

**The entitlement endpoint returns 503, never `deny`.** This is the single
most consequential behaviour in the phase: a product receiving `deny` tells a
paying customer they have no licence and may tear down their session; a
product receiving 503 retries or honours its last known answer. Collapsing an
outage into a denial converts a platform incident into an ecosystem-wide
false lockout.

`test_a_platform_failure_is_a_503_and_never_a_denial` asserts the response
does not contain the string `deny` at all.

**New sign-in fails closed.** Correct: the platform cannot verify membership
or entitlement, and a sign-in that skipped either would be granting access it
has not checked.

**Discovery still serves** — it costs zero queries, so the cheapest and
most-fetched endpoint in the ecosystem does not depend on the database.

## 3. Redis is down

Redis is Tier 1: "degrades latency, never correctness".

| Affected | Behaviour |
|---|---|
| Rate limiting | Degrades to a **per-process** window. A deployment with N workers allows up to N× the limit. Stated rather than hidden |
| Assertion replay protection | Lost for the assertion's maximum age (≤ 300 s) |
| Entitlement snapshot cache | Falls through to PostgreSQL. Slower, correct — unchanged from Phase 1 |

**Rate limiting deliberately fails open-ish.** Failing closed would mean a
Redis blip stops every product authenticating anyone — a cache outage becoming
an ecosystem outage. This is the one place where degrading *capacity* to
preserve *availability* is right, and it matches
[09 §4.6](../architecture/09-failure-resilience.md)'s "fall back to an
in-process per-worker limiter, biased conservative".

`test_the_limiter_degrades_rather_than_failing_closed` proves the fallback
still enforces, per process.

**Assertion replay protection is a layer, not the defence.** The assertion is
still signature-verified, audience-bound and expiry-bound. What is lost is one
layer of a defence in depth ([11 T-8](11-security-threat-model.md)).

## 4. RabbitMQ is down

**Nothing on this boundary is affected.** Authentication, token issuance and
entitlement verification publish no events and enqueue no tasks. The only
Celery task in the module is the hourly code prune, which is housekeeping —
if it runs late, codes are deleted late.

This was a design requirement ("authentication should not depend
unnecessarily on RabbitMQ") and it is satisfied by the absence of a
dependency, not by a fallback.

## 5. The secret manager is unreachable

Token *signing* fails; everything else works.

`SigningKeyUnavailable` is a distinct exception type for exactly this reason:
the correct response is **503, not 400**. Nothing the caller sent is wrong,
and retrying after the operator restores the secret will work. Collapsing it
into a generic error produces an outage that looks like a client bug.

`EnvSigningKeyProvider` reads the environment **live** rather than at
construction, so restoring the secret does not require a restart.

Existing sessions continue — signing is only needed to mint *new* tokens.

## 6. No active signing key

The state a fresh environment is in before bootstrap, and the state a botched
rotation could leave.

Token issuance fails with 503. **Everything else works, and the instance stays
`ready`** — a missing signing key must not de-pool an instance whose login,
billing and organization management are perfectly healthy.

`/healthz/deep` reports `NO ACTIVE SIGNING KEY` so an operator can see it.

`active_signing_key()` **raises rather than falling back** to "the newest
key". A fallback would quietly sign with a `retiring` key during a botched
rotation, producing tokens that leave the key set before they expire from
their own `exp` — a failure that would surface days later as unexplained
verification errors.

## 7. The platform is unreachable from a product

The contract a product can rely on:

| | |
|---|---|
| **New sign-in** | Fails. Correct — nothing can be verified |
| **Existing session** | Continues, bounded by the product's own session policy |
| **Identity staleness** | ≤ 330 s (token lifetime + skew). An unredeemed authorization code does **not** add its own 60 s: the token endpoint re-validates the subject before spending one |
| **Access staleness** | ≤ `revalidate_after` (900 s default) |
| **Entitlement calls** | 503; the product honours its last known answer and retries |

**There is no indefinite stale access.** A product that follows
[10 D1–D4](10-product-certification-checklist.md) cannot serve a revoked
organization for longer than its revalidation window, because the answer
expires even when the platform cannot be reached to refresh it.

**The emergency lever is the platform's**: retiring the signing key with
`--force` invalidates every unexpired token within one JWKS cache period
([08 §6](08-key-management.md)).

## 8. Cascading-failure analysis

| Chain | Present? |
|---|---|
| Product → platform (synchronous, per request) | **No.** Offline verification; entitlement on a cadence, not per request |
| Platform → product | **No.** No outbound call of any kind exists |
| Product A → product B | **No.** No shared state, no shared credential, no shared database |
| Auth → RabbitMQ | **No.** No events, no tasks on the path |
| Auth → Redis | **Soft.** Degraded rate limiting and replay protection; the path works |
| Auth → secret manager | **Yes, for signing only.** Bounded to token issuance; verified sessions unaffected |
| Auth → PostgreSQL | **Yes.** Tier 0, and the platform is down regardless |

The two hard dependencies are PostgreSQL and the secret manager. Both are Tier
0 infrastructure, both fail as 503 rather than as a denial, and neither
affects a product session that has already started.

## 9. Failure responses, in one table

| Condition | Status | Code | Never |
|---|---|---|---|
| Entitlement undeterminable | 503 | `temporarily_unavailable` | **`deny`** |
| Signing key unresolvable | 503 | `server_error` | 400 |
| Rate limited | 429 + `Retry-After` | `temporarily_unavailable` | Silence |
| Client auth failed | 401 | `invalid_client` | Any detail about which part |
| Grant invalid | 400 | `invalid_grant` | Which of the five checks failed |
| Not entitled | 302 | `product_not_entitled` | Plan, price or subscription state |

The first row is the one to remember: **an operational failure is never
reported as a denial**, because the two demand opposite responses from the
product.

## 10. A code is redeemed after the world changed underneath it

Sixty seconds separate the browser redirect from the token exchange, and
everything the authorization endpoint checked can change inside them. The
code binds *which* subject it speaks for — user, organization, membership —
and that binding is what stops a code issued for one tenant being completed
against another. It says nothing about whether that subject is still allowed
in.

The token endpoint therefore re-runs three checks before it will spend a
code, through the same two functions the authorization endpoint uses
(`authorization._revalidate_subject`):

| Re-checked at exchange | Why it cannot wait for the next sign-in |
|---|---|
| Active membership | Offboarding. An operator who removed someone expects that to be effective, not effective in six minutes |
| Organization permits login | Lock and deletion are the strongest states the platform has |
| Entitlement | A cancellation mid-flow would otherwise still hand out a 300 s identity token |

Three properties of how it fails:

- **Uniform.** Every one becomes `invalid_grant`, like every other redemption
  failure. Naming the cause would tell whoever holds the code which part of
  their attempt was wrong. The product restarts the flow and the
  authorization endpoint answers precisely, redirectably.
- **Before the spend, not after.** If the entitlement read is unavailable the
  exception propagates, the endpoint answers 503, and the code is still live
  for the retry. Spending first would burn a legitimate user's authorization
  on a database blip.
- **Never a denial on failure.** The same rule as §9 row 1: an unreachable
  store is a 503, not an `invalid_grant`.

The cost is roughly eleven extra queries on an endpoint that already signs an
RS256 token for ~28 ms. It runs once per product sign-in, not per request.

## 11. The load balancer rewrites nothing (deployment requirement)

Two rate-limit buckets are keyed partly on the caller's address, read from
`REMOTE_ADDR`:

| Bucket | Key | Behind a proxy that does not rewrite `REMOTE_ADDR` |
|---|---|---|
| `authorize` | `client_id:ip` | Collapses to one bucket per product — 60 sign-ins/minute for that product across the whole ecosystem |
| `client_auth_failure` | `client_id`, or the IP when no client id was presented | The IP fallback collapses to one shared bucket for anonymous failures |

Neither is cross-product: a saturated `authorize` bucket for CRM does not
touch Books, and a legitimate client that authenticates successfully never
touches `client_auth_failure` at all. But within one product the first row is
reachable by anyone who knows a client id and one registered redirect URI,
both of which are public by construction, and the effect is a sign-in outage
for that product's customers.

`REMOTE_ADDR` is used deliberately rather than `X-Forwarded-For`: a
forwarded header is client-supplied, and trusting it without knowing how many
proxies sit in front would let any caller spoof an address and evade the
limit entirely — a worse failure than the one above. The correct fix is
topology-dependent and belongs with the deployment:

**Either** terminate at a proxy that sets `REMOTE_ADDR` to the real client
(PROXY protocol, or an ingress that rewrites it), **or** supply the number of
trusted proxies so the platform can count that many entries from the right of
`X-Forwarded-For`. Until one of those exists, treat the `authorize` limit as
a per-product ceiling and size it accordingly.

Carried as R-8 in [11](11-security-threat-model.md) and in the debt register.
