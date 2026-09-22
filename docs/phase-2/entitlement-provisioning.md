# Phase 2 — Entitlement Provisioning

**Date:** 2026-09-21

---

## 1. The two properties everything rests on

```python
compute_entitlements(organization_id, at) -> ComputedEntitlements   # pure
apply_entitlements(computed)              -> int                    # idempotent
```

**`compute_entitlements` is a deterministic function of durable database
state.** Never of a provider response, never of an event payload. Re-run with
the same `at` it produces the same answer.

**`apply_entitlements` is idempotent and diff-based.** Re-running with the
same input produces the same rows and the same snapshot content.

Together they make duplicated, out-of-order and lost events non-corrupting.
An event is a *hint that something changed*, never the change itself; nothing
in provisioning reads a payload to decide what to write.

`test_computation_is_deterministic` and
`test_reconciling_twice_produces_the_same_rows` pin both.

## 2. Desired state, not deltas

Reconciliation computes what the organization **should** have and diffs.

An incremental "apply this change" model is broken by a duplicated event, by
an out-of-order one, and by a worker that died halfway. A diff-from-truth
model converges from all three, and
`test_reconciling_after_every_kind_of_change_converges` runs a purchase, a
plan change and four reconciliations and asserts the answer never moves.

The strongest form of the property:
`test_entitlements_rebuild_from_nothing` **deletes every entitlement row and
the snapshot**, asserts access is denied, runs one reconciliation and asserts
full access is back. If the entitlement tables were lost entirely, one pass
rebuilds them from subscriptions.

## 3. Inputs

Both read through the owning module's **selectors**, never its models (F-6):

| Input | Selector |
|---|---|
| Subscriptions and their items | `commercial.subscriptions.selectors.commercial_state` |
| Plan versions and their features | `commercial.catalog.selectors.plan_versions` (batched) |
| Support grants | `platform.entitlements.selectors.active_grants` |

Even though provisioning is the sole entitlement *writer*, reading another
module's tables directly is still forbidden: a queryset over another module's
model welds this code to a schema it does not own and is not tested against.

### Support grants are an input, not an override

That is what makes "support fixes it manually, the nightly job undoes it"
structurally impossible. `test_a_support_grant_survives_reconciliation` runs
reconciliation twice after a grant and asserts the access persists.

## 4. Outputs

Per effective subscription item:

| Output | Value |
|---|---|
| `Entitlement` | product code, status from the subscription, seats, effective window |
| `FeatureEntitlement` | one per feature the pinned plan version grants |
| `SeatGrant` | capacity in `item.seat_pool or product_code` |

**`source_ref` is per item**, not per subscription:
`subscriptions.item:<uuid>`. An organization holding CRM from both a bundle
and a standalone plan has two sources, and collapsing them would make
"most generous wins" unable to see there are two.
`test_each_item_is_its_own_entitlement_source` asserts two distinct refs from
one bundle.

`source_kind` is `bundle` for a derived line and `subscription` for a direct
one, so the origin is inspectable without being load-bearing.

## 5. Status mapping

`SubscriptionStatus.entitlement_status` is the single translation point.

| Subscription | Entitlement | Read | Write |
|---|---|---|---|
| `trialing` | `trial` | yes | yes |
| `active` | `active` | yes | yes |
| `past_due` | `past_due` | yes | **yes** |
| `grace` | `grace` | yes | **yes** |
| `suspended` | `suspended` | yes | no |
| `cancelled` | `cancelled` | yes | yes |
| `expired` | `expired` | no | no |

`past_due` and `grace` restrict nothing, deliberately. A failed card is
usually an expired card, not a customer who stopped paying. Escalation is by
notification intensity, not capability removal, until grace elapses.
`test_past_due_and_grace_restrict_nothing`.

`suspended` is read-only, not lockout. The customer's data stays visible and
exportable, the pay-now path stays open, and an entire category of angry
support escalation disappears. `test_suspended_is_read_only_not_locked_out`.

### The invariant is derived, not maintained

