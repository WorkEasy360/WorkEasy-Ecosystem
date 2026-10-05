# Phase 8 — Operations

## 1. Work, queues and timers (Tasks 91-93)

| Work | Where | Bound |
|---|---|---|
| synchronous AI call | web request | 30 s deadline; the request thread waits on the provider, so sync is for interactive calls and long outputs use async |
| async AI job | `ai_gateway.run_execution`, **heavy** | 120 s deadline; soft/hard task limits above it |
| document indexing | `rag.index_version`, **heavy** | 60 s per embedding batch; 5 attempts with backoff |
| source purge | `rag.purge_source`, **heavy** | bounded batches (`RAG_PURGE_BATCH`) |
| analytics projection | `analytics.project`, **heavy** | 500 facts per transaction, per organization |
| retention | `intelligence.prune`, **heavy**, daily beat | per organization, bounded batches |

Nothing in Phase 8 runs on the critical queue (fitness test
`test_intelligence_work_never_runs_on_the_critical_queue`), so an AI or
indexing backlog cannot delay authentication, provisioning or webhooks.

Every background unit is a **due-work timer** (Phase 6 `scheduling.DueWork`)
before it is a message: `ai.execution` (the lease of every reservation),
`rag.index`, `rag.purge`, `analytics.project`. The message is an optimisation;
the timer sweep re-dispatches anything whose message was lost, so a broker
outage delays work and never loses it.

## 2. Failure matrix (Tasks 94-100)

| Failure | Effect | Why it is safe |
|---|---|---|
| **AI provider down / 5xx / 429** | bounded retries within the deadline, then the breaker opens; permitted fallback; otherwise `503 ai_provider_unavailable` with `Retry-After` | reservation released at the actual (failed calls cost zero tokens); the rest of the platform is untouched — readiness never includes AI (`test_ai_health_never_gates_readiness`) |
| provider read timeout | not retried; `503` | the vendor may have billed; the execution is charged what the vendor reported, or its reservation if it never answered |
| provider credential missing / invalid | `503`, no network call if missing; a 401/403 trips the breaker and is not retried | a misconfiguration cannot become a retry storm |
| embedding provider down | versions `retrying`, then `failed`; the serving version keeps serving, `stale: true` | never reported indexed (`test_an_embedding_outage_is_never_reported_as_indexed`) |
| **Redis down** | rate limits degrade to per-process windows (D-012) | quotas and concurrency are in PostgreSQL and stay exact; nothing in Phase 8 stores state in Redis (`test_a_cache_outage_degrades_rate_limits_never_spend_control`: AI answers, the limit still fires per process, the quota refuses the second request exactly) |
| **RabbitMQ down** | async jobs, indexing and projection wait; `202`s are still returned | the timer re-dispatches when the broker returns (`test_a_job_queued_during_a_broker_outage_runs_from_its_timer`); sync AI calls and queries are unaffected |
| worker killed mid-job | the lease expires; the timer settles the execution `expired` at its full reservation, or re-claims the index version (attempt n+1) | no double charge, no duplicate chunks (`test_a_crashed_callers_reservation_is_settled_at_its_full_amount_once`, `test_a_worker_that_dies_mid_index_is_recovered_without_duplicates`) |
| **PostgreSQL down** | every Phase 8 request fails closed (`503`) | no spend without a reservation (the cost-failure policy, [02-cost-and-quota.md](02-cost-and-quota.md) §5) |
| pgvector missing | the RAG migration refuses to apply | the operator installs it first (§5) |
| projection bug or lost projection | aggregates lag; answers report it in `freshness` | `w360_analytics_aggregates --rebuild` from facts |
| organization suspended | new AI work, indexing and product queries refused; in-flight executions still settle; deletions and revocations still purge | metering and RAG tables are write-block exempt (D-023) |

## 3. Observability (Tasks 101, 102)

Metrics (bounded labels only — no organization, execution, document, skill,
source or metric id):

| Metric | Labels | Watch for |
|---|---|---|
| `ai_requests_total` | operation, outcome, provider | outcome ≠ succeeded ratio |
| `ai_provider_latency_seconds` | provider, operation | p95 against the deadline |
| `ai_provider_failures_total` | provider, category | any `auth`: a credential problem |
| `ai_breaker_opened_total` | provider | any |
| `ai_fallbacks_total` | provider | a sustained rate: the primary is degraded |
| `ai_tokens_total`, `ai_estimated_cost_micros_total` | provider, kind | spend trend |
| `ai_reservations_total` | operation, result (`reserved`, `quota_*`, `concurrency`, `replayed`) | quota refusals per hour |
| `ai_reservations_expired_total`, `ai_late_completions_total` | — | any: workers are dying or deadlines are wrong |
| `ai_invalid_outputs_total`, `ai_citations_dropped_total` | — | a skill or model regression |
| `ai_redactions_total` | where | a product sending secrets |
| `rag_versions_indexed_total`, `rag_versions_failed_total`, `rag_index_retries_total`, `rag_embedding_failures_total` | — | failed > 0 |
| `rag_indexing_duration_seconds`, `rag_retrieval_duration_seconds`, `rag_retrieval_results` | — | retrieval p95 > 250 ms: see [05-rag.md](05-rag.md) §9 |
| `analytics_facts_recorded_total`, `…_projected_total`, `…_rejected_total{reason}` | reason | rejected by reason: a product publishing the wrong shape |
| `analytics_projection_lag_seconds`, `analytics_ingestion_lag_seconds` | — | p95 > 5 min |
| `analytics_query_duration_seconds`, `analytics_query_denied_total{reason}` | reason | — |

