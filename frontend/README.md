# Workeasy360 — Platform Frontend

The unified shell: login, organization chooser, app launcher, and platform
settings (profile, organization, members, product access/seats). Product
domain UI (CRM, Books, ...) does not and will not live here — see
`docs/architecture/adrs/ADR-017-products-are-separate-applications.md`.

## Stack

Next.js 16 (App Router, Turbopack), React 19, TypeScript. No UI framework,
no client-side state-management or data-fetching library, no icon library —
see `docs/phase-4/01-frontend-architecture.md` for why each was left out.

## Local development

1. Start the backend (see `../README.md`) so it is reachable at
   `http://localhost:8000` (or set `BACKEND_ORIGIN` below to wherever it
   actually is).
2. `cp .env.local.example .env.local` and adjust `BACKEND_ORIGIN` if needed.
3. `npm install`
4. `npm run dev`

The app proxies `/api/*` to `BACKEND_ORIGIN` (see `next.config.ts`) so the
browser only ever talks to one origin — the session and CSRF cookies the
Django backend sets land on this app's own origin.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server (Turbopack), restarted automatically if it stops serving pages that exist - see `scripts/dev.mjs` |
| `npm run build` | Production build |
| `npm run start` | Serve a production build |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest (unit/component tests) |

## Structure

```
src/
  app/                 Routes (App Router)
    login/
    organizations/select/
    o/[orgSlug]/        Everything inside the platform shell
      apps/              The launcher
      settings/          profile, organization, members, products
  components/          Small, route-agnostic UI primitives
  features/            Route-scoped feature code (auth, launcher)
  lib/
    api/                One fetch client + typed calls per backend surface
    auth/               useSession()
  proxy.ts             Optimistic auth redirect + CSP (Next.js 16 "Proxy",
                        formerly Middleware)
```
