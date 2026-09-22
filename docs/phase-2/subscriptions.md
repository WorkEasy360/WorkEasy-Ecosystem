# Phase 2 — Subscriptions

**Date:** 2026-09-21

---

## 1. Model

```python
Subscription(organization, status, currency, billing_interval,
             collection_mode, renewal_policy,
             current_period_start, current_period_end,
             trial_ends_at, trial_source,
             activated_at, cancelled_at, cancel_at, ended_at, version)

SubscriptionItem(organization, subscription, parent_item,
                 plan_version | bundle_version,
                 product_code, plan_code, plan_version_number, bundle_code,
                 seat_pool, quantity,
                 price_id, price_source, price_reason,
                 unit_amount_minor, total_amount_minor, currency, interval,
                 price_model, tax_behaviour,
                 added_at, removed_at)
```

**One subscription per organization.** A suite purchase becomes many items on
one renewal cycle, not four subscriptions with four dunning cycles. Enforced
by a partial unique constraint:

```sql
UNIQUE (organization_id) WHERE status <> 'expired'
```

That constraint exists because a row lock cannot protect a row that does not
exist yet. Two simultaneous first purchases both found none and both
inserted — found by
`test_concurrent_different_purchases_do_not_create_two_subscriptions` and
fixed by the constraint plus an `IntegrityError` re-read in
`_open_subscription`.

## 2. Effective-dated items

`added_at` and `removed_at` bound the window in which an item grants
anything. Items are **never deleted**: proration, revenue reporting and
disputes all need the history.

One mechanism covers four features that would otherwise each need a table:

| Feature | How it uses effective dating |
|---|---|
| Plan change | close the old line, open the new one at the same instant |
| Period-end downgrade | close and open at `current_period_end`, in the **future** |
| Scheduled cancellation | `Subscription.cancel_at` bounds every line |
| "What did they have on 3 March?" | a query, not an audit-log reconstruction |

The window is half-open — effective from `added_at` inclusive to
`removed_at` exclusive — so a change at an instant never leaves both the
outgoing and incoming line effective at once.

## 3. Lifecycle

```
              ┌──────────────────────────────────┐
trialing ─────┤                                  │
   │          ▼                                  │
   │       active ──> past_due ──> grace ──> suspended
   │          │  ▲        │   ▲      │  ▲        │  │
   │          │  └────────┘   └──────┘  └────────┘  │
   │          ▼                                     ▼
   └──────> cancelled ──────────────────────> cancelled
   │          │
   ▼          ▼
expired <── expired  (terminal)
```

The table lives in `subscriptions/domain.py`. What is **absent** is as
deliberate as what is present:

| Absent edge | Why |
|---|---|
| `cancelled -> active` | The customer, billing and any partner commission have all been told the relationship ends on a date. Resuming is a new subscription, which leaves both facts in the record |
| `expired -> anything` | Expiry is terminal, so win-back reporting is not a guess |
| `trialing -> past_due` | A trial has nothing to fail to pay |

`MUTABLE_STATUSES` — `trialing`, `active`, `past_due`, `grace` — is the set
in which the commercial shape may still change. A cancelled subscription
keeps its access until period end but accepts no new commitments: the
customer has said they are leaving, and billing them for more is a dispute.

### Seven states, mapping one-to-one to entitlement status

`SubscriptionStatus.entitlement_status` is the **single** translation point
between the two vocabularies. The 06 §3.1 sketch also named `unpaid`; it is
represented here by `suspended`, which is the state dunning exhaustion
actually produces and which already carries the read-only semantics products
see.

One-to-one is what makes Task 17's consistency invariants *derivable*:
`test_commercial_status_produces_the_expected_entitlement_status` is
parametrised over the live enum, so a status added tomorrow is covered the
moment it exists.

## 4. Commands

Every command follows the same five-step shape, and the order is
load-bearing:

1. **Authorise** — in the service, not the view
2. **Claim an idempotency key** — inside the transaction
3. **Write durable state** — under a row lock on the subscription
4. **Publish to the outbox and audit** — in the *same* transaction
5. **Notify provisioning** — after the transaction, never inside it

| Command | Idempotency scope |
|---|---|
| `purchase_plan` | `commercial.subscription.purchase_plan` |
| `purchase_bundle` | `commercial.subscription.purchase_bundle` |
| `activate_subscription` | `commercial.subscription.activate` |
| `change_plan` | `commercial.subscription.change_plan` |
| `cancel_subscription` | `commercial.subscription.cancel` |

No external service is called anywhere in that sequence, so no transaction is
ever held across a network round trip.
`test_no_external_call_happens_inside_a_commercial_transaction` asserts no
HTTP client is imported anywhere in `commercial/`.

### Optimistic concurrency

`Subscription.version` is a token. A caller that read the subscription passes
the version it saw; a mismatch raises `ConcurrentModification`. The row lock
serialises concurrent commands; the version check catches a caller acting on
a stale read even when the two never overlap in time.

## 5. Trials

- A trial is a **status**, not a plan called Trial and not a flag on the
  organization. Every code path that handles entitlements handles it.
- Length comes from `PlanVersion.trial_days`.
- Conversion (`activate_subscription`) moves `trial_ends_at` to *now*.
  Leaving it in the future would keep `access_ends_at()` pinned to it and
  expire a paying customer.
- Expiry moves to `expired` — export-only — never deletion. Data is retained
  so a late conversion restores everything, and because deleting on expiry is
  legally risky and commercially fatal to win-backs.

### Abuse prevention

**One trial per product per organization**, derived from durable subscription
history rather than a flag. Items are never deleted, so the history is the
stronger source; a flag is one data-fix away from being cleared.

Signup fingerprinting and per-domain limits remain platform concerns
(06-entitlements.md §7) and are not in Phase 2.