Logs are structured (`ai.execution.settled`, `ai.provider.failed`,
`ai.breaker.opened`, `ai.fallback`, `rag.document.submitted`,
`rag.version.indexed`, `analytics.projected`…) with `request_id`,
`correlation_id`, product and organization ids — never content (06 §4).
`/health/deep` reports `ai_providers` (credential configured, circuit state);
it never gates readiness.

**Suggested alerts.** Breaker open > 10 min; `ai_reservations_expired_total`
increasing; `rag_versions_failed_total` increasing; projection lag p95 > 5
min; `ai_provider_failures_total{category="auth"}` > 0; heavy-queue depth.

## 4. Runbooks

**A provider is failing.** Check `/health/deep` and `ai_provider_failures_total`
by category. `auth`: rotate `W360_AI_CREDENTIAL_<REF>` in the secret manager —
read per call, no restart. `unavailable`/`rate_limited`: the breaker already
sheds load; to stop using the vendor, disable it in Super Admin → AI (reason,
AAL2). Traffic moves to a permitted fallback or fails fast with `503`.

**An organization reports `ai_quota_exceeded`.** Settings → AI shows which
scope (`organization`/`product`) and when it resets. The plan's feature sets
the product quota; the administrator's budget and the platform cap set the
organization's; staff can raise the platform cap for one organization (audited).

**Reservations are expiring.** Workers are dying or the heavy queue is
starved: check worker health and queue depth. Each expiry is charged in full
by design; nothing needs repair.

**Documents stuck `retrying`/`failed`.** `last_error` on the status API gives
the reason code: `embedding_unavailable` or a provider failure category
(outage), `ai_quota_exceeded` / `ai_concurrency_limited` (spend or in-flight
limit), `ai_not_entitled` / `ai_disabled` (AI switched off or not granted),
`too_many_chunks` / `empty` (the content itself; never retried).
Fix the cause; the product resubmits (a new version label) to retry a
`failed` version.

**Aggregates look wrong.**
`manage.py w360_analytics_aggregates <org-uuid> [--metric KEY]` lists drift;
add `--rebuild` to repair from facts. Safe on a live system.

**Changing the embedding model.** Add and price the new model (created
disabled), enable it, disable the old one. Documents report `stale` until
their products resubmit them; retrieval never mixes vector spaces.

## 5. Upgrade and rollback (Tasks 110-113)

**Prerequisite: pgvector.** The `vector` extension must exist in the
database before `rag.0001` runs, and creating it needs a superuser (or a
`CREATE` privilege on the database with the extension trusted). The
application role is deliberately not a superuser, so this is a one-time DBA
step:

```sql
-- as a superuser, once per database (and in template1 for new databases)
CREATE EXTENSION IF NOT EXISTS vector;
```

Development: `docker-compose.yml` uses `pgvector/pgvector:pg16-trixie` and
`infra/postgres/init.sql` creates the extension; an existing development
volume needs the statement above run once. CI: the service image is the
pgvector image and the workflow creates the extension before migrating.
Managed PostgreSQL (RDS, Cloud SQL, Azure) ships pgvector ≥ 0.5; the
platform uses only `vector`, `<=>` and exact search, available in all of them.
Without the extension the migration stops with an instruction pointing here —
it never half-applies.

**Order.** Take a backup → create the extension → deploy → `migrate` (Phase 8
migrations only add tables, policies, grants and seed rows; they alter no
Phase 1-7 table) → verify `/health/deep` → configure
`W360_AI_CREDENTIAL_ANTHROPIC` / `_OPENAI` in the secret manager (until then
AI answers `503` and nothing else is affected) → grant AI in plans by adding
the `<product>.ai.monthly_budget_micros` feature.

**Rollback.** Phase 8 is additive and off by default: no organization has AI
until a plan grants the feature, and no product uses it until it calls the
new endpoints. To roll back the code, deploy the Phase 7 build — it ignores
the new tables. To remove the schema as well, migrate back
(`tenancy 0007`, `authz 0006`, then `rag zero`, `analytics zero`, `ai zero`) — rehearsed by `scripts/rehearse_phase8_upgrade.py`, which also
proves Phase 1-7 data is byte-identical before and after. The extension may
stay installed.
