# Concurrency and failure behaviour

## Races, and what settles each

Every one of these has a test that runs the contenders on real threads with real
`COMMIT`s (not one transaction), and asserts the end state.

| Race | Settled by |
|---|---|
| Eight workers accrue the same sale | Unique idempotency key on the ledger; losers roll back a savepoint and count a duplicate |
| The same sale event delivered twice | Same key |
| Two staff reverse the same accrual | Advisory lock per accrual + idempotency; total reversals can never exceed the original |
| Two staff prepare a payout for one partner | Advisory lock per partner; partial unique index on live `PayoutLine` |
| One prepare request sent twice at once (same key) | The key is read under the partner lock: the second waits and replays the first |
| Approve vs. a reversal of an entry in the batch | Approval refuses a batch with a reversal recorded since preparation; a reversal after approval nets into the next payout |
| Prepare vs approve vs cancel on one payout | Row `version` check under `SELECT ... FOR UPDATE` |
| Approver equals preparer | Service check and database constraint |
| Provisioning replayed / double-clicked | Idempotency ledger; one organization, one link |
| Sale vs. ending the partner link | `lock_active_link`: the sale sees the link or is refused |
| Two updates to one partner | `expected_version` - the second gets 409 |
| Grant vs. revoke of one capability | Row lock; last write is coherent, never both live |
| Two revocations of the last security administrator | Ordered locks over all holders; one is refused |
| Two partners verify one hostname | Partial unique index on verified/active hostnames; the loser gets `hostname_claimed` |
| Verify vs. activate a domain | Row lock + state check |
| Rule versions created concurrently | Advisory lock per rule code; one live version |

## Failure behaviour

| Dependency fails | Behaviour |
|---|---|
| Commission accrual raises | The sale stands. The failure is counted (`commission_accrual_failures_total`, bounded labels) and logged; the sweep or `reconcile` produces the row later |
| `CustomerSummary` write fails | Provisioning still succeeds; the directory shows the last summary and its age; the next reconciliation repairs it |
| Event outbox unavailable | Partner and payout writes still commit; events are delivered later (transactional outbox) |
| DNS times out or errors | `verify` reports `lookup_failed` and changes nothing; a *scheduled* re-check skips the domain - an outage is never a miss. Only an answer without the proof (NXDOMAIN, record removed) is a miss, and three consecutive misses disable an active domain (tested against a real DNS server) |
| Files scan not finished | The logo is not attachable until `clean`; branding without it works |
| Rate-limit cache unreachable | Per-process counters (D-012) |
| Redis/broker down | Scheduled jobs resume when it returns; every job is idempotent and safe to run twice |

## Jobs

`commissions-sweep`, `commissions-prune-referrals` and `partners-recheck-domains`
are Celery beat entries (all three on the `heavy` queue - the domain re-check was moved there by the
final audit so its DNS lookups never hold a worker that subscription
due-processing needs). Each builds its **own trusted
context** (`platform_context`) - it never runs under a request's or a user's - and
enters an organization's scope only to touch that organization's rows. Each is
idempotent and bounded per run.

## Transactions

Following D-016, every service in `partners`, `commissions` and `controlplane`
either validates before its first write or raises from inside the `atomic()`
block that made the write, so a 4xx never leaves half a change. Foreign keys the
client controls (organization, plan version, partner, domain, referral, file) are
checked with tests that **commit** for real, because a savepoint-wrapped test
cannot see a deferred constraint fire.
