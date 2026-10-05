# ADR-029: Commission is an append-only ledger derived from sales; payouts move no money

**Status:** Accepted
**Date:** 2026-09-24
**Deciders:** Architecture
**Depends on:** [ADR-028](ADR-028-partner-capability-model.md)

---

## Context

Partners earn commission on customers' sales. Money errors are the costly kind
of bug, and payment is regulated activity this platform has not been approved to
perform. We need a record that is correct under concurrency, auditable after the
fact, and honest about what the platform does not do.

## Decision

**The ledger.** `CommissionAccrual` is append-only, enforced twice: the
application role has no `UPDATE`/`DELETE`, and a trigger (`make_append_only`)
raises for anyone else. Kinds are `accrual`, `reversal` and `adjustment`. A wrong
entry is corrected by a **new** entry that refers to it; a partner's balance is
the sum. Amounts are integer minor units in the partner's payout currency; rates
are integer basis points; arithmetic is `Money.apply_rate` (Decimal,
`ROUND_HALF_UP`). No float exists.

**Derived, never supplied.** An accrual is computed from the durable, priced,
trigger-frozen `SubscriptionItem`, the customer's attribution, the partner's
capability history and the rule in force **at the sale's time**. No partner,
customer or browser supplies an amount, a rate or a partner id.

**After the sale, idempotent.** Accrual runs in a change listener, a scheduled
sweep or `reconcile` - never inside the sale's transaction, so a commission fault
can never fail a purchase. The ledger key `accrual:subscriptions.item:<id>` is
unique: racing workers converge on one row. `reconcile` re-derives what should
exist and reports missing, duplicated, mis-ruled and unexpected rows without
rewriting anything.

**Versioned rules.** `CommissionRule` rows are immutable versions; a change
retires one and inserts the next. Selection is most-specific-first and pure
(`domain.select_rule`). Each accrual records the rule code and version it used.

**Holdback.** An accrual is not payable until `clears_at`
(`W360_COMMISSION_HOLDBACK_DAYS`).

**Attribution: first touch.** A referral records the first partner a visitor
arrived through; later codes do not replace it. The rule name is stored on the
converted referral so a future change cannot reinterpret history.

**Payouts.** A payout is a reviewed batch: `draft -> ready -> approved -> paid`
(or `cancelled`). Totals are snapshotted when prepared and **re-derived from the
lines** before approval; a mismatch refuses approval. A ledger row can be in at
most one live payout (partial unique index), so a retry or race cannot pay a
commission twice. The preparer and approver must differ (checked, and refused by
the database); approval needs its own permission, AAL 2 and a reason.

**No money moves.** `paid` is reachable only by staff supplying an external
reference. Nothing here contacts a bank or processor, and no bank details are
stored. The API and UI say so.

**Dual approval: NO.** Volume is low and the flow is manual; separation of
duties (different preparer and approver) plus AAL 2, a reason and audit is the
control. Adopt two-signature approval if payment execution is ever automated, a
single payout exceeds a configured threshold, or a second approver is routinely
unavailable.

## Consequences

- Concurrency is settled by unique indexes and advisory locks (reversal, rule and
  payout serialisation), not by care.
- A partner sees their own ledger, balance and payouts only; staff see all;
  neither can edit an entry.
- Real payment (a later phase) must be added *behind* `paid`, as a state reached
  by a verified provider event. It will not change the ledger.

## Alternatives rejected

- **A mutable balance column.** Unauditable and race-prone.
- **Accrual inside the sale transaction.** Couples purchase success to
  commission correctness.
- **Storing the rate on the partner.** Loses which rate a past sale used.

## Amendment (Phase 7 final audit, 2026-09-24)

- **A sale is the activation of a paid subscription, not the opening of a
  line.** A line earns only once its subscription is activated, and as of that
  moment (attribution, capability, rule and holdback); a trial that lapses never
  earns. The platform had been accruing on trials it knew never converted.
- **Approval refuses a stale batch.** A payout with an entry reversed outside the
  batch (after preparation, or clearing after its period end) cannot be approved;
  cancel and prepare again, which nets the reversal.
- **Separation of duties covers the money, not only the batch:** neither the
  preparer nor the author of any manual ledger entry in the payout may approve it.
- A terminated partner can be settled; a suspended one is held.
- The ledger refuses `TRUNCATE` for every role, as it already refused `UPDATE`
  and `DELETE`: maintenance is an explicit, visible trigger disable.
- Dual approval remains unnecessary; the triggers above are unchanged.