## 6. Upgrade and downgrade

`change_plan(subscription_item_id, to_plan_version_id, timing=None)`.

### A change is not an edit

The outgoing line is closed at the effective moment and a new line opens
there. The history contains both what the customer had and what they moved
to, with the exact instant. An edit would leave only the destination and make
a mid-period invoice impossible to explain.

### Direction is classified, not asserted by the caller

`compare_plan_versions` returns `upgrade`, `downgrade` or `lateral`. The
rule: a change is a **downgrade if anything gets smaller** — fewer seats, a
feature withdrawn, or a quota reduced. Only if nothing shrinks and something
grows is it an upgrade.

Classifying a mixed change as an upgrade would apply it immediately and
silently remove capability the customer is still paying for. Ten parametrised
cases cover it, including the mixed one.

### Timing

| Direction | Default | Why |
|---|---|---|
| Upgrade | **immediate** | The customer asked for more and expects it now |
| Downgrade | **period end** | The current period is already paid for at the higher tier |
| Lateral | period end | Same |

The caller may override, which is what "downgrade me now" in a support tool
needs. A period-end change whose period has already elapsed takes effect
now rather than in the past, because a change scheduled into the past would
be invisible to the effective-dating filter and would never happen.

### The seat conflict policy

> **Task 19: 20 assigned seats downgrading to a 10-seat plan. Do not silently
> corrupt access state. Define a deterministic policy.**

Two rules, both from 06-entitlements.md §6:

**A scheduled downgrade is permitted.** The change is recorded, takes effect
at the period end, and when it does, assignments beyond the new limit are
marked `over_limit_since` — **flagged, never revoked**. Silently locking
someone out because of a billing change produces "I was locked out
mid-invoice" incidents. The organization has until the deadline to choose who
releases a seat.

**An immediate downgrade that would strand seats is refused.** The choice
cannot be made retroactively, so `SeatRemediationRequired` is raised carrying
the assigned count, the target capacity and the period-end date the caller
can schedule for instead.

Which assignments are flagged is deterministic — the most recently assigned
lose first — so a retry flags the same members rather than shuffling who is
affected. The flag clears itself when capacity returns.

### What change_plan refuses

- A bundle component on its own — change the bundle, or unbundle first
- A plan of a different product — that is a purchase and a removal
- The plan version the line is already on

## 7. Cancellation

```python
cancel_subscription(subscription_id, immediate=False, reason_code=…)
```

**`immediate=False` is the default and the normal case.** The customer keeps
access until `current_period_end`, because they paid for it. Revoking on
request is theft and generates chargebacks.

`immediate=True` exists for the cases where it is correct — fraud, a mistaken
signup inside a refund window — and is audited with a reason code.

Cancellation also sets `renewal_policy = none`. Without that, the renewal
sweep would roll the period past the cancellation date and quietly restore
access.

### Access stops at the right instant, in one place

No product checks a date. `compute_entitlements(at)` evaluates every durable
timestamp — `cancel_at`, `ended_at`, `trial_ends_at`, `item.removed_at` — at
a single moment. `test_access_is_computed_at_a_moment_not_from_a_flag`
computes for one minute before and one minute after the cancellation date and
asserts the product appears and then does not.

## 8. Renewal

```
current_period_start, current_period_end, billing_interval, renewal_policy
```

`advance_period` rolls forward repeatedly until the period contains `at`, so
a renewal job that has not run for three months produces the **current**
period rather than one three months stale. It is idempotent, and bounded at
400 iterations — over thirty years of monthly periods, so reaching it is a
data defect rather than a long outage.

`add_months` clamps the day to the target month's length. 31 January plus one
month is 28 February, not 3 March: rolling over shifts every subsequent
anniversary and customers notice.

**No payment is taken and none is recorded.** Phase 2 has no payment
provider, and a renewal that quietly marked itself paid would be the most
dangerous placeholder in the system.

## 9. The system pathway

Trials ending, cancellations taking effect and periods rolling have no actor.
They happen because a clock moved.

That collides with R-10: a tenant context makes the persistence chokepoint
refuse writes for a read-only organization — and the organizations most in
need of these operations are precisely the suspended, non-paying ones whose
subscription must still be cancellable and expirable.

So `commercial/subscriptions/system.py`, and **nothing else in
`commercial/`**, writes subscription state without a tenant context:

```python
unscoped_access(reason=…)      # relaxes application scoping (Layer 2)
organization_db_scope(org)     # satisfies the RLS policy    (Layer 3)
```

Both are required and neither is sufficient. **RLS is not relaxed**: every
write happens inside exactly one organization's database scope, so a bug here
can corrupt one tenant's own record and cannot reach another's.

This is not a general bypass, and three things keep it that way:

1. It is one file, named in the `unscoped` allowlist, so `grep` gives the
   complete list of system writers.
2. Every operation is audited with `ActorKind.SYSTEM` and a reason. An audit
   failure fails the operation.
3. Nothing there takes a caller-supplied status. Each function applies
   exactly one transition, decided by a durable timestamp that has passed.

`test_system_operations_work_for_a_suspended_organization` suspends an
organization and asserts its due cancellation still applies.
`test_system_transitions_are_audited_without_an_actor` asserts the audit
entry carries `SYSTEM`, a null actor and a reason.

### The clock

One scheduled task, `commercial.subscriptions.process_due`, because every
transition it applies has the same cause: a durable timestamp now in the
past. Three separate sweeps of one table for one reason would be three times
the load for no extra information.

It enumerates organizations from the **control-plane** `Organization` table
and queries due subscriptions inside each one's scope. A single cross-tenant
scan is not available — a tenant table matches zero rows without a tenant
variable — and would be forbidden even if it were.
