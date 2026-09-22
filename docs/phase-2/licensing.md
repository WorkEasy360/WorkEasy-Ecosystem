# Phase 2 — Licensing and Seats

**Date:** 2026-09-21

---

## 1. No new License model

> **Task 13 asks for `License`, a seat allocation model and
> `LicenseAssignment`, "or exact architecture-approved equivalents".**

The architecture-approved equivalents already exist. 06-entitlements.md §6
defines `SeatGrant` (capacity) and `SeatAssignment` (occupancy), and Phase 1
shipped both, with the partial unique constraint already in place.

Adding a parallel `License` / `LicenseAssignment` pair would be a **duplicate
implementation** of a model the architecture already names — precisely what
the lean-code requirement exists to prevent, and two sources of truth for
"who may use this" is the worst possible place to have two.

So Phase 2 adds no licensing models. It adds the **service layer** those two
models were waiting for, and one field.

| Concept in Task 13 | What it is here |
|---|---|
| License | `SeatGrant` — capacity in a pool, one row per granting source |
| Seat allocation model | `pool_key`, which is a product code or a bundle's shared pool name |
| LicenseAssignment | `SeatAssignment` — a membership occupying a seat |

The three licensing strategies the task names as "future" map onto what
exists:

| Strategy | How |
|---|---|
| Organization licence | `Product.licensing_mode = per_organization`; entitlement without seats |
| Named user seat | `SeatGrant` + `SeatAssignment`, `has_seat()` |
| Feature quota | `FeatureEntitlement` with `kind=quota`, `quota()` / `within_quota()` |

No generic licensing engine was built. Each strategy is a shape the existing
models already express.

## 2. Two writers, deliberately

| Question | Answer lives in | Written by |
|---|---|---|
| How many seats does this organization have? | `SeatGrant` | `commercial/provisioning`, only |
| Which member holds one? | `SeatAssignment` | `platform/entitlements/services/seats.py` |

Capacity is a commercial fact derived from subscriptions, so it belongs to
the single entitlement writer. Occupancy is a customer decision made by an
administrator holding `platform.seat.assign`; it changes no commercial state
and must not require a commercial code path.

Conflating them would mean either products could reach a capacity writer, or
a customer administrator assigning a colleague would have to call into
`commercial`. A new import-linter contract,
`products-never-write-seats`, names `services/seats.py` alongside
`services/write.py` for the `products` source.

## 3. The concurrency guarantee

> **Task 14: plan limit 10, nine assigned, two simultaneous requests. Both
> must not become seat #10 and #11.**

```python
with transaction.atomic():
    grants = SeatGrant.objects.select_for_update()
        .filter(organization_id=…, pool_key=…).order_by("id")
    ...
    used = SeatAssignment.objects.filter(seat_grant__in=grants,
                                         released_at__isnull=True).count()
    if used >= total:
        raise SeatLimitReached(...)
```

Three mechanisms, each covering what the others cannot:

1. **`select_for_update` on every grant in the pool, ordered by id.** The
   lock serialises concurrent assignments; ordering by id gives a consistent
   lock order, so two callers working on overlapping pools cannot deadlock.
2. **The occupancy count runs inside the same transaction, after the lock**,
   so it cannot be stale by the time it is acted on.
3. **The partial unique constraint** on `(seat_grant, membership)` where
   `released_at IS NULL` catches the remaining case — the same member
   assigned twice. That race is between two *different rows*, so no lock
   ordering prevents it; only the database can.

A counter cache with optimistic checking was rejected in Phase 0: it can
over-allocate under concurrency, and over-allocating paid seats is a revenue
leak that is embarrassing to explain.

### Proved, not asserted

`test_concurrent_seat_assignment_cannot_overallocate` — real threads, real
connections, real commits, a `Barrier` so they arrive together:

```
pool of 10, 9 already assigned, 8 threads contend
  → 1 assigned, 7 refused, 10 seats allocated in total
```

`test_concurrent_assignment_of_the_same_member_takes_one_seat` — 8 threads
add the same person; one seat is consumed and all 8 return successfully,
because assignment is idempotent.

`test_concurrent_reconcile_and_seat_assignment_do_not_deadlock` — provisioning
rewrites capacity while admins assign seats. If the two paths ordered their
locks differently this would deadlock under load, which is the kind of
failure that only appears in production.

## 4. Idempotence

Assigning a seat the member already holds returns the existing assignment
unchanged. A retried request must not consume a second seat.

Releasing is soft — `released_at` is stamped, the row is not deleted — so
"who had a seat in March?" stays answerable for a licence audit. Releasing
twice returns 0 rather than failing.

## 5. Capacity is never deleted

`SeatAssignment` cascades from `SeatGrant`. Deleting a stale grant during
reconciliation would silently delete every assignment under it — revoking
members' access as a side effect of a *commercial recomputation*, with no
record that it happened.

So stale grants are **zeroed, never deleted**. The capacity goes; the
assignments remain and are marked over-limit.
`test_provisioning_never_deletes_a_seat_grant` cancels a subscription and
asserts the grant row survives with `seats_total = 0` and the assignment
intact.

## 6. The over-limit policy

When capacity drops below occupancy — a downgrade, a bundle component
removed — assignments beyond the new limit are flagged with
`over_limit_since`. **Flagged, never revoked.**

| Property | Behaviour |
|---|---|
| Selection | Most recently assigned lose first (`assigned_at` ascending, keep the first N) |
| Determinism | A retry flags the same members rather than shuffling who is affected |
| Self-clearing | Capacity returning clears the flag; a flag that only ever gets set becomes noise nobody trusts |
| Visibility | `seats.over_limit_assignments(org, pool_key=…)` |
| Effect on access | None yet — the member keeps their seat while the organization decides |

Tested by `test_a_seat_reduction_flags_rather_than_revokes` (four assigned,
capacity two, two flagged, four still assigned),
`test_over_limit_selection_is_deterministic` and
`test_restoring_capacity_clears_the_flag`.

### What is not implemented

The **deadline sweep** — automatically releasing over-limit seats once the
grace window elapses — is not built. The flag and its timestamp are; the
policy that acts on them needs a customer-facing remediation UI to be
meaningful, and releasing someone's access with no way for the customer to
have chosen differently would be the silent revocation the whole design
avoids. Trigger: the seat-management UI.

## 7. Seats do not substitute for entitlement

A seat check is **additive** to the product check, never a replacement. A
cancelled customer whose assignments survive does not keep access through
them: `check(org, product)` denies at the product level first.

`test_a_seat_does_not_substitute_for_a_product_entitlement` assigns a seat,
cancels immediately, and asserts the check denies.

## 8. Shared pools

`pool_key` rather than `product_code` because a bundle may grant 10 seats
usable across CRM and Projects. Per-product pools are the common case
(`pool_key == product_code`); shared pools are the enterprise case, and one
model covers both.

`test_a_bundle_seat_pool_is_shared` buys a bundle whose items both name
`suite` and asserts 15 seats in that pool and 0 in `crm`.