`test_commercial_status_produces_the_expected_entitlement_status` is
parametrised over `list(SubscriptionStatus)`. A status added tomorrow is
covered the moment it exists. A hand-maintained list would be missing it, and
the failure mode of a missing mapping is a customer with no access at all.

This is the Phase 1 pattern — derive expectations from the live registry —
carried forward, alongside the parametrised isolation suite that now covers
all five new tenant models automatically.

## 6. Time

`compute_entitlements(at)` evaluates every durable timestamp at one moment:
`cancel_at`, `ended_at`, `trial_ends_at`, `item.added_at`,
`item.removed_at`.

Nothing else in the system checks a clock to decide access. That is what
makes a scheduled downgrade and a period-end cancellation work with no extra
machinery: the rows already say when, and one function reads them.

Expiry is a **positive, durable decision** — a date that has arrived — not
an absence of information, so computing it here stays consistent with
"absence of information never revokes".

`test_access_is_computed_at_a_moment_not_from_a_flag` computes for one minute
either side of a cancellation date and asserts the product appears and then
does not.

## 7. Drift

`reconcile_and_report_drift` compares the **stored snapshot** against a fresh
computation before applying it.

The snapshot, because that is the row every read resolves from — it is what
the customer effectively has. The underlying `Entitlement` rows are rewritten
by the same pass regardless, so a corruption in either is repaired; only a
difference in the *effective answer* is worth waking somebody for.

| On drift | Action |
|---|---|
| Repair | The same `apply_entitlements` call |
| Signal | `entitlement_drift_total` metric |
| Announce | `entitlements.drift.repaired.v1` to the outbox |
| Log | `provisioning.drift_repaired` with the changed keys |

Drift is a bug and is never silently fixed. A rising rate means provisioning
logic or event delivery is broken, and the signal is what surfaces that
before a customer does.

`test_drift_is_detected_repaired_and_signalled` corrupts both the snapshot
and the rows and asserts all four. `test_no_drift_produces_no_signal` asserts
the detector does not cry wolf — a detector people stop believing is worse
than none.

The expected-snapshot computation deliberately reuses the writer's own
`_more_generous_*` functions. A second implementation of "most generous wins"
would eventually disagree with the first, and the detector would then report
drift that does not exist.

## 8. Concurrency

`pg_advisory_xact_lock(hashtext(org_id))` serialises recomputes per
organization. Last-writer-wins is safe because compute is deterministic: two
racing runs from the same durable state produce the same answer.

`test_concurrent_reconciles_converge` runs eight simultaneous reconciliations
and asserts one snapshot, correct contents, and a monotonically advanced
version.

Transaction-scoped (`_xact_`) rather than session-scoped, because session
locks are broken by transaction pooling.

## 9. The single-writer rule

Three independent enforcements:

1. **Import-linter contract A10** names
   `platform.entitlements.services.write` as forbidden to every module except
   `commercial/provisioning`.
2. **`test_provisioning_is_the_only_entitlement_writer`** walks every file in
   the package by AST and asserts no other module imports it — including a
   module added tomorrow that nobody thought to add to the contract.
3. **Selectors, not models.** Provisioning reads through DTOs, so it cannot
   accidentally write through a live model instance it was handed.

## 10. Triggers

| Trigger | Latency | Guarantees |
|---|---|---|
| `notify_commercial_change` after a command | immediate | none — best effort |
| `commercial.provisioning.reconcile_all`, hourly | up to an hour | correctness and drift repair |
| `commercial.subscriptions.process_due`, 5 minutes | up to 5 minutes | time-based transitions |

Two triggers, **one implementation**. The hook is a latency optimisation; the
scheduled pass is the correctness mechanism. Losing a notification delays
provisioning, it cannot lose it, and
`test_a_provisioning_failure_does_not_roll_back_the_purchase` asserts the
commercial record stands and reconciliation repairs access.

`reconcile_all` enumerates organizations from the **control-plane**
`Organization` table. Building the work list from a tenant table would return
nothing outside a tenant scope, and the job would report success having
visited nobody — see the P0 in the failure-mode review.

Per-organization failures are isolated: one corrupt tenant must not stop the
pass for everyone else. `test_a_failing_organization_does_not_stop_the_pass`.
