# Phase 8 — AI entitlement, spend and concurrency

## 1. Who may use AI (Task 7)

`policy.authorize(organization, product)` — taken before anything is reserved,
retrieved or sent:

1. **The organization exists and accepts new work.** Its live status, read
   from the control plane; suspended or read-only means `ai_disabled`.
2. **The product is entitled to it, for writes** — the same decision the
   entitlement API and event ingestion use (`verification.verify(write=True)`),
   so AI can never be more permissive than the product. An unknown
   organization and an unentitled one are the same `ai_not_entitled`.
3. **The plan grants this product AI.** The feature
   `<product>.ai.monthly_budget_micros` is sold through the ordinary catalog
   and resolved most-generous-wins like every feature. Absent, `0` or not an
   integer → `ai_not_entitled`; `-1` → no product-level limit (the
   organization's cap still applies). **No product gains AI merely by reaching
   the gateway.**
4. **AI is not switched off** by the organization (`ai_enabled`) or the
   platform (`platform_suspended`) → `ai_disabled`.

A platform failure reading the entitlement is `503 temporarily_unavailable`,
never a denial.

## 2. Limits (Tasks 10, 35, 54, 57, 58)

| Limit | Default | Enforced |
|---|---|---|
| organization monthly cap | `min(administrator's budget, platform cap)`; platform cap 50.00 USD (`AI_DEFAULT_ORG_MONTHLY_CAP_MICROS`) or staff-set | database, at reservation |
| product monthly quota | the plan's `<product>.ai.monthly_budget_micros` | database, at reservation |
| concurrent requests per organization | 8 (`AI_MAX_CONCURRENT_PER_ORG`) — queued jobs included | database, under the organization row lock |
| request rate | 1,200/min per product, 300/min per (product, org), 120/min per (product, org, skill) | cache (degrades, §5) |
| input per request | 64 KiB; the whole prompt ≤ 100,000 estimated tokens and the model's context | before reservation |
| output per request | the skill's `max_output_tokens` ≤ 4,096 and the model's | in the request |
| retrieval per request | `top_k` ≤ 20, query ≤ 2,000 chars, ≤ 20 sources, context ≤ 24,000 chars | before retrieval |

An "unlimited" plan is never unlimited spend: the platform cap applies to
every organization.

## 3. Reservation and settlement (Tasks 11, 59)

```
reserve   (one transaction, organization scope)
  lock this month's budget rows: organization (scope ""), then product
  count in-flight executions            -> ai_concurrency_limited
  spent + reserved + estimate <= limit  -> ai_quota_exceeded (per scope)
  reserved += estimate on both rows
  insert the execution (reserved | queued) + its due-work timer at lease expiry
call      (no transaction)
settle    (one transaction)
  lock the execution; it must still be reserved/running (fenced)
  lock the same budget rows
  reserved -= reservation; spent += actual; requests, tokens += actual
  usage lines, result (if any), timer removed
```

- **The estimate is the worst case of every permitted candidate**: the
  trusted instructions and input, the largest context retrieval could add, the
  full output budget, times the structured-output re-asks, plus the query
  embedding, at each candidate's current price. A fallback can never run
  unreserved.
- **Race-free.** The limit check and the increment are under the same row lock:
  eight concurrent requests against one request's budget reserve exactly one
  (`test_eight_requests_against_one_requests_budget_reserve_exactly_one`); a
  burst of eight against a cap of two gets exactly two
  (`test_the_per_organization_concurrency_cap_holds_under_a_burst`, with the
  provider's own high-water mark as evidence).
- **Crash-safe.** A caller that dies after reserving leaves a row and a timer.
  At lease expiry (120 s sync, 300 s async) the timer settles it `expired` at
  the **full reserved amount** — the provider may have billed, so the
  platform assumes it did. A late settle from the dead caller finds the
  execution final and changes nothing (`ai_late_completions_total`). A queued
  job that never started is released at zero: no provider was called.
- **Idempotent.** `idempotency_key` is unique per (organization, product). A
  retry gets the same execution: its stored result if it succeeded (kept 15
  minutes sync, 24 hours async), `409` while it runs, its outcome if it
  failed — never a second charge. The same key with a different request is a
  conflict (SHA-256 request fingerprint).

## 4. Cost (Task 9)

Estimated AI infrastructure cost is integer **micro-units of USD**
(`AI_ACCOUNTING_CURRENCY`): `ceil((in × in_price + out × out_price) / 10⁶)`
per provider call, at the **price version pinned to the call**. Prices are
immutable versions (a trigger refuses `UPDATE`); a change is a new version
from staff, and history is never re-priced. Every provider call — including a
failed one, with zero tokens — is a usage line, so "the vendor returned 429
three times" is in the record.

**This is not billing.** It is a spend-control figure the budget is enforced
in. No invoice is derived from it; `commercial/` cannot import `intelligence/`
(import-linter), and every screen that shows it says so.

## 5. The cost-failure policy (Tasks 12, 100)

**Fail closed.** If the reservation cannot be recorded — the database is
unavailable — the request is refused (`503`). There is no emergency allowance:
the accounting database is Tier 0, and when it is down the platform is down
with it, so an allowance would buy nothing but unmetered spend.

**Redis.** The rate limits in front of the gateway use the Phase 1 throttle:
with the cache down they degrade to a per-process window — each web process
admits up to the limit on its own, so the effective limit is multiplied by the
number of processes for the outage. **The quota and the concurrency cap never
touch Redis**: spend and in-flight counts stay exact through a cache outage.

## 6. Retention of the metering record

Execution metadata and usage lines: 400 days (`AI_EXECUTION_RETENTION_DAYS`),
then deleted with the execution. Budget periods: 36 months. Results: until
`expires_at`. See [06-security-and-privacy.md](06-security-and-privacy.md).
