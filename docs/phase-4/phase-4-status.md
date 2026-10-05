# Phase 4 — Status

**PHASE 4 STATUS: PASS**, after an adversarial audit that found and fixed
one P0 and nine P1s — see [14-opus-audit.md](14-opus-audit.md). Remaining
gaps are carried forward explicitly (§4).

**Date:** 2026-09-22 (implementation), 2026-09-22 (audit)
**Baseline:** Phase 3, 933 passing (re-confirmed before any Phase 4 change)
**Result after audit:** 1,010 backend tests passed · 0 failed · 0 skipped,
from a database migrated fresh from zero. 29 frontend tests passed ·
0 failed, from a clean install.

> **The audit's headline, recorded here rather than only in its own
> document.** The production frontend could not execute any JavaScript on
> `/`, `/login`, `/organizations/select` or `/_not-found`: a nonce-based
> `script-src` with `'strict-dynamic'` over four statically prerendered
> routes meant every script tag on those pages shipped without a nonce and
> would have been blocked. Sign-in was impossible. Nothing in the suite
> could see it — components run in jsdom where there is no CSP, no test
> produced a production build, and CI had no frontend job at all. Fixed,
> verified against a real build, and now held by a build-output gate in CI.

---

## 1. The question this phase existed to answer

> Can a user now enter Workeasy360 once, see all available products, and
> securely launch separately developed products according to organization
> entitlement and member seat access?

## YES.

A signed-in user sees an app directory built entirely from catalog and
access-decision data (no hardcoded product list), opens an entitled,
seated product through a re-checked, server-controlled redirect, and is
refused - server-side, regardless of what the browser was last shown - if
the organization does not hold the product or this specific member holds no
seat. The seat gap Phase 3's audit identified and deferred is closed in the
OIDC protocol path itself, not only in the launcher's UI.

---

## 2. Exit gate

| Requirement | Status | Evidence |
|---|---|---|
| Workeasy360 frontend foundation exists | ✅ | `frontend/`, Next.js 16 + TypeScript, builds clean |
| Login UX works | ✅ | `/login`, real session, CSRF-protected (verified with `enforce_csrf_checks=True`) |
| Organization chooser works | ✅ | `/organizations/select`, auto-continue at exactly one org |
| Authorization flow can resume safely | ✅ | `PendingAuthorization`, single-use, no cross-org substitution — [06](06-sso-ui-integration.md) |
| Unified shell exists | ✅ | `Shell.tsx` — branding, org identity, platform nav only, no product nav |
| App directory is data-driven | ✅ | `ProductDirectory.list_marketable()`, zero hardcoded product codes |
| App launcher is data-driven | ✅ | Same decision (`can_launch_product`) backs directory and launch |
| No product-specific branches | ✅ | Pre-existing AST fitness tests unbroken; no new branch added anywhere in Phase 4 code |
| Entitlement status represented correctly | ✅ | `NO_ENTITLEMENT` state, `EntitlementGate.check` |
| Seat requirement represented correctly | ✅ | `NO_SEAT` state; licensing mode read explicitly, never guessed from seat count |
| Seat assignment works | ✅ | `platform.launcher.services.assign_seat`/`release_seat`, audited |
| Concurrent seat limit cannot overallocate | ✅ | Phase 2's `test_concurrent_seat_assignment_cannot_overallocate` et al., carried forward unchanged — [04](04-seat-enforcement.md) |
| Launch requires seat where applicable | ✅ **and now tested** | `require_seat()` in the OIDC flow, re-checked at redemption. The audit found this had **no test at either boundary** and added three |
| Organization-wide licensing still works | ✅ | `test_organization_wide_product_needs_no_seat` |
| OIDC launch re-checks access | ✅ | `authorize()` and `_revalidate_subject()` both call `require_entitlement`+`require_seat` |
| No browser can forge launch target | ✅ | `launch_view` builds the redirect server-side from `platform/ecosystem`; the browser never supplies or sees `launch_url` |
| Multi-org switching cannot leak app access | ✅ | No client-side cache exists to leak from, **and the organization is never in the session** — it comes from the URL and is re-resolved to a membership per request, which is also what makes multi-tab safe — [14](14-opus-audit.md) §3 |
| Frontend cache cannot leak tenant state | ✅ by construction | `cache: "no-store"` on every call, no state-management library, org slug in every URL |
| Product outage does not crash launcher | ✅ | `INTEGRATION_SUSPENDED`/`PRODUCT_UNAVAILABLE` are ordinary states; Phase 3's unconditional empty-registry readiness test still holds |
| CSP/security headers exist | ✅ **after audit** | Shipped broken: the policy made the production app inert. Corrected and verified against a real production build — [08](08-csp-and-browser-security.md), [14](14-opus-audit.md) §1 |
| Rate-limit trusted-IP debt is resolved | ✅ **after audit** | D-009 closed. Trusted-proxy client-IP resolution, and login/resume throttling that Phase 4 shipped without entirely — [07](07-security.md) |
| Accessibility baseline passes | ⚠️ partial | Built to WCAG patterns by inspection; **no automated audit or screen-reader pass run** — [11](11-accessibility.md) |
| Frontend performance measured | ✅ **after audit** | Lighthouse 100/100/100 on the production build; bundle measured in gzip; query slopes measured at 5/20/50 products — [09](09-performance.md) |
| Lean-code cleanup completed | ✅ | [12-cleanup-review.md](12-cleanup-review.md) |
| Products remain external | ✅ | `products/` unconditionally empty, unchanged |
| No unresolved P0 | ✅ | One found by the audit (the CSP), fixed and verified |
| No Phase-5-blocking P1 | ✅ | See §12 |
| Full backend + frontend regression passes | ✅ | **1,010/0/0 backend, 29/0 frontend**, both from clean state (973/11 before the audit) |

