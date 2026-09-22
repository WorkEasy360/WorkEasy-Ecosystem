# 01 — Frontend Architecture

## Stack and why

Next.js 16 (App Router, Turbopack default), React 19, TypeScript. Chosen
because it was the brief's suggested stack (Task 2) and because Turbopack-
default production builds are current as of this writing - see the version
mismatch note below.

**No UI component library, no icon library, no CSS framework.** Plain CSS
custom properties (`globals.css`) plus a handful of reusable classes
(`w360-btn`, `w360-card`, `w360-badge`, ...) and CSS Modules for
component-scoped layout. The launcher has a handful of screens; a design
system dependency would be weight bought for coverage this phase does not
need yet (Task 56).

**No client-side data-fetching or state-management library** (no SWR, React
Query, Redux, Zustand). Every page fetches its own data with `useEffect` +
`useState` through one fetch client (`lib/api/client.ts`). At this app's
scale - a handful of routes, no cross-page shared server-state cache wanted
- a library would duplicate what three lines of `useEffect` already do, and
would reintroduce exactly the caching Task 30 forbids for security-sensitive
state unless deliberately configured not to cache, which is more to get
wrong than simply not caching.

**No form library.** Two form fields (login) and a handful of buttons; native
`<form>`/`useState` is the entire requirement.

## Why data fetching is client-side, not Server Components

The backend owns the session as an HttpOnly cookie Django sets via its own
`CsrfViewMiddleware`/`ensure_csrf_cookie` machinery. A Next.js Server
Component fetching the Django API directly (bypassing the `next.config.ts`
rewrite, since a server-side fetch needs an absolute URL) would receive any
`Set-Cookie` the backend issues into its own outgoing `fetch()` call - which
is *not* forwarded to the browser. Only a request that travels through the
actual HTTP proxy path (browser → this app's server → Django, via
`rewrites()`) round-trips cookies correctly in both directions.

The pragmatic, correct architecture given that constraint: every
auth-sensitive read (`session`, `organizations`, the app directory, seat
data) is fetched **from the browser**, through the rewrite, so cookies flow
exactly the way they would for any other same-origin request. Server
Components render only the static shell (layout, CSS); everything that
needs to know who is signed in renders after hydration, with its own
loading/empty/error states (Task 26). This is a deliberate simplification
for this phase - see §4, "Decisions proposed to freeze for Phase 5" in
[phase-4-status.md](phase-4-status.md).

## Auth architecture

- **Optimistic check** (`src/proxy.ts`, Next.js 16's renamed Middleware):
  redirects to `/login` when no `w360_session` cookie is present at all -
  never reads the cookie's value, which is HttpOnly and opaque even to this
  process by design. This is explicitly documented by Next.js as
  insufficient alone ("Proxy ... should not be used as a full session
  management or authorization solution").
- **Secure check**: every actual page/data read goes through the Django
  backend, which is the only place membership, organization status,
  entitlement and seat are actually verified. A denied/expired session
  surfaces as a 401/403 the calling page handles (redirect to `/login`, or
  a "you don't have access" state - see `Shell.tsx`).

## Routes (Task 53)

```
/                              redirect logic (session → org count)
/login
/organizations/select
/o/[orgSlug]/apps              the launcher
/o/[orgSlug]/settings/profile
/o/[orgSlug]/settings/organization
/o/[orgSlug]/settings/members
/o/[orgSlug]/settings/products  seat assignment (Task 13/14)
```

The organization slug lives in the URL, not in a global client store - the
same property that makes Tasks 49/50 (multi-org isolation, cache isolation)
hold structurally: there is no cross-org cache to leak from, because there
is no cache. Switching organizations is a navigation to a different URL,
which naturally re-runs every `useEffect` fetch scoped to the new slug.

## A version note

This project scaffolded Next.js 16.3.5 (current stable at time of
scaffolding), which is newer than this model's training data and ships its
own bundled docs (`node_modules/next/dist/docs/`) specifically because of
that. Middleware was renamed to "Proxy" in 16.0; the file here is
`src/proxy.ts` accordingly, not `middleware.ts`.
