# 13 — Test Report

## Backend

**973 passed, 0 failed, 0 skipped** — from a database migrated fresh from
zero (`pytest --create-db`, Task 64), 472.76s.

Baseline carried in: 933 (Phase 3, re-confirmed at the start of this phase
before any Phase 4 change — see git history, commit `7e6065b`). **+40 new
tests** this phase:

| File | New tests | Covers |
|---|---|---|
| `tests/integration/test_authorization_resume.py` | 8 | Park/resume flow, Task 32 properties |
| `tests/integration/test_launcher_http.py` | 21 | Login, session, CSRF, organizations, app directory, launch, seat admin, organization settings — end to end over real HTTP |
| `workeasy360/platform/launcher/tests/test_access.py` | 9 | `can_launch_product`'s five states |
| `tests/performance/test_launcher_queries.py` | 2 | App-directory and seat-overview query budgets |
| Modifications to existing tests | — | Two OIDC tests updated for the new park-at-login behaviour; two query budgets raised with the measured, explained delta |

Also independently re-run, both with **Redis and RabbitMQ stopped**
(`docker stop`) — 231 passed, 0 failed (see
[10-resilience.md](10-resilience.md)).

**Quality gates**, all clean on the final state:

```
ruff check backend               → clean
ruff format --check backend      → clean
mypy (contracts/, the configured scope)  → Success: no issues found in 20 source files
bandit -r backend/workeasy360 -ll        → No issues identified
pip-audit -r requirements.txt --strict   → No known vulnerabilities found
lint-imports                             → 12 kept, 0 broken (includes the two new
                                            Phase 4 contracts: the P4 layer, and the
                                            widened commercial-boundary rule)
manage.py check --deploy (migration settings) → 6 warnings, unchanged from Phase 3
                                                  (that settings module is intentionally
                                                  permissive - see its own module docstring)
manage.py makemigrations --check --dry-run     → No changes detected
```

## Frontend

**11 passed, 0 failed** (Vitest) — `lib/api/client.ts` (7 tests: JSON
parsing, `ApiError` shape, CSRF header attachment, `cache: no-store`) and
`features/launcher/AppCard.tsx` (4 tests: state-driven rendering, no
`launch_url` ever exposed). See
[12-cleanup-review.md](12-cleanup-review.md) for two real bugs these tests
caught before they would have shipped.

```
npm run build       → compiles, 9 routes, clean (Turbopack)
npm run lint         → clean
npm run typecheck    → clean
npm test              → 11/11
npm audit             → 0 vulnerabilities
```

All four re-run from a clean install (`rm -rf node_modules .next && npm ci`)
— Task 64's frontend half.

## What this test suite does not cover

No end-to-end browser test (Playwright/Cypress) exercises the full user
journey click-by-click through a real browser. What exists instead:

- The backend suite exercises every HTTP contract the frontend calls,
  including the exact JSON shapes and redirect behaviours, over Django's
  real test client (not mocked).
- A manual, scripted `curl`-based walkthrough (login → session check →
  organizations → app directory → org settings) was run against the real
  Next.js dev server proxying to a real Django dev server, confirming the
  proxy, CSRF cookie issuance, and session cookie round-trip all work
  end-to-end outside the test harness (see
  [phase-4-status.md](phase-4-status.md) §5 for what that walkthrough
  covered and found).
- The frontend's own component tests are narrow (two files) rather than
  comprehensive - see [12-cleanup-review.md](12-cleanup-review.md) and
  [phase-4-status.md](phase-4-status.md) §12 for this stated as a genuine
  gap, not an oversight.

---

## Phase 4 adversarial audit (2026-09-22)

Findings and what each one cost are in
[14-opus-audit.md](14-opus-audit.md). What changed in the suite:

**Backend: 973 → 1,010** (`pytest --create-db`, fresh database, 0 failed,
0 skipped). **+37 tests**, and one removal of dead code:

