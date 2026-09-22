# 13 — Performance Review

## Method, and what these numbers are worth

Measured by `backend/tests/performance/test_ecosystem_queries.py` on the local
development stack:

| | |
|---|---|
| Hardware | Developer workstation, Windows 11, Docker Desktop |
| Database | PostgreSQL 16 in Docker, **through PgBouncer in transaction mode** |
| Django | Test settings, `DEBUG=False`, locmem cache |
| Transport | Django test client (no network, no TLS) |

**These are not production numbers**, and the direction of the error is not
uniform: no network or TLS makes them optimistic, while Docker Desktop's
filesystem and a cold cache on the first iteration make them pessimistic.
They are useful as a **baseline to regress against**, which is why the query
budgets — which are deterministic and machine-independent — are the asserted
part and the timings are reported.

---

## Entitlement verification

The hottest endpoint in the ecosystem: every product calls it for every
organization it serves, on the documented revalidation cadence.

Full request path — client authentication, rate limit, tenant scope,
entitlement read, response — over 40 calls:

| | |
|---|---|
| p50 | **7.5 ms** |
| p95 | **8.3 ms** |
| p99 | **8.6 ms** |
| min / max | 6.8 ms / 8.6 ms |

The spread is remarkably tight (max is 1.15× p50), which says the path has no
conditional expensive branch — no cache-miss cliff, no lazy initialisation.

**Comparison:** `ENTITLEMENT_P50_BUDGET_MS = 1.0` governs the *in-process*
check, which is a dictionary lookup after the first call in a request. This
endpoint adds RSA-free client authentication (a SHA-256 or one RSA verify),
two tenant-scope round trips through PgBouncer, and a snapshot read. 7.5 ms
for that, through a pooler, is proportionate.

**Asserted ceiling: p95 < 500 ms** — loose enough never to be flaky, tight
enough to catch a regression that turned a bounded read into an unbounded one.

## Query budgets

Deterministic, asserted, and the actual regression protection.

| Path | Budget | Nature |
|---|---|---|
| `/.well-known/openid-configuration` | **0** (exact) | Settings and constants only |
| `/.well-known/jwks.json` | **1** (exact) | One indexed read, **flat in the number of keys** |
| Entitlement verification | ≤ 12 | Flat in the number of registered integrations |
| Authorization endpoint | ≤ 20 | Session, integration, organization, membership, entitlement, code insert, plus tenant-scope round trips |
| Token endpoint | ≤ 26 | Credential, use stamp, code+integration (one query), **subject re-validation**, spend, signing key, profile |
| Integration listing | ≤ 6 | Flat in the number of integrations |

### The two N+1 guards that matter

**JWKS does not grow a query per key.** A naive implementation reads the key
list then fetches each key. Four published keys cost exactly what one costs —
`test_the_jwks_does_not_grow_a_query_per_key`.

**Code redemption joins its integration.** Without `select_related`, redemption
reads the code, then reads the integration to compare client ids, then reads it
again for the product code on the token: three queries where one will do, on
the endpoint a user is waiting for.
`test_the_code_lookup_joins_its_integration_rather_than_refetching` captures
the SQL and asserts exactly one SELECT **against the integration table** -
counted that way rather than as one SELECT in total, so that the re-validation
reads below cannot mask a lost join and a change to either does not fail the
other's test.

### Why the token endpoint costs more than it used to

Most of its 26 is subject re-validation: the organization, the membership
inside that organization's database scope, the entitlement snapshot, and the
tenant-scope round trips around both. It was bought deliberately - without it
a membership revoked during the sixty seconds a code lives still produced a
fresh five-minute identity token, and the documented revocation window was
wrong by the code lifetime
([14 section 10](14-failure-mode-review.md)).

It is affordable **because of where it sits**. This runs once per product
sign-in, next to an RS256 signature that costs ~28 ms on its own; eleven
indexed reads on a local connection are not what the user waits for. The same
cost on a per-request path would be indefensible, which is exactly why
verification is offline and this endpoint is reached once.

**Discovery costs zero queries.** The cheapest and most-fetched endpoint in
the ecosystem does not depend on PostgreSQL being up.

