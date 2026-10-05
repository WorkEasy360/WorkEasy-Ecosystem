# Performance

Measured on a developer machine: PostgreSQL 16 in Docker, one Django process,
the real HTTP stack (session, CSRF, tenancy, authorization, serialization), warm
cache. Numbers are **local shapes, not capacity claims** - they show what each
request costs and, more importantly, that the cost does not grow with the data.
No load test was run (R6-6 still applies).

The test is `backend/tests/phase7/test_phase7_performance.py`
(`pytest -s -m slow backend/tests/phase7/test_phase7_performance.py` reprints the
table). Three properties are asserted for every list:

1. **The query count is identical at 10, 100 and 500 rows** (no N+1).
2. **The page's own SQL, captured from the real request, has an index-served
   plan**: run under `EXPLAIN` with sequential scans disabled, it must not need a
   `Seq Scan` of the big table. (At these sizes the planner correctly prefers a
   sequential scan, so the unforced plan proves nothing; disabling it proves an
   index *exists that can serve the query*.)
3. A page of 100 costs the same number of queries as a page of 25.

## Results (median of 7 warm calls, page size 25)

| List | Queries at 10 / 100 / 500 rows | p50 at 10 / 100 / 500 rows |
|---|---|---|
| Admin customer directory | 10 / 10 / 10 | 14.6 / 15.9 / 16.7 ms |
| Admin partner list | 7 / 7 / 7 | 25.8 / 13.6 / 16.5 ms |
| Admin commission ledger | 7 / 7 / 7 | 11.3 / 11.1 / 13.5 ms |
| Partner commission ledger | 11 / 11 / 11 | 24.9 / 36.8 / 17.6 ms |
| Partner customer list | 11 / 11 / 11 | 22.7 / 26.2 / 33.0 ms |
| Admin payout list | 7 / 7 / 7 | 17.6 / 23.9 / 24.4 ms |

Most of each request's queries are the fixed cost of being authenticated and
authorized: the session, the user, the staff grants or partner membership and
capabilities (read fresh every request, deliberately uncached so revocation is
immediate), and the audit-safe context. The page itself is one query, plus one
batched lookup for the rows it shows (owners, or the partner's link state) -
never one per row.

The variation between the sizes (for example 36.8 ms at 100 partner-ledger rows)
is noise from a shared laptop, not a trend: the 500-row figure is lower.

## What the measurement found

Three lists had no index for their own ordering and would have sorted the whole
table for every page at scale. All three are fixed (migrations
`organizations.0004`, `commissions.0003`, `partners.0002`):

| Table | Ordering | Index added |
|---|---|---|
| `organizations_organization` | newest first, keyset | `ix_org_created_id (created_at desc, id desc)` |
| `commissions_payout` | newest first, unfiltered | `ix_payout_created_id (created_at desc, id desc)` |
| `partners_account` | newest first | `ix_partner_created_id (created_at desc, id desc)` |

The ledger and the partner customer list were already index-served
(`ix_accrual_partner` on `(partner, event_at desc, id desc)` and
`ix_partnerlink_portfolio` on `(partner, status, started_at desc, id desc)`).

## Not indexed, on purpose

- **Customer and partner text search** (`icontains` on name, slug, code) is an
  unindexed scan, bounded by a 100-character cap and by keyset paging. It is
  correct and fast at today's scale. Past roughly 50,000 organizations, add a
  `pg_trgm` GIN index on `display_name` and `slug`; the failing signal would be
  the directory's search latency, not its browse latency.

  Measured by the final audit (`tests/phase7/test_final_audit_search_scale.py`,
  selector level, median of 7 warm calls; a *miss* is the worst case, since the
  keyset walk must visit every row):

  | Organizations | Queries | Search miss | Search hit | Browse |
  |---|---|---|---|---|
  | 100 | 1 | 3.8 ms | 6.1 ms | 9.9 ms |
  | 500 | 1 | 4.0 ms | 8.0 ms | 7.5 ms |
  | 5,000 | 1 | 5.3 ms | 6.2 ms | 5.3 ms |

  Not pathological in the primary admin flow at these sizes: it stays P3.

## Background work

`commissions-sweep`, `commissions-prune-referrals` and `partners-recheck-domains`
run under Celery beat (hourly, daily and every six hours), each under its own
trusted context, each idempotent; all three use the `heavy` queue (the domain
re-check makes up to 200 DNS lookups of several seconds each, which must never
hold a worker that subscription due-processing is waiting for). The sweep accepts a `limit`; referral
pruning deletes in batches of 1,000 per call; the domain re-check takes a `limit`
(default 200). Accrual is O(sales without a ledger row), not O(ledger).
`reconcile` is O(organizations checked) and runs on demand (`GET
/admin/commission/reconcile`); it is the one operation to schedule off-peak at
large scale.

## Upgrade cost

The Phase 6 -> 7 migration set adds tables and three indexes. The only data
migration touches price books (`retire_orphan_partner_books`), a table with
dozens of rows. The customer summary starts empty and is filled by the existing
hourly `entitlement-reconcile-all`; until then the directory shows blanks for
subscription and seats ("as of the last reconciliation" is null). Run the
reconcile once after deploy to avoid that window (see the upgrade runbook in
[phase-7-status.md](phase-7-status.md)).
