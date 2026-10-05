# Commissions and payouts

Reasoning and alternatives are in
[ADR-029](../architecture/adrs/ADR-029-commission-ledger-and-payout-model.md).

## Rules

`CommissionRule` rows are versions: `(code, version)` with a rate in basis
points, an optional partner and product scope, `effective_from` and
`retired_at`. Creating a rule under an existing code retires the current
version and inserts the next inside one lock (serialised per code), so exactly
one version is live. Selection prefers the most specific rule: partner+product,
then partner, then product, then the programme default.

## Accrual

A line earns only once its subscription is **activated** - converted from a
trial, or bought without one - and it earns *as of* that moment (final audit
A-03): a trial that lapses unconverted never earns, and a converted trial's
holdback starts at the conversion. For each such priced subscription item sold
to a customer attributed to a partner that held `commission.earn` at the sale
time, accrual computes
`Money.apply_rate(amount, rate_bp)` (integer minor units, half-up), records the
rule and version used, and sets `clears_at = sale + holdback`. The idempotency
key `accrual:subscriptions.item:<item id>` is unique; concurrent or repeated
runs converge on one row. It runs from the commercial change listener, from a
scheduled sweep (`commissions-sweep`), and from `reconcile`; a failure never
affects the sale.

## The ledger

`commissions_accrual` is append-only: no `UPDATE`/`DELETE` for the application
role, and a trigger forbidding both for any other role. Entries are `accrual`,
`reversal` (negative, refers to the entry it reverses, reason from a closed set:
cancellation, refund, chargeback, downgrade correction, correction) and
`adjustment` (signed, staff-supplied reason). A reversal may not exceed what is
left of the original, and reversing the same entry twice is one entry (advisory
lock plus idempotency key). Balance = sum of entries.

## Reconciliation

`GET /admin/commission/reconcile` (and the matching management path) recomputes
what the ledger should contain from the sales and rules and reports `missing`,
`duplicates`, `wrong` (computed under a different rule/amount) and `unexpected`.
It writes nothing; repair is a deliberate accrual or adjustment.

## Payouts

`draft -> ready -> approved -> paid`, or `cancelled` from any non-final state.

| Step | Who | Guard |
|---|---|---|
| prepare | `staff_payout.manage` | Server selects the partner's cleared, unpaid entries up to the period end; totals snapshotted |
| ready | `staff_payout.manage` | Version check |
| approve | `staff_payout.approve` (a different person than the preparer, and than the author of any manual entry in the batch) | AAL 2, reason, totals **re-derived from the lines** must equal the snapshot, and no entry may have been reversed since preparation (cancel and prepare again) |
| paid | `staff_payout.manage` | AAL 2; requires an external reference; records who and when |
| cancel | `staff_payout.manage` | AAL 2, reason; entries become payable again |

A ledger entry is in at most one live payout (partial unique index on
`PayoutLine`). A terminated partner can still be settled (money earned before
termination remains owed); a suspended partner's payouts are held. **No payment is made by this platform and no bank details are
stored.** A payout is in the partner's payout currency, and only ledger entries in that
currency are eligible for it. A payout's key (`Idempotency-Key`) replays the
same payout, and is refused if reused for a different partner or period.

## Partner view

A partner sees its own ledger, balance per currency and payouts (keyset
paginated), never another partner's, and cannot alter anything. A terminated
partner keeps the read-only history permissions.