---

## 3. Report

**1. Backend tests** — 1,010 passed, 0 failed, 0 skipped, fresh database
(973 at the end of implementation; +37 from the audit).
**2. Frontend tests** — 29 passed, 0 failed, clean install (11, +18).
**3. Exact counts** — see [13-test-report.md](13-test-report.md) for the
full breakdown and every quality-gate command run.

**4. Frontend stack/version** — Next.js 16.3.5 (Turbopack default), React
19.2.8, TypeScript 5. Newer than this model's training data; verified
against the version's own bundled docs
(`node_modules/next/dist/docs/`) before writing any code — Middleware was
renamed to Proxy in this version, and this codebase uses the new name
(`src/proxy.ts`) throughout.

**5. Models/tables added** — 1: `PendingAuthorization`
(`ecosystem_pending_authorization`). Control-plane, RLS-exempt for the
identical, documented reason `AuthorizationCode` already is (reachable only
through the SHA-256 of a random secret).

**6. Migrations** — 1 (`ecosystem.0002_pendingauthorization`).

**7. APIs added** — 12 new endpoints:

```
POST /api/v1/auth/login                                      session
POST /api/v1/auth/logout                                     session
GET  /api/v1/auth/session                                    session, issues CSRF cookie
GET  /api/v1/organizations                                   session
GET  /api/v1/integrations/oauth/resume                       none (resume token)
GET  /api/v1/orgs/<slug>/organization                        tenant
GET  /api/v1/orgs/<slug>/apps                                 tenant
POST /api/v1/orgs/<slug>/apps/<code>/launch                   tenant
GET  /api/v1/orgs/<slug>/apps/<code>/seats                    tenant + platform.seat.assign
POST/DELETE /api/v1/orgs/<slug>/apps/<code>/seats/<membership_id>  tenant + platform.seat.assign
```

Plus one field widened on an existing endpoint (none — the entitlement
verify/authorize/token endpoints' wire shapes are unchanged; only their
internal decision logic gained a step).

**8. Frontend routes** — `/`, `/login`, `/organizations/select`,
`/o/[orgSlug]/apps`, `/o/[orgSlug]/settings/{profile,organization,members,products}`.
9 total (including `/_not-found`).

**9. Launcher states** — five, closed:
`active`/`no_seat`/`no_entitlement`/`integration_suspended`/`product_unavailable` — [03](03-launcher-access-model.md).

**10. Login/org chooser results** — both functional over real HTTP,
verified by a manual `curl`-based walkthrough against real Next.js + Django
dev servers, in addition to the automated suite: login sets a session,
`session` reflects it, `organizations` returns exactly the signed-in user's
memberships and nothing else, isolated per user
(`test_a_users_organizations_are_isolated_from_another_users`).

**11. Authorization-resume results** — 8/8 tests passing, covering every
Task 32 property (single-use, no product-controlled failure destination, no
cross-org substitution, re-park rather than fail when still undecided) — [06](06-sso-ui-integration.md).

**12. Seat-enforcement architecture** — [04-seat-enforcement.md](04-seat-enforcement.md);
ADR-021 for the layering decision behind it.

**13. Seat concurrency results** — Phase 2's existing, unmodified coverage
(`test_concurrent_seat_assignment_cannot_overallocate`: 10-seat pool, 9
taken, N concurrent contenders for the last seat → exactly 1 succeeds;
`test_concurrent_assignment_of_the_same_member_takes_one_seat`;
`test_concurrent_reconcile_and_seat_assignment_do_not_deadlock`). No new
concurrency primitive was needed; Phase 4 added the authorization-time
*check* against a primitive that was already correct.

