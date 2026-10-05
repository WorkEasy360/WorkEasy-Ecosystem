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
| `GET /orgs/<slug>/apps` (2 products) | 21 | New. Was 23 before the audit - see below |
| `GET /orgs/<slug>/apps/<code>/seats` | 23 | New |

### How the app directory scales, measured (Phase 4 audit)

Measured, not estimated, at three catalog sizes against real PostgreSQL:

| Marketable products | Queries before | Queries after |
|---|---|---|
| 5 | 29 | 24 |
| 20 | 59 | 39 |
| 50 | 119 | 69 |

The slope was **two queries per product** and is now **one**.

The one that went: `can_launch_product` re-read each product's catalog
descriptor through `ProductDirectory.get()` - the identical row
`app_directory` had just fetched for every product in a single
`list_marketable()` call. It now accepts the descriptor the caller already
holds. That changes no answer: `licensing_mode` is the only field the
decision branches on, it is intrinsic to the product, and every field that
can change underneath a stale answer - entitlement, seat, integration
status - is still read live on every call.

The one that remains is `ecosystem.selectors.launch_target()`: "is there a
registered integration for this code, and where does it point", asked once
per product. Entitlement is **not** per-product (the request-scope
middleware memoises the snapshot once per request whatever the catalog
size), and the seat check runs only for a product the organization actually
holds - so the shape the audit brief warns about, "per-product entitlement
query + per-product seat query", is not what this endpoint does.

A batched `launch_targets(codes)` would collapse the remaining slope to a
constant. Not built: a platform sells tens of products, and at 20 the whole
endpoint is 39 queries. What *is* built is
`test_the_app_directory_cost_per_product_does_not_grow`, which measures at
two catalog sizes and asserts the difference - so the assumption is
falsifiable rather than assumed, and the moment a third per-product read
appears the build says so.

## RS256 / entitlement-verify costs (Phase 3, unchanged, restated for context)

RS256 signing ~28ms p50 (once per product sign-in, not per request);
entitlement verification p50 ~7-8ms. Both dwarf the query-count deltas
above, which is the reasoning `authorization.py` itself gives for accepting
them.

## Frontend bundle

`npm run build` (Turbopack, production). All nine routes are
server-rendered on demand (`ƒ`) - none is prerendered, and that is a
security setting rather than a performance one
([08](08-csp-and-browser-security.md)).

Measured over `.next/static/chunks`:

| | Raw | Gzip |
|---|---|---|
| All JavaScript (19 chunks) | 605 KB | **190 KB** |
| Largest chunk (React/Next runtime, shared) | 224 KB | 70 KB |
| Second largest (framework, shared) | 156 KB | 43 KB |
| Legacy `noModule` chunk (modern browsers never fetch it) | 110 KB | 39 KB |
| CSS (3 files) | 7.4 KB | 2.4 KB |

No page-specific chunk approaches the framework ones, because no page
imports a UI, icon, date or state-management library - see
[01-frontend-architecture.md](01-frontend-architecture.md) for why none were
added. The only runtime dependencies are `next`, `react` and `react-dom`.

## Lighthouse (Phase 4 audit)

Lighthouse 12.8.2, Chrome headless, against `next start` on the production
build with the real Django API behind it, `/login`, **mobile form factor,
simulated throttling** (Lighthouse's default, which is harsher than a
desktop run):

| Category | Score |
|---|---|
| Performance | **100** |
| Accessibility | **100** |
| Best practices | **100** |
| SEO | 63 |

| Metric | |
|---|---|
| First Contentful Paint | 0.8 s |
| Largest Contentful Paint | 2.0 s |
| Total Blocking Time | 40 ms |
| Cumulative Layout Shift | 0 |
| Time to Interactive | 2.2 s |

SEO is 63 **by design**: `public/robots.txt` disallows everything, so
`is-crawlable` fails. Every page of this application is behind
authentication and every URL under `/o/` names a specific customer's
organization; there is nothing to index and a crawler following links
produces login redirects. The first run, before that file existed, scored
SEO 91 and failed `robots-txt` for having no valid file at all.

Two audits still fail, both accepted:

- **`bf-cache`** - the back/forward cache cannot restore the page, because
  every response carries `Cache-Control: no-store`. That is the direct
  consequence of rendering every route per request, which the nonce-based
  CSP requires. Security over a navigation optimisation.
- **`legacy-javascript` (13 KB)** - Turbopack's `noModule` chunk, which no
  modern browser fetches. A framework default, not this app's code.

Environment: single Windows 11 development machine, PostgreSQL 16 and
PgBouncer in Docker Desktop, `next start` and Django `runserver` on
loopback. Not a production-shaped deployment; the numbers are a floor for
"is anything obviously wrong", not a capacity claim.

## API caching (Task 30)

Deliberately none. Every `apiFetch` call sets `cache: "no-store"`
unconditionally (`lib/api/client.ts`); the app directory, seat overview and
session state are all treated as security-sensitive current state, not
slow-changing catalog metadata, even though part of what they return
(product names, descriptions) genuinely is slow-changing. Splitting the two
- caching the catalog half behind its own endpoint, leaving the access
decision uncached - is a real future optimisation, not built here because
nothing yet demonstrates the combined endpoint is a measured bottleneck.
