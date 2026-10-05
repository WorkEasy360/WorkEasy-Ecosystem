# Phase 8 — Frontend (Tasks 87-90)

Three screens, deliberately minimal. None renders model output, a prompt, a
retrieved passage or a document: they show settings, limits, spend and
metadata. There is no product dashboard.

## 1. Settings → AI (`/o/<slug>/settings/ai`)

`features/intelligence/AiSettings.tsx`. Needs `platform.ai.view`; editing
needs `platform.ai.manage` and a fresh step-up (the `useStepUp` flow), with a
revision check against lost updates.

| Section | Shows |
|---|---|
| Status | on / off by the organization / suspended by the platform; this month's estimated spend against the organization limit, when it resets; "estimated cost for spend control, not an invoice" |
| Products | per product: spend, reserved, requests, tokens against the plan's quota |
| Recent requests | metadata only: when, product, operation, outcome, model, estimated cost — keyset-paginated |
| Settings | AI on/off, monthly budget (≤ the platform cap, shown), which providers may process the organization's data — with each provider's recorded region, input retention, training use and note, and the statement that deleting knowledge does not delete what a vendor already received — and whether a second vendor may answer when the first is unavailable |

## 2. Settings → Analytics (`/o/<slug>/settings/analytics`)

`features/intelligence/AnalyticsShell.tsx`. Needs `platform.analytics.view`.
A generic, bounded query form over the metrics of products the organization
holds: metric, granularity, range (the window helper `windowFor` produces
aligned bounds within the server's limits) and at most one grouping. The
result is a table (no chart library), money formatted per currency and never
summed across currencies, with the freshness line ("N recent event(s) not yet
included - figures are catching up"). The server remains the authority on every bound;
the form only avoids asking for what it would refuse.

## 3. Super Admin → AI (`/admin/ai`)

`features/controlplane/AdminAi.tsx`, inside the Phase 7 control plane, shown
with `platform.staff_ai.view`.

| Section | Shows / does |
|---|---|
| Providers | status, **credential configured yes/no** (never a value), data handling; enable/disable with a reason (`staff_ai.manage`, AAL2) |
| Models | provider, status, tier, capabilities, current price version, circuit state; enable/disable with a reason |
| Organization | look up by id: its policy (customer and platform fields), this month's spend by scope, latest requests (metadata); suspend/resume its AI and set its platform cap and premium allowance, each with a reason — audited in both the platform and the organization's audit |

Adding a model (created disabled) and publishing a price are API-only
(`POST /api/v1/admin/ai/models`, `…/models/<key>/prices`) with the same
permission, step-up and audit; there is no endpoint field anywhere.

## 4. Caching, CSP, accessibility (Tasks 89, 90)

- **No caching across users or organizations.** Every call goes through
  `apiFetch` (same origin, `cache: "no-store"`, CSRF header on writes).
- **CSP.** The Phase 4 nonce-based policy is unchanged; nothing here adds an
  inline script, `eval`, a third-party origin or `dangerouslySetInnerHTML`
  (fitness test `test_the_frontend_never_renders_html_from_the_server`).
- **Accessibility.** Labelled controls, tables with captions and row headers,
  `role="alert"` for errors, `aria-live`/`role="status"` for loading and
  results, keyboard-reachable actions.
- **Navigation.** "AI" and "Analytics" in the organization shell; "AI" in the
  control plane when the staff member holds `platform.staff_ai.view`.

## 5. Real-browser verification

`scripts/phase8_browser_check/` (the Phase 7 harness pointed at these
screens): the production build against a real backend on a fresh Phase 8
database, seeded with an organization that has a month of metering (so the
usage tables render rows, not empty states), and the Super Admin
organization panel opened by looking that organization up.

| Pages | CSP violations | Console errors | axe (WCAG 2.1 A/AA, incl. contrast) | Focus not visible (first 40 tab stops) | Horizontal scroll at 320px |
|---|---|---|---|---|---|
| Settings → AI, Settings → Analytics, Super Admin → AI (+ organization panel), and the eight other organization pages | 0 | 0 | 0 | 0 | 0 |

**Found and fixed by it:** with rows present, Settings → AI scrolled
horizontally at 320px (content 679px wide). Two causes, both general: the
organization shell's `main` centred itself with auto margins, which in a
flex column turns stretching off and sized it to its widest table; and the
intelligence grid lacked `minmax(0, 1fr)`, so a card grew to its table. The
shell fix applies to every organization page, which is why the other eight
were re-checked.

Unit tests: `frontend/src/features/intelligence/intelligence.test.tsx`
(jsdom + axe per state).