## Token size

Measured at the widest point the platform can produce: every scope granted, so
`email`, `email_verified` and `name` are all present, plus the longest `nonce`
the authorization endpoint accepts (255 characters).

| Token | Bytes | Headroom |
|---|---|---|
| ID token (RS256) | **1 269** | ~2.8 KB under a 4 KB browser cookie; ~6.7 KB under a typical 8 KB header limit |
| Access token (RS256) | **765** | — |

Asserted at 2 048 bytes by `test_report_token_size_and_bound_it`, chosen
against the constraint rather than against the current number so the test says
what it is protecting. ES256 is smaller still — a 64-byte signature against
RS256's 256.

The reason to bound it at all is that the failure is not gradual: an oversized
header is refused outright by most ingress defaults, and an oversized cookie is
dropped **silently** by the browser. A product storing the ID token in a
session cookie would start failing for the subset of users whose name pushed it
over the line, with nothing in any log to explain it. That is the cost of every
future claim, and it is the argument against adding one speculatively.

## Token signing

| Algorithm | p50 | max |
|---|---|---|
| RS256 (default) | **27.66 ms** | 30.08 ms |
| ES256 | **1.28 ms** | 5.56 ms |

RSA-2048 signing is ~20× the cost of P-256, which is expected and is the one
number here worth acting on.

**It is not on a hot path.** Signing happens once per authentication — once
per user per product per session — not per request. A product exchanging a
code once and running its own session for eight hours pays 27 ms once.

**When to switch to ES256:** a deployment doing sustained high-volume
authentication. The code path is identical, it is set per key, and
`test_both_supported_algorithms_sign_and_verify` covers both. RS256 remains
the default because OpenID Connect Core requires every implementation to
support it, so it is the one algorithm an independently written product is
guaranteed to verify — compatibility beats 26 ms of once-per-session cost.

**Verification** is the reverse: RSA verification is fast and ECDSA
verification is slower. Products verify once per session too, so neither
matters much; RS256 is marginally better for the verifier, which is the side
this platform does not control.

## Indexes added

Four, each serving a named query. None speculative.

| Index | Serves |
|---|---|
| `ix_integration_client` (`client_id`, `status`) | The authorization endpoint's lookup: "who is this, and may it authenticate?" in one probe |
| `ix_credential_integration` (`integration`, `status`) | Listing a product's credentials |
| `uq_credential_integration_ref` (`integration`, `kind`, `credential_id`) | **Client authentication** — one indexed row rather than a hash per stored credential |
| `ix_signing_key_status` (`status`, `-created_at`) | Key selection and the JWKS |
| `ix_authcode_expires` (`expires_at`) | The pruning sweep, which would otherwise scan everything ever issued |

Plus the unique constraints on `product_code`, `client_id`, `kid` and
`code_hash`, which are correctness controls that happen to be indexes —
`code_hash` in particular is the redemption lookup.

## No caching was added

Deliberately. The entitlement path already has three cache layers behind it
(`entitlements/services/read.py`), and adding a fourth at this boundary would
mean a second staleness window nobody reasons about on a path whose whole
contract is "this is a live read".

The measured p50 does not justify it. Should it ever, the number to move is
`revalidate_after` — telling products to ask less often — before introducing
a cache with its own invalidation problem.

## Unbounded growth, and the one place it existed

`ecosystem_authorization_code` gains a row per product sign-in and nothing
deleted them. Left alone, the table grows for the life of the deployment and
the redemption lookup — which a user waits for — gets slower with every login
that ever happened.

Fixed by `platform.ecosystem.prune_authorization_codes`, hourly on the `heavy`
queue. It keeps expired codes for one hour so a replay attempt still *finds*
the row and is logged as a replay rather than as an unknown code.

The other three tables are bounded by the number of products in the ecosystem
and the number of key rotations, both of which are small by construction and,
unlike the codes table, small for a reason that will not change.

## No synchronous outbound calls

The platform makes none. No product health polling, no webhook delivery, no
icon fetching — so no product can make a platform request slow, which is the
performance half of the failure-isolation rule
([14](14-failure-mode-review.md)).