**14. Multi-org isolation results** — verified at the HTTP layer
(`test_a_users_organizations_are_isolated_from_another_users`) and true by
construction on the frontend (no cross-request state exists to leak) — not
independently fault-injection-tested beyond that.

**15. Cache-isolation results** — by construction, not by a targeted
regression test: `cache: "no-store"` on every request, no client-side
state-management/data-fetching library, organization slug embedded in every
URL rather than held in global state. See
[01-frontend-architecture.md](01-frontend-architecture.md) for why this
was judged sufficient rather than adding a library specifically to then
prove it does not leak.

**16. SSO launch results** — `test_launch_redirects_to_the_registered_launch_url_with_organization_context`
and the paired denial test confirm the launch endpoint's redirect target and
its re-check; the full OIDC authorize→token round trip (unaffected by this
phase except for the new seat step) remains covered by Phase 3's suite,
re-run and passing throughout this phase's work.

**17. Security findings** — three real bugs found and fixed during this
phase's own work (not shipped): `SessionEpochMiddleware` initially
force-logged-out every test-harness session; `platform.launcher.access`
initially imported another module's model directly (caught by a
pre-existing architecture fitness test); the CSRF cookie was not being
issued at all until `session_view` was given `@ensure_csrf_cookie` — see
[07-security.md](07-security.md) and
[12-cleanup-review.md](12-cleanup-review.md) for detail on each.

**18. CSP/header findings** — trigger reached and closed, two
non-conflicting layers — [08](08-csp-and-browser-security.md).

**19. Rate-limit/IP findings** — D-009 re-evaluated, correctly left
unresolved pending known deployment topology — [07](07-security.md).

**20. Accessibility findings** — built to pattern, **not tool-verified** —
[11](11-accessibility.md).

**21. Performance findings** — query budgets pinned and measured for every
new endpoint; bundle size measured; Lighthouse/CWV **not run** —
[09](09-performance.md).

**22. Resilience findings** — Redis and RabbitMQ both stopped, 231 tests
still passing across login/session/launcher/OIDC/resume — a result, not an
architectural claim — [10](10-resilience.md).

**23. Observability findings** — new metrics:
`identity_login_failures_total`/`identity_login_success_total`,
`ecosystem_authorization_parked_total`, `launcher_launch_allowed_total`/
`launcher_launch_denied_total`, `organizations_for_user_scan_truncated_total`.
None carry a user/org id as a label, consistent with the existing
cardinality guard.

**24. Cleanup findings** — five scaffold files removed, zero dead code
knowingly left behind; three bugs found and fixed by the test suite itself
- [12-cleanup-review.md](12-cleanup-review.md).

**25. Dependencies added/removed** — Backend: **0 added, 0 removed**.
Frontend: `next`/`react`/`react-dom`/`typescript`/`eslint` (the approved
stack) + `vitest`/`@testing-library/*`/`jsdom`/`@vitejs/plugin-react`
(dev-only, Task 45 coverage). `npm audit`: 0 vulnerabilities. `pip-audit
--strict`: 0 vulnerabilities.

**26. Architecture deviations/ADRs** — One: [ADR-021](../architecture/adrs/ADR-021-launcher-access-decision-and-seat-projection.md)
(new platform layer P4, the seat read model, the launcher's access
decision). Two `contracts/` ports widened additively (`ProductDescriptor`
gains `licensing_mode`/`description`; `EntitlementGate` gains `check`/
`has_seat`) — neither is a new pattern, both extend ports the existing
runtime-registry design already established.

**27. Files/LOC changed** — Backend: 33 files changed since the Phase 3
checkpoint, +2,696/−63 lines (three commits:
`c0c0750` seat enforcement, `b0a20cb` authorization resume, `91eaf7d`
launcher/identity/seats), plus this documentation pass. Frontend: 32 source
files, ~2,208 lines (TypeScript/TSX/CSS).

**28. Fresh-environment result** — Backend: `pytest --create-db`, full
suite, from zero → **1,010/0/0**. Frontend: `rm -rf node_modules .next && npm
ci`, then build/lint/typecheck/test → all clean. Both are Task 64's
requirement, both genuinely re-run for this report, not carried over from
an earlier pass.

**29. Decisions proposed to freeze for Phase 5** — §4 below.

**30. Explicit answer** — see §1: **YES.**

---

## 4. Decisions proposed to freeze for Phase 5

1. **`platform/launcher` is P4.** Composes `platform/ecosystem` in addition
   to everything ecosystem itself composes. A future platform module
   needing ecosystem's data reaches it the same way - a narrow selector, not
   the model.
