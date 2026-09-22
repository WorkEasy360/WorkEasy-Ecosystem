# 04 — Seat Enforcement

## The gap, and where it closes

Phase 3's audit ([17-audit-findings.md](../phase-3/17-audit-findings.md),
"Seat trigger"): an organization holding a per-seat product with every seat
assigned still authenticated an unseated member into it, because the OIDC
contract carries no `membership_id` a product could check itself. The audit
concluded the gap "closes in the platform or not at all", and named the
launcher phase as where.

It closes in `platform/ecosystem/authorization.py`, not only in the
launcher:

- `require_seat(subject, product_code)` runs as a new step in `authorize()`
  (step 9, after `require_entitlement`), and again inside
  `_revalidate_subject()` at code redemption - exactly the same
  re-validate-don't-trust-the-earlier-answer property entitlement itself
  already had, extended to the seat question.
- It reads a product's licensing mode through `contracts/ports/catalog.py`
  (widened to carry `licensing_mode`, additive) and, only for `per_seat`
  products, calls the entitlement gate's `has_seat()` (widened onto the
  raising-form `EntitlementGate` protocol, mirroring the read-only
  `EntitlementService` it already lived on).
- A new OAuth error code, `product_no_seat` - the `no_seat`
  `EntitlementReason` reserved since Phase 3 specifically for this, made
  real. Redirected to the product exactly as `product_not_entitled` already
  is; the product's own client library is expected to render "ask your
  administrator for a seat".

This is deliberately **not delegated to the launcher's pre-flight
decision alone**. A product reachable via its own deep link (skipping the
launcher entirely) must still be refused; a seat unassigned in the seconds
between the launcher rendering "Open" and the browser completing the
redirect must still be refused. Both require the check to live in the
protocol path itself.

## What already existed (Phase 2), reused rather than rebuilt

`Product.licensing_mode`, `SeatGrant`/`SeatAssignment`, and the
concurrency-safe `platform.entitlements.services.seats.assign_seat` (row
lock, ordered, idempotent, single-savepoint retry on a duplicate-assignment
race) were already built. Task 12's canonical scenario - "10-seat limit, 9
allocated, 8 simultaneous assignments → exactly one succeeds" - is already
covered by
`tests/integration/test_commercial_concurrency.py::test_concurrent_seat_assignment_cannot_overallocate`,
alongside same-member-twice, seat-vs-reconcile, and over-limit-flagging
races. Phase 4 added no new seat-assignment concurrency primitive because
none was missing; it added the *authorization-time check* against the
primitive that already existed, plus the seat-administration HTTP surface
(`platform.launcher.services.assign_seat`/`release_seat`, Task 13) — both
newly authz-gated and audited (`launcher.seat.assigned`/`released`).

## Licensing mode is explicit, never guessed (Task 11)

`can_launch_product` and `require_seat` both branch on
`licensing_mode == PER_SEAT` read from the catalog - never on whether a
`SeatGrant` happens to exist for the pool. An organization-wide product with
no seat pool at all is `ACTIVE` once entitled; nothing infers licensing mode
from the presence or absence of seat rows.

## Cost, measured

See [09-performance.md](09-performance.md) for the exact query-budget
deltas this added to the OIDC `authorize` and `token` endpoints.
