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
