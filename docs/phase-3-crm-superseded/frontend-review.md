> **SUPERSEDED — CRM MOVED OUTSIDE PLATFORM SCOPE.**
>
> This document describes the CRM product as it was built *inside* this
> repository during Phase 3. That code was removed by the Phase 3R scope
> correction: products are now separate applications in their own
> repositories ([ADR-017](../architecture/adrs/ADR-017-products-are-separate-applications.md),
> [16 Ecosystem / Product Boundary](../architecture/16-ecosystem-product-boundary.md)).
>
> Retained as historical audit evidence only. **Nothing here describes the
> current repository.** For what was kept from this work and why, see
> [Phase 3R status](../phase-3R/phase-3R-status.md).

---

# Frontend Review

**Status:** Accepted (Phase 3)

---

## Decision: no frontend was built

Task 56 is explicit: "If frontend foundation does not yet exist according
to architecture, do not create an unrelated frontend architecture merely to
satisfy this task. Document the decision."

**No frontend foundation exists in this repository.** Confirmed before any
Phase 3 code was written: no `package.json`, no `next.config.*`, no
`/frontend` or `/web` directory anywhere in the tree. The only description
of a frontend anywhere in this codebase is the *planned, target-state*
structure in `docs/architecture/14-repository-structure.md` §7
(`frontend/src/{app,features,platform,lib,components}/`, Next.js App
Router, a generated OpenAPI client, `<EntitlementGate>`/`<Can>`
components) - prescriptive documentation, not scaffolding. Nothing was
generated from it.

## What this means for Phase 3

- CRM ships as a JSON API only (`products/crm/api/`), reachable at
  `/api/v1/orgs/<slug>/crm/...` - see the endpoint list in
  [crm-domain-model.md](crm-domain-model.md) and
  [test-report.md](test-report.md).
- No CSP was configured (see
  [failure-mode-review.md](failure-mode-review.md) §4) - there is no HTML
  surface for a content-security-policy header to protect.
- Tasks 57 and 58 (frontend performance, accessibility) do not apply: there
  is no frontend code to review for either.

## What would trigger building one

Building a frontend from zero, inside a phase whose brief is explicitly
about the CRM *domain*, would be exactly the "unrelated frontend
architecture" Task 56 warns against - it would invent the App Router
layout, the API client generation pipeline, the auth/session handling and
the design system in the same phase as the domain model they are meant to
sit on top of, none of which this brief asked for and all of which are a
separate, substantial body of work in their own right.

The trigger is a future phase (per Phase 2's own recommended sequencing,
"the first product, end to end" precedes broader UI work) that explicitly
scopes building `frontend/` from the architecture document's target
structure - at which point CRM's JSON API, built to the platform's existing
conventions (thin views, explicit selectors, bounded pagination, allowlisted
filter/sort), is exactly what that frontend would call.