2. **The launcher's access decision is the only one.** `can_launch_product`
   is called by the directory and by launch; nothing else computes or
   caches an independent answer.
3. **Seat enforcement lives in the OIDC protocol path, not only the
   launcher.** A product reachable by its own deep link, bypassing the
   launcher entirely, is still refused.
4. **All auth-sensitive frontend data fetching is client-side**, through the
   `rewrites()` proxy, never a Server Component fetching Django directly.
   See [01-frontend-architecture.md](01-frontend-architecture.md) for why -
   this is a real architectural choice a future contributor should not
   "helpfully" undo by moving a fetch into a Server Component, which would
   silently break cookie propagation.
5. **`organizations_for_user` is an honestly-bounded scan, not a solved
   query.** The Phase 5 fix (a denormalised, RLS-exempt projection,
   synchronised at every membership write) is specified but not built - see
   [05-organization-selection.md](05-organization-selection.md) §3.
6. **Organization creation/onboarding does not exist yet** as a first-class
   flow (only direct model construction in test fixtures). A prerequisite
   for the organization-settings work to matter to a real new customer -
   [05-organization-selection.md](05-organization-selection.md) §4.
7. ~~**D-009 (rate-limit trusted-IP) is still open**~~ — **closed by the
   audit.** The topology is not something the application can discover; it
   is something a deployment must state, and `W360_TRUSTED_PROXY_COUNT` is
   how it states it (`required=True` in production). The forwarded chain is
   read from the right, so no entry a caller could have written is ever
   used. `/auth/login`, which Phase 4 shipped entirely unthrottled, and the
   resume endpoint are now both rate-limited.
8. **No MFA/step-up UI exists.** Acceptable because nothing in Phase 4's UI
   is a `dangerous` permission; will need addressing before any
   `dangerous` action is ever exposed through this frontend.
9. ~~**Accessibility and performance are inspected, not tool-verified.**~~ —
   **done by the audit.** axe-core: 0 violations. Lighthouse on the
   production build: performance 100, accessibility 100, best practices 100.
   A manual screen-reader pass and a measured contrast check remain the only
   outstanding accessibility work.

10. **Every frontend gate now runs in CI.** There was no frontend job before
    the audit, which is the single reason the CSP defect reached a status
    document claiming PASS. A build-output gate
    (`scripts/assert-dynamic-rendering.mjs`) is part of it, because the
    property that broke is only observable in the build's output, never in a
    unit test.

11. **The organization chooser's scan is measured, not described**
    (D-013): 5 queries per organization *on the platform*, per request —
    500 organizations costs 2,506 queries and 1.7 s, and past the 2,000 cap
    a user silently disappears from their own chooser. Still a bounded scan
    rather than a projection table, because the brief is explicit that a
    projection is not to be built until the trigger is reached; the
    measurement is what makes that trigger checkable.

12. **A whole class of bug is invisible to this test suite**: every
    tenant-to-tenant foreign key is `DEFERRABLE INITIALLY DEFERRED`, and
    pytest rolls each test back rather than committing, so a
    cross-organization write passes in tests and 500s at commit in
    production. Found in seat assignment and fixed there with an
    application-level check. Deciding the general answer — a commit-forcing
    test mode, or a rule that any client-supplied id becoming a tenant
    foreign key is checked in the service — is Phase 5 work.

---

## 5. Scope discipline

Not built, per the brief: CRM/Books/Inventory/etc. business UI; Super Admin;
white-label/reseller/affiliate; a request-a-seat workflow; profile editing;
organization creation; real billing/checkout (Task 19 forbids faking it);
global/cross-product search; iframe-based product embedding.

`products/` contains ten `__init__.py` files and nothing else, asserted
unconditionally by the pre-existing fitness test, unchanged.

**Phase 4 is complete, audited, and the gaps in §4 are stated and carried
forward explicitly rather than silently. Phase 5 has not been started.**

The audit's own summary, its findings and the evidence for each are in
[14-opus-audit.md](14-opus-audit.md). Two of its conclusions belong here
rather than only there:

- **Phase 4's backend work held up.** The launch decision has no bypass, the
  launcher cannot override OIDC, seat enforcement survives the races, seat
  removal before redemption denies, `PendingAuthorization` is replay- and
  tamper-safe, session revocation works, the launch target is
  server-controlled, and no cross-organization state can leak through a
  frontend that holds no cache and keeps the organization in the URL rather
  than the session. Those were the hard parts and they were right.
- **The frontend shipped a policy that made it unusable, and no gate could
  see it.** That is the more useful lesson: the missing control was not a
  test, it was a *gate that runs the thing being shipped*. A CI job that
  builds the production bundle and inspects its output now exists, and it is
  what would have caught this on the first push.