| File | New | Covers |
|---|---|---|
| `tests/security/test_client_ip_and_throttling.py` | 20 | Trusted-proxy client-IP resolution (D-009) in every configuration including a 30-entry spoofed chain; login throttling per address and per account; the 429-is-not-an-oracle property; resume throttling |
| `tests/security/test_ecosystem_security.py` | 3 | **`product_no_seat` at `/authorize`; a seat released between authorize and redemption denies the exchange; an organization-wide product still authorizes with no seat** — Phase 4's headline property, previously untested at both boundaries |
| `tests/integration/test_cache_degradation.py` | 4 | Sign-in, the login throttle, entitlement resolution and an OIDC launch against a *genuinely unreachable* cache, not a LocMemCache standing in for one |
| `tests/integration/test_launcher_http.py` | 3 | A seat cannot be assigned to another organization's member or to an unknown id, uniformly; release stays a harmless no-op |
| `tests/integration/test_commercial_concurrency.py` | 1 | Concurrent assign and release from a full pool: no over-allocation, no lost release |
| `tests/architecture/test_architecture.py` | 3 | A22 (no response serialises `launch_url`), A23 (CSRF exemption is credential-authenticated only), A24 (no product-named file in the frontend) |
| `tests/performance/test_launcher_queries.py` | 1 (+1 budget lowered) | The app directory's **cost per product**, measured at two catalog sizes — pins the slope, not just the constant |
| `platform/organizations/selectors.py` | — | `active_member_ids` removed (no caller anywhere); `active_membership_exists` added in its place |

**Frontend: 11 → 29.**

| File | New | Covers |
|---|---|---|
| `src/proxy.test.ts` | 7 | The CSP as a pure function: nonce + `strict-dynamic` for scripts, no `unsafe-eval`/`unsafe-inline` in production, **no nonce in `style-src`** (which would block every inline style attribute), no wildcards; and that the root layout still forces dynamic rendering |
| `src/accessibility.test.tsx` | 11 | axe-core over the app card in all five states, the sign-in form, the seat table and both status screens (**0 violations**), plus two assertions for the things axe cannot see |

**New non-test gates:**

- `frontend/scripts/assert-dynamic-rendering.mjs` — reads the build's own
  `prerender-manifest.json` and fails on any prerendered route, because a
  nonce-based CSP and static prerendering are mutually exclusive and the
  source-level check alone would not notice a per-route override.
- A **`frontend` CI job** — `npm ci`, lint, typecheck, test, build,
  `npm audit`, prerender gate. There was none before; every frontend gate
  was a command a human remembered to run.

**Gate corrections:**

- `manage.py check --deploy --fail-level WARNING` could never pass in CI's
  environment, so it enforced nothing; CI now runs `check --fail-level
  WARNING` and the deployment checks are asserted against real production
  settings by the existing `test_production_*` tests.
- `make security` audited only `requirements.txt` while CI audited both
  files, so they disagreed. Now both audit both — which surfaced pytest
  8.3.4 / PYSEC-2026-1845, fixed by upgrading to 9.1.1.
- `npm run typecheck` is `next typegen && tsc --noEmit`; it could not run on
  a clean checkout before, because `LayoutProps` is generated by the build.

**One flake, found and fixed rather than rerun.** The first full-suite run
of the new tests failed `test_the_login_throttle_degrades_to_a_per_process_counter`
in the suite and passed it in isolation. The cause was real and in the test,
not the code: the limiter's window is `int(time.time()) // window_seconds`,
so four requests straddling a minute boundary see the counter reset half way
through. Under a full suite - and with a deliberately unreachable Redis
adding connection latency to every request - that is reachable. A
`frozen_throttle_window` fixture now pins the module's clock for every
throttling assertion. It is worth recording because the alternative, a
rerun, would have left a test that fails roughly once a fortnight in CI and
teaches everyone to ignore it.

**Re-measured, not carried over:** Lighthouse (performance 100,
accessibility 100, best practices 100 on the production build), the bundle
in gzip, the app directory's query growth at 5/20/50 products, and the
organization chooser's cost at 10/100/500 platform organizations. All in
[09-performance.md](09-performance.md) and
[05-organization-selection.md](05-organization-selection.md).
