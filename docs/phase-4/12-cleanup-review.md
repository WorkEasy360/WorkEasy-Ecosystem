# 12 — Cleanup Review

## Frontend dependencies (Task 56)

| Added | Why | Alternative rejected |
|---|---|---|
| `next`, `react`, `react-dom` | The approved stack (Task 2) | — |
| `typescript`, `eslint`, `eslint-config-next` | Scaffold defaults, required | — |
| `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`, `@vitejs/plugin-react` | Task 45 test coverage (dev-only, no production bundle impact) | Jest - heavier setup for an App Router + Turbopack project; Vitest is the framework Next.js's own docs point to |

**Not added, deliberately**: a CSS framework, a UI component library, an
icon library, a form library, a data-fetching/state-management library. See
[01-frontend-architecture.md](01-frontend-architecture.md) for the reasoning
behind each.

`npm audit`: 0 vulnerabilities.

## Backend dependencies (Task 57)

**None added.** Every capability Phase 4 needed - session cookies, CSRF,
`ensure_csrf_cookie` - is Django's own, already a dependency. `pip-audit
--strict`: no known vulnerabilities (unchanged from Phase 3).

## Removed

- `frontend/src/app/page.module.css` (scaffold default, unused after the
  root page was rewritten).
- `frontend/public/{file,globe,next,vercel,window}.svg` (scaffold default
  assets; `grep -rl` across `src/` confirmed zero references before
  deletion).
- Two dead `next/font/google` imports removed at the same time the root
  layout was rewritten (`layout.tsx`) - see
  [01-frontend-architecture.md](01-frontend-architecture.md)'s note on
  avoiding a build-time external font fetch in favour of the system font
  stack already defined in `globals.css`.

## Verified, not removed

`frontend/src/app/favicon.ico` (scaffold default) - kept as a placeholder;
replacing it with a Workeasy360 mark is a design asset this phase did not
produce, not a cleanup decision.

## Found during this phase, fixed rather than left

Three real bugs surfaced by writing tests rather than by inspection alone -
recorded here because "the test caught it" is the point of Task 45, not an
incidental detail:

1. `SessionEpochMiddleware` force-logged-out every session established by
   Django's test client `force_login()` (no `w360_session_epoch` recorded)
   - would have broken the *entire pre-existing test suite* the moment it
   merged. Fixed by treating "no epoch recorded" as current rather than
   revoked (see the middleware's own docstring for why).
2. `platform.launcher.access` imported `IntegrationStatus` directly from
   `platform.ecosystem.models` - caught by the pre-existing architecture
   test `test_no_cross_module_orm_access`, not by review. Fixed by widening
   `LaunchTargetDTO` to carry semantic booleans instead of the raw enum
   (ADR-021 §4 records the decision this became).
3. Two Vitest tests were false negatives from test-harness bugs, not
   component bugs: missing `cleanup()` between tests (DOM from a previous
   `render()` was still present) and a mocked `Response` object being read
   twice (`.json()` throws on a second read of a consumed body). Both fixed
   in the test harness itself (`vitest.setup.ts`, `client.test.ts`), not by
   changing the code under test.

## Not run

`test-report.md`'s own count is the only place "unused code" was checked
comprehensively (via the full test suite passing with nothing skipped). No
dedicated dead-code/unused-export scanner (e.g. `ts-prune`, `knip`) was run
against the frontend - a reasonable next step once the codebase is larger
than the handful of files this phase produced.
