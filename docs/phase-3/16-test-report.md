# 16 — Test Report

## Headline

```
933 passed, 0 failed, 0 skipped        in 292s
```

912 at phase close; the adversarial audit in
[17-audit-findings.md](17-audit-findings.md) added **21**, one per finding
it fixed — each written to fail against the code as it stood.

From a **database created and migrated from zero** — `docker compose down -v`,
fresh volume, `migrate` from empty, full suite. No skips were used to
manufacture a pass; the suite has no conditional skips at all.

| | Phase 3R baseline | Phase 3 | Δ |
|---|---|---|---|
| Tests | 654 | **933** | **+279** |
| Failed | 0 | 0 | — |
| Skipped | 0 | 0 | — |
| Duration | 154 s | 292 s | +138 s |
| Import contracts | 11 / 11 | **12 / 12** | +1 |

## Where the new tests are

| Suite | Tests | Covers |
|---|---|---|
| `platform/ecosystem/tests/` | 183 | Manifest, registry, keys, OIDC flow, client auth, entitlement API, rate limits |
| `tests/security/test_ecosystem_security.py` | 30 | Token tampering, enumeration, cross-product, CORS, skew, log hygiene |
| `tests/integration/test_reference_product_client.py` | 15 | **The acceptance proof** — a simulated independent product |
| `tests/integration/test_ecosystem_concurrency.py` | 8 | Races, against real PostgreSQL |
| `tests/performance/test_ecosystem_queries.py` | 12 | Query budgets and measured latency |
| `tests/architecture/` | +8 (32 total) | The new fitness guards |
| Elsewhere | +2 | Production issuer assertions, secret-scanner canary |

Module breakdown of the 183:

| File | Tests |
|---|---|
| `test_oidc_flow.py` | 36 |
| `test_registry.py` | 37 |
| `test_clientauth.py` | 23 |
| `test_manifest.py` | 33 |
| `test_keys.py` | 31 |
| `test_entitlement_api.py` | 19 |
| `test_ratelimit.py` | 11 |

## The acceptance proof

`test_an_independent_product_integrates_end_to_end` walks the phase's thirteen
stated criteria in order, with numbered comments matching them. Its client
imports **nothing** from `workeasy360` —
`test_the_reference_client_imports_no_platform_module` reads the file's AST to
assert it, including imports inside function bodies.

The negative half, each its own test:

| Must fail | Test |
|---|---|
| Product Y using product X's token | `test_product_y_cannot_use_product_x_token` |
| Organization B's context reused for A | `test_organization_b_context_cannot_be_reused_for_organization_a` |
| Authorizing into an organization you are not in | `test_a_user_cannot_authorize_into_an_organization_they_do_not_belong_to` |
| Expired entitlement | `test_an_expired_entitlement_cannot_receive_access` |
| Suspended integration | `test_a_suspended_integration_cannot_authenticate` |
| Replayed authorization code | `test_a_replayed_authorization_code_fails` |
| A code exchanged by another product | `test_a_code_minted_for_one_product_cannot_be_exchanged_by_another` |
| Cross-product entitlement probing | `test_a_product_client_cannot_probe_another_products_entitlement` |
| State mismatch | `test_the_product_refuses_a_callback_whose_state_does_not_match` |
| Nonce mismatch | `test_the_product_refuses_a_token_whose_nonce_does_not_match` |

Plus `test_the_product_survives_a_platform_key_rotation_without_redeploying`,
which is the operational claim in [08](08-key-management.md) executed rather
than asserted.

## Coverage by requirement

| Requirement | Tests |
|---|---|
| Exact redirect matching | 7 unit variants + 7 end-to-end + registration-time refusals |
| PKCE | Required · S256 only · wrong / missing / short verifier |
| Code single use | Sequential + **8-thread concurrent** |
| Audience isolation | Platform verifier + product verifier + userinfo |
| Token tampering | **10 parametrised forgeries** |
| Clock skew | 4 boundary cases against genuinely signed tokens |
| Key rotation | Pre/post-rotation validity, publish-before-sign, retire, unknown kid, missing secret, concurrent promotion |
| Client auth | **8 parametrised failure modes**, all asserted identical |
| Entitlement statuses | 6 parametrised, each mapped to a documented answer |
| Rate limits | 4 buckets + degradation + **isolation from other traffic** |
| Enumeration | Organization, client, and grant-failure oracles |
| Lifecycle | **10 parametrised transitions**, legal and illegal |
| Manifest | 33, each a named attack or operational failure |

## What is *not* covered, stated plainly

| Gap | Why | Risk |
|---|---|---|
| **Real network transport** | Django's test client, no TLS, no proxy | Low. TLS is infrastructure; the platform never inspects it |
| **A real product in another language** | The reference client is Python | Low–medium. Mitigated by using only standard OIDC and by the test vectors in [10](10-product-certification-checklist.md) |
| **Redis-backed rate limiting** | The suite uses locmem; the atomicity of `cache.incr` is Redis's | Low. The degradation path *is* tested |
| **Production-scale load** | Measured on a workstation | Medium — see [13](13-performance-review.md). Budgets are the regression guard |
| **Seat enforcement at sign-in** | Deliberately not implemented | Documented, [06 §7](06-entitlement-api.md) |
| **The browser UI** | Not built in this phase | Documented, [03 §9](03-sso-architecture.md) |
| **A multi-worker limiter fallback** | Single process under test | Low. The approximation is documented |

## Quality gates

| Gate | Result |
|---|---|
| `pytest` (fresh database) | **933 passed, 0 failed, 0 skipped** |
| `ruff check` | Clean |
| `ruff format --check` | 240 files formatted |
| `mypy` (strict, `contracts/`) | No issues, 20 files |
| `bandit -ll` | No issues, 26,984 lines |
| `pip-audit --strict` | No known vulnerabilities |
| `lint-imports` | **12 / 12 kept, 0 broken** |
| `manage.py check` | No issues |
| `manage.py check --deploy` | 6 warnings, all pre-existing properties of the migration settings module |
| `manage.py makemigrations --check` | No changes detected |

## Three tests that earned their place

**`test_the_limiter_degrades_rather_than_failing_closed`** — proves a Redis
outage does not stop every product authenticating. The tempting implementation
fails closed and would have looked more secure.

**`test_a_platform_failure_is_a_503_and_never_a_denial`** — asserts the
response does not contain the string `deny`. The behaviour it protects is the
difference between a platform incident and an ecosystem-wide customer lockout.

**`test_unrelated_platform_traffic_is_not_throttled`** — exhausts every
ecosystem rate-limit bucket and then asserts an ordinary tenant endpoint still
answers. Without it, "targeted rate limiting" is an intention rather than a
property.

## Four bugs the tests found

Each was a real defect in code written for this phase, caught before it
shipped.

1. **`entitlements.check()` requires an ambient tenant context** that neither
   OIDC endpoint has. Found by a registry test. Fixed properly — a shared
   `decide()` and a service-path selector — rather than by patching the test.
2. **Redirectable errors were being rendered**, contrary to RFC 6749
   §4.1.2.1. Found by `test_a_suspended_integration_cannot_authenticate`. Led
   to the `resolve_client` / `parse_request` split and the removal of a
   misleading predicate.
3. **A suspended integration could still call the service endpoints.** Found
   by `test_a_suspended_integration_cannot_verify_entitlement`. Fixed at the
   single client-authentication chokepoint, which covered the token endpoint
   too.
4. **`RateLimited` escaped the authorize view** as a 500 instead of a 429,
   because the limiter call sat outside both try blocks.
