# 09 — Performance

**Test environment stated, per Task 59:** a single Windows development
machine, PostgreSQL 16 and PgBouncer in Docker Desktop, Django's own
`runserver`/pytest-django test client - not a production-shaped deployment,
not a claim of internet-scale capacity (Task 59's own instruction).

## Backend query budgets, measured and pinned

`backend/tests/performance/test_ecosystem_queries.py`,
`test_launcher_queries.py` - each a `django_assert_max_num_queries` pin
against a real PostgreSQL connection, not an estimate.

| Endpoint | Budget | What changed this phase |
|---|---|---|
| `POST /integrations/oauth/authorize` | 25 | +5 vs. Phase 3's 20: `require_seat`'s catalog lookup, seat read, and tenant-scope round trips |
| `POST /integrations/oauth/token` | 30 | +4 vs. Phase 3's 26: `_revalidate_subject` re-running `require_seat` (+3), and `SessionEpochMiddleware` touching `request.user` on every request, including credential-only ones that never inspected it before (+1) |
| `GET /orgs/<slug>/apps` (2 products) | 23 | New. Scales with the number of products in the catalog - see the note below |
| `GET /orgs/<slug>/apps/<code>/seats` | 23 | New |

**Honest note on the app directory's scaling.** 23 queries for 2 products is
not O(1) in the catalog size: `platform.entitlements`'s request-scope
middleware memoises the *entitlement snapshot* per organization (one read
regardless of N), but `has_seat()` and `platform/ecosystem`'s
`launch_target()` are not currently batched across products, so each
product in the directory costs its own seat-check and integration-lookup
query. Acceptable at this catalog's realistic size (a platform sells tens of
products, not thousands); a genuine issue if the catalog grows an order of
magnitude, at which point the fix is a batched `has_seat_bulk` /
`launch_targets` read rather than a change to the access model itself. Not
built now because it would be optimising against a guess (Task 29: "do not
optimize by guess") rather than a measured need at today's catalog size.

## RS256 / entitlement-verify costs (Phase 3, unchanged, restated for context)

RS256 signing ~28ms p50 (once per product sign-in, not per request);
entitlement verification p50 ~7-8ms. Both dwarf the query-count deltas
above, which is the reasoning `authorization.py` itself gives for accepting
them.

## Frontend bundle

`npm run build` (Turbopack, production): all nine routes compile; five are
server-rendered on demand (`ƒ`), four are static (`○`). Total JS across all
chunks in `.next/static/chunks/`: **~660KB uncompressed** (not gzipped
in this measurement; Turbopack's production build output does not print a
Webpack-style "First Load JS" table the way `next build --webpack` does, so
this is a directory-size sum, not a per-route first-load figure). The two
largest chunks (~224KB, ~160KB) are the React/Next.js framework runtime,
shared across every route; no page-specific chunk in this app approaches
that size, since no page imports a UI/icon/state-management library (Task
56 - see [01-frontend-architecture.md](01-frontend-architecture.md) for
why none were added).

**Not measured**: gzipped/brotli transfer size, Lighthouse score, Core Web
Vitals from a real browser session, render-waterfall profiling. Recorded as
a genuine gap rather than a claimed pass - see
[phase-4-status.md](phase-4-status.md) for how this is carried forward.

## API caching (Task 30)

Deliberately none. Every `apiFetch` call sets `cache: "no-store"`
unconditionally (`lib/api/client.ts`); the app directory, seat overview and
session state are all treated as security-sensitive current state, not
slow-changing catalog metadata, even though part of what they return
(product names, descriptions) genuinely is slow-changing. Splitting the two
- caching the catalog half behind its own endpoint, leaving the access
decision uncached - is a real future optimisation, not built here because
nothing yet demonstrates the combined endpoint is a measured bottleneck.
