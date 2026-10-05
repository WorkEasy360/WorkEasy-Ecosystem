# 10 — Resilience

## Redis and RabbitMQ unavailable — actually tested, not just reasoned about

`docker stop` against this environment's `workeasy360-redis-1` and
`workeasy360-rabbitmq-1` containers, then the full launcher, login/session,
authorization-resume and OIDC test suites re-run against the *same* live
PostgreSQL with both dependencies down:

```
backend/tests/integration/test_launcher_http.py
backend/workeasy360/platform/launcher/
backend/workeasy360/platform/ecosystem/
backend/tests/integration/test_authorization_resume.py

→ 231 passed, 0 failed, 0 skipped, Redis and RabbitMQ both stopped
```

This is a genuine result, not an architectural claim taken on faith: login,
session establishment, the app directory, launch (including seat
re-checking), seat administration, and the full OIDC authorize/token/resume
flow all continue to function correctly with both dependencies down.
Containers restarted immediately after.

**Why this holds.** `platform.entitlements.services.read._resolve_from_shared_cache`
already wraps every Redis read/write in `try/except`, falling back to
PostgreSQL (Phase 1/3 property, exercised here rather than newly built).
Celery/RabbitMQ is not on any path this phase's new endpoints touch at all -
`grep` for `apply_async`/`delay` across `platform/launcher` and
`platform/identity` returns nothing.

### What that run does *not* prove (Phase 4 audit)

Stopping the container is weaker evidence than it looks: the test settings
use a `LocMemCache`, so nothing those 231 tests touch was ever talking to
Redis. They prove the suite does not *depend* on Redis being up; they do not
exercise a single cache call failing.

`backend/tests/integration/test_cache_degradation.py` closes that, by
pointing `CACHES` at a Redis port nothing listens on - so every
`cache.get`/`add`/`incr` raises a real connection error, on the request
path, before authentication. Four properties, and the line between them is
the Tier 1 contract ("degrades latency, never correctness"):

| | With the cache unreachable |
|---|---|
| Sign in | **works** - sessions are in PostgreSQL, and the login throttle touches the cache before any credential is examined |
| Login throttle | **still bounds the endpoint**, from a per-process counter. With N workers the effective limit is N x the configured one - capacity degradation, stated as debt D-012, never a hole |
| Entitlement resolution | **`active`, from PostgreSQL** - the failure this test exists for is a cache miss-by-exception resolving to an empty snapshot, which would report every organization unentitled for the duration of the outage and show a paying customer an empty app directory |
| OIDC authorize → code | **completes** - the limiter in front of it does not fail closed either |

Deliberately not a mock. The code catches broad exceptions from the cache
client, and a mock would let the test pass while a real client raised
something the `except` clause did not cover.

## A single product's outage cannot break the platform (Task 25)

Not independently re-tested this phase - Phase 3 already asserts readiness
with an empty integration registry, unconditionally
(`docs/phase-3/14-failure-mode-review.md`). What Phase 4 adds is
*presentation* of a down product: `AppAccessState.INTEGRATION_SUSPENDED`
(reversible) and `PRODUCT_UNAVAILABLE` (no integration at all) are both
ordinary states the launcher renders without special-casing - there is no
code path in `platform/launcher` or the frontend that can raise merely
because one product's integration is absent, suspended, or retired
(`test_a_product_with_no_registered_integration_is_unavailable`,
`test_a_suspended_integration_is_reported_distinctly_from_no_entitlement`).
An organization's other, healthy products are unaffected because each is an
independent `can_launch_product()` call - see
[03-launcher-access-model.md](03-launcher-access-model.md).

## Frontend error isolation (Tasks 25/26)

`src/app/o/[orgSlug]/error.tsx` sits *inside* the shell layout - a rendering
failure in one settings page or the launcher grid does not unmount the
header, navigation or organization switcher around it. Not independently
load-tested (no synthetic component-throw test exists yet); the boundary's
placement is verified by inspection and by Next.js's own documented
error-boundary semantics, not by a fault-injection test. Recorded as a gap
rather than claimed as fully proven.

## What was not tested

PostgreSQL itself unavailable (the one dependency every code path in this
system genuinely requires - not a resilience target, a hard dependency by
design). A live load-balanced multi-instance deployment. Network partition
between the Next.js process and the Django backend specifically (the
`rewrites()` proxy's own failure mode - an unhandled `fetch` rejection in
`next.config.ts`'s rewrite path - was not exercised).
