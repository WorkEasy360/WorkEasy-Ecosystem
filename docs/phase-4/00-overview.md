# 00 — Phase 4 Overview

## The question this phase exists to answer

> Can a user now enter Workeasy360 once, see all the products their
> organization can use, and securely launch separately developed products
> according to organization entitlement and member seat access?

## Yes, with the seat gap Phase 3 identified now closed

Phase 3's audit named a real gap and recommended exactly where to close it:
an organization holding a per-seat product with every seat assigned still
authenticated an unassigned member into it, because the remote OIDC contract
carries no per-member identifier a product could check itself
([phase-3/17-audit-findings.md](../phase-3/17-audit-findings.md), "Seat
trigger"). Phase 4 closes it in the one place that can:
`platform.ecosystem.authorization.require_seat()`, run in the OIDC
authorization flow itself and re-checked at redemption, exactly as
entitlement already was.

Everything else Phase 4 builds exists to make that decision - and the
broader "may this organization use this product at all" decision - visible
and actionable to a human, for the first time: a login page, an organization
chooser, an app launcher, and seat/member administration, all served by a
new Next.js frontend calling a set of new platform HTTP endpoints.

## What is new

**Backend**

- `platform/launcher` (new P4 module, [ADR-021](../architecture/adrs/ADR-021-launcher-access-decision-and-seat-projection.md)):
  the one authoritative `can_launch_product()` decision (Task 9), the
  data-driven app directory, and seat administration.
- `require_seat()` in the OIDC authorization flow (Task 11).
- `PendingAuthorization` and the `/integrations/oauth/resume` endpoint: the
  platform now interrupts an unauthenticated or org-less `/authorize`
  request with its own login page or organization chooser and resumes it
  afterward, rather than bouncing an error back to the product (Tasks 31/32).
- Session login/logout (`platform/identity`), and `SessionEpochMiddleware`,
  which is the first thing that actually reads `User.session_epoch` -
  `revoke_sessions()` now really ends live sessions (Task 33).
- `SecurityHeadersMiddleware`: CSP and Permissions-Policy on every response
  (the CSP trigger, Task 34).

**Frontend** (`frontend/`, new)

Next.js 16 + TypeScript. Login, organization chooser, the app launcher, and
platform settings (profile, organization, members, product access/seats).
No product-domain UI, per the brief and per ADR-017.

## Scope discipline, as required

Not built: CRM/Books/Inventory/etc. business UI; Super Admin; white-label/
reseller; a request-a-seat workflow; profile editing; organization creation/
signup (this gap pre-dates Phase 4 - see
[05-organization-selection.md §4](05-organization-selection.md)); real
billing/checkout (Task 19 explicitly forbids faking it).

## Documents in this set

| # | Document |
|---|---|
| 01 | [Frontend architecture](01-frontend-architecture.md) |
| 02 | [App directory](02-app-directory.md) |
| 03 | [Launcher access model](03-launcher-access-model.md) |
| 04 | [Seat enforcement](04-seat-enforcement.md) |
| 05 | [Organization selection](05-organization-selection.md) |
| 06 | [SSO UI integration](06-sso-ui-integration.md) |
| 07 | [Security](07-security.md) |
| 08 | [CSP and browser security](08-csp-and-browser-security.md) |
| 09 | [Performance](09-performance.md) |
| 10 | [Resilience](10-resilience.md) |
| 11 | [Accessibility](11-accessibility.md) |
| 12 | [Cleanup review](12-cleanup-review.md) |
| 13 | [Test report](13-test-report.md) |
| 14 | [**Adversarial audit**](14-opus-audit.md) — findings, fixes and the evidence for each |
| — | [Phase 4 status](phase-4-status.md) |
