# 03 — Launcher Access Model

See [ADR-021](../architecture/adrs/ADR-021-launcher-access-decision-and-seat-projection.md)
for the architectural decision (a new platform layer, P4, and why). This
document is the model itself.

## `AppAccessState` — five states, closed (Task 8)

| State | Meaning | Frontend renders |
|---|---|---|
| `active` | Entitled, seated (or org-wide), integration live | "Open" |
| `no_seat` | Entitled, per-seat, this member has no seat | "No seat" badge, no action |
| `no_entitlement` | Integration live, organization does not hold it | "Not activated" |
| `integration_suspended` | Entitled (+ seated), integration reversibly down | "Temporarily unavailable" |
| `product_unavailable` | No live integration at all, or unknown code | "Coming soon", no action |

`AVAILABLE` from the brief's example list is deliberately not a sixth state:
it would render identically to `no_entitlement` ("Activate/Learn More"), and
a state nothing distinguishes in the UI is a state that exists only to be
forgotten by a future change. If a genuine distinction (e.g. "not yet
purchasable at all" vs. "purchasable now") is needed later, it is a state to
add then, deliberately.

## `can_launch_product(ctx, product_code)` — the one decision (Task 9)

`platform/launcher/access.py`. Order matters and each step is chosen for a
stated reason (see the function's own docstring for the full reasoning):

1. No live integration (`platform/ecosystem`, `draft`/`retired`/missing) →
   `PRODUCT_UNAVAILABLE`. Checked first: whether the organization holds the
   product is moot when there is nowhere to send a browser.
2. Not entitled (`EntitlementGate.check`) → `NO_ENTITLEMENT`. Checked before
   operational status, so an organization that never bought a product never
   learns whether it happens to be having an outage (Task 48).
3. Integration `suspended` → `INTEGRATION_SUSPENDED`.
4. Per-seat and no seat (`EntitlementGate.has_seat`) → `NO_SEAT`.
5. Otherwise → `ACTIVE`, with the registered `launch_url` attached for the
   caller to redirect to.

**Called from exactly two places**, and they cannot disagree because there
is one function: the app directory (informational) and
`platform.launcher.services.launch` (the launch endpoint's pre-flight
re-check, Task 16). A decision rendered by the directory a moment ago is
never trusted by the launch endpoint - it re-runs the same call.

## What it is not

Not a cache, not a new commercial model, not a second implementation of
entitlement or seat logic. Every fact it reads comes from
`contracts/ports/entitlements.py` (`check`, `has_seat`) and
`platform/ecosystem/selectors.py` (`launch_target`) - the same ports the
OIDC authorization flow itself reads. See
[04-seat-enforcement.md](04-seat-enforcement.md) for that flow's own,
independently re-checked, use of the identical two calls.
