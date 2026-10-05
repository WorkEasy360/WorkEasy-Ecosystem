# Phase 8 — Status

**PHASE 8 STATUS: PASS**

Workeasy360 now gives independently deployed products shared AI (skills through
one gateway), retrieval over their own indexed knowledge, and analytics over
their own events — entitled, metered, spend-capped, tenant- and
product-isolated, audited, failure-isolated — without the platform owning any
product's business meaning. The platform remains platform only.

## Gates

| Gate | Result |
|---|---|
| backend `pytest` (full suite) | **2,145 passed, 0 failed** (50 min) |
| `ruff check` / `ruff format --check` | pass / pass (513 files) |
| `mypy` (contracts, strict) | pass (28 files) |
| `bandit -ll` | no issues |
| `pip-audit --strict` | no known vulnerabilities |
| `lint-imports` | 17 contracts kept, 0 broken |
| architecture tests | pass (Phase 1-8 fitness suites) |
| `manage.py check --fail-level WARNING` | no issues |
| `check --deploy` | enforced by `test_security_baseline` (pass) |
| `makemigrations --check` | no changes |
| frontend: clean `npm ci`, `next typegen` + `tsc`, `eslint`, `vitest`, `next build`, `npm audit` | pass; 86 tests; 0 vulnerabilities |
| production CSP / browser / axe | 12 pages: 0 CSP violations, 0 console errors, 0 axe violations, focus visible, no 320px scroll ([08-frontend.md](08-frontend.md) §5) |
| fresh database | 16/16 (`scripts/rehearse_phase8_upgrade.py`) |
| Phase 7 → 8 upgrade + rollback | 23/23 |
| real concurrency | `tests/phase8/test_concurrency.py` (9 races, real threads and commits) |
| provider failure injection | `tests/phase8/test_provider_failures.py` |
| Redis failure | `test_a_cache_outage_degrades_rate_limits_never_spend_control` |
| RabbitMQ failure | `test_a_job_queued_during_a_broker_outage_runs_from_its_timer` |
| RAG cross-tenant | `test_the_nearest_passage_in_another_tenant_is_never_returned` (+ product, tag isolation) |
| analytics cross-tenant | acceptance flow step 7, `test_guessing_another_products_metric_key_learns_nothing` |

## Acceptance flows

- **AI** (`tests/phase8/test_ai_acceptance.py`, 13 steps): product X
  authenticates; Org A is entitled with AI in its plan; the skill, input and
  quota are checked; an indexed document is retrieved for Org A / product X
  only; the approved model answers; citations carry stored provenance; usage
  and cost are recorded; an idempotent retry is not charged twice; Org B cannot
  retrieve Org A's source; product Y cannot invoke X's private skill; a
  provider outage is a bounded 503 while the platform keeps serving.
- **Analytics** (`tests/phase8/test_analytics_acceptance.py`, 10 steps):
  versioned event published; authentication and organization binding; the
  duplicate is idempotent; the aggregate is correct; a late event corrects its
  historical bucket exactly once; Org A queries the approved metric; Org B
  cannot; product Y cannot query X's private metric until shared; a rebuild
  reproduces the values; no product database is queried.

## Report

1. **Backend tests** — 2,145 passed, 0 failed (full suite, one run). 220 are
   Phase 8's: `tests/phase8/`
   (acceptance, gateway, concurrency, RAG, analytics, provider failures, HTTP
   org/staff, units, performance), `tests/architecture/test_phase8_architecture.py`
   and `tests/isolation/test_tenant_task_concurrency.py`.
2. **Frontend tests** — 86 vitest tests (8 files) incl. 10 for the Phase 8
   screens with axe per state; real-browser check above.
3. **Models/tables** — 17. Control plane (no tenant, app DML only): `ai_provider`,
   `ai_model`, `ai_model_price`, `ai_skill`, `ai_skill_version`, `analytics_metric`.
   Tenant (RLS forced, composite FKs): `ai_org_policy`, `ai_budget_period`,
   `ai_execution`, `ai_usage_line`, `ai_execution_content`, `rag_source`,
   `rag_document`, `rag_document_version`, `rag_chunk`, `analytics_fact`,
   `analytics_aggregate`. Freeze triggers on prices, skill versions, metric
   versions, facts, chunks, usage lines and execution/version identity.
4. **Migrations** — `ai.0001`, `ai.0002` (seed registry), `rag.0001` (refuses
   without pgvector), `analytics.0001`, `authz.0007` (role grants),
   `tenancy.0008` (RLS, grants, composite FKs, freeze triggers). All reversible
   (rehearsed).
5. **APIs** — 23 routes. Product (client credential, CSRF-exempt, discovered):
   `integrations/ai/{skills, skills/retire, invoke, requests/status}`,
   `integrations/rag/{documents, documents/delete, documents/status,
   sources/revoke, retrieve}`, `integrations/analytics/{metrics, query}`.
   Organization: `orgs/<slug>/ai/{settings, usage, requests}`,
   `orgs/<slug>/analytics/{metrics, query}`. Staff:
   `admin/ai/{registry, providers/<key>/status, models, models/<key>,
   models/<key>/prices, organizations/<id>, organizations/<id>/policy}`.
6. **Frontend routes** — `/o/<slug>/settings/ai`, `/o/<slug>/settings/analytics`,
   `/admin/ai`.
7. **AI gateway** — one entry point; checks in a fixed order, each refusal free;
   worst-case reservation before any call; one deadline; bounded retries;
   per-model breaker; permitted fallback only ([01](01-ai-gateway.md)).
8. **Provider registry** — providers with adapter, credential reference and
   descriptive data policy; two adapters (Anthropic Messages, OpenAI) over
   direct HTTP through egress; no caller-supplied endpoint; production pins
   overrides empty.
9. **Model registry** — models with status, tier, capabilities, limits,
   priority, breaker state; immutable price versions; new models created
   disabled; seeded Opus 5 active, Sonnet 5 / Haiku 4.5 disabled, embeddings
   active.
10. **Credential architecture** — `credential_ref` → `W360_AI_CREDENTIAL_<REF>`
    from the secret manager, read per call, one header only; never in a column,
    config JSON, log, response, prompt or the frontend; products never receive
    one (fitness tests).
11. **AI entitlement policy** — organization active; product entitled for
    writes (the same decision as the entitlement API); plan feature
    `<product>.ai.monthly_budget_micros`; organization and platform switches.
    Unknown and unentitled are one answer ([02](02-cost-and-quota.md)).
12. **Usage/quota architecture** — monthly budget rows per organization and
    product, locked in a fixed order; in-flight cap per organization;
    lease timer per reservation; idempotency keys with request fingerprints.
13. **Cost metering** — integer micro-USD at the price version pinned to each
    call; a usage line per provider call including failures; estimated cost,
    never billing (`commercial/` cannot import `intelligence/`).
14. **Prompt registry** — the skill version *is* the prompt record: immutable
    by trigger, hashed, versioned; platform skills defined in code and
    registered, drift logged not applied ([03](03-skills-and-prompts.md)).
15. **Product skill registry** — `product.<code>.*` namespace from the
    credential; deny-by-default invocation; explicit `shared_with`; retirement.
16. **Structured-output validation** — bounded schema subset (no `$ref`, no
    `pattern`), vendor-safe copy for native structured output, full validation,
    one bounded re-ask, then `502` with nothing stored.
17. **RAG document model** — source → document → version → chunk, tenant-
    scoped with composite keys; content held only until indexed ([05](05-rag.md)).
18. **Ingestion** — remote contract only; idempotent by (document, version)
    under a row lock; conflicting content is `409`; secrets redacted before
    storage.
19. **Chunking** — one deterministic strategy; size bounds enforced at
    ingestion so a document costs at most 256 embeddings.
20. **Embedding abstraction** — the registry's embedding model through the
    same adapter/egress/metering path as generation; model and dimensions on
    every chunk.
21. **Vector storage** — pgvector in the platform's PostgreSQL, untyped
    `vector`, exact kNN; no vector database; HNSW trigger documented.
22. **Tenant retrieval isolation** — organization predicate + RLS inside the
    ranking statement; nearest-neighbour adversarial test across tenants.
23. **Product retrieval isolation** — calling product from the credential;
    own sources only; access tags applied before ranking; deny by default.
24. **Citation/provenance** — platform-assigned ids for supplied passages
    only; stored source/document/version/chunk/title/https URL; invented ids
    dropped and counted.
25. **Prompt-injection controls** — no tools; delimiters neutralised; trusted
    preamble; nothing reads model output as state; import-linter
    `ai-is-never-an-authority` ([04](04-ai-safety.md)).
26. **Secret/PII controls** — forbidden-secret filter on input, queries and
    documents before storage/embedding (strengthened this phase: whole
    passphrases, bare `token=`, `client_secret=`, `private_key=`); PII
    minimised structurally; no content in logs or metric labels.
27. **Retention** — results 15 min / 24 h, metadata 400 days, budgets 36
    months, content until indexed, tombstones 30 days, facts 400 days,
    aggregates 90 days / 5 years; daily per-organization prune
    ([06](06-security-and-privacy.md) §3).
28. **Provider failure behavior** — timeouts not retried (may have billed);
    429/5xx retried within the deadline; auth failures trip the breaker; open
    breaker fails fast; permitted fallback; outage is a 503 with `Retry-After`;
    readiness never includes AI.
29. **AI concurrency** — reservation and limit under the same row locks (8-way
    race reserves exactly one); in-flight cap holds under a burst; one
    idempotency key raced 8 ways is one execution and one provider call;
    settle racing expiry counts once.
30. **Analytics ingestion** — the Phase 6 event path only, via a routing
    consumer, in the routing transaction ([07](07-analytics.md)).
31. **Schema/metric registry** — versioned, immutable metric definitions over
    the product's own declared events; enumerated dimensions; no SQL.
32. **Tenant analytics isolation** — organization from the authenticated
    context, in the fixed statement and by RLS.
33. **Duplicate handling** — duplicate events never routed; facts unique on
    (organization, source, event, metric, version); 8-way publish is one fact.
34. **Aggregation** — hour and day buckets by occurrence time; projection and
    projected-mark in one transaction under a per-organization advisory lock.
35. **Late events** — bucketed by when they occurred; correct their historical
    bucket exactly once; corrections are new events.
36. **Rebuild/reconciliation** — `rebuild` and read-only `reconcile` from facts
    under the projection lock; `manage.py w360_analytics_aggregates`.
37. **Analytics query API** — one metric, bounded range, ≤ 2 of its own
    dimensions, row cap checked before the database; money grouped by
    currency; freshness on every answer.
38. **Performance** — invoke 114 ms / 46 statements with an instant provider;
    retrieval flat in query count (56) from 10 to 5,000 chunks; analytics query
    ~20 ms / 13 statements at every size; projection ~5,000 facts/s
    ([11](11-performance.md)).
39. **Observability** — bounded-label metrics for requests, latency, failures,
    breaker, fallback, tokens, cost, reservations, redactions, indexing,
    retrieval, facts, projection lag; structured logs with request and
    correlation ids and no content; `/health/deep` reports AI, never gates
    readiness ([09](09-operations.md)).
40. **Redis/RabbitMQ degradation** — Redis: rate limits degrade per process,
    spend and concurrency stay exact (tested). RabbitMQ: every unit of work is
    a due-work timer first; a lost message is re-dispatched (tested).
41. **Security findings** — see *Findings* below; none open.
42. **Frontend security/accessibility** — no HTML from the server, no model
    output rendered, `no-store` everywhere, nonce CSP unchanged; axe clean in
    jsdom and in Chrome; a 320px reflow defect found and fixed.
43. **Cleanup** — dead code removed (`adapter_keys`, `now_window`, the unused
    `publishPrice` client call); enums moved to `ai/domain.py` to keep modules
    from reaching into each other's models; no TODOs; lean-code review
    complete.
44. **Dependencies** — none added (Python or npm). No LangChain, LlamaIndex,
    vendor SDK, vector database, warehouse, Kafka, ClickHouse or OpenSearch.
    Infrastructure: the PostgreSQL `vector` extension (image
    `pgvector/pgvector:pg16-trixie` in compose and CI).
45. **ADRs/deviations** — ADR-030 (gateway owns providers, spend, safety),
    ADR-031 (pgvector, authorized before ranking), ADR-032 (product metrics
    over routed events). Deviations: no streaming (Task 56, not needed for
    acceptance); write-block exemptions for metering/RAG tables (D-023).
46. **Unresolved P1/P2/P3** — no P0; no Phase-8 P1 open. P2: D-023 (write-block
    exemption), D-024 (heuristic token estimates), D-025 (vendor data-policy
    metadata unverified), R8-5..R8-8. Carried from Phase 7, unrelated to and
    not blocking Phase 9: D-020 (reseller new-customer credential journey, P1
    go-live gate for that channel), D-021, D-022.
47. **Fresh DB** — 16/16: migrate refuses without pgvector and names the
    step; completes with it; every table, RLS policy, composite FK, freeze
    trigger and grant present; registry seeded; owner cannot re-price history;
    system checks pass as the application role.
48. **Phase 7 → 8 upgrade** — 23/23: Phase 7 data byte-identical after
    upgrade (checksums); exactly the six new role grants added; full rollback
    reverses every Phase 8 migration with Phase 7 data identical; re-apply
    succeeds.
49. **Decisions proposed to freeze for Phase 9** — see below.

## Findings (all fixed, with tests)

| Finding | Severity | Fix |
|---|---|---|
| `TenantTask` kept its context tokens on the task object, which one process shares across threads: overlapping runs reset each other's tenant context (a `ValueError`, or a context left in place). Latent since Phase 1; surfaced by the Phase 8 concurrency suite | P1 (latent; prefork workers unaffected) | tokens on the per-run, thread-local `self.request`; `test_overlapping_runs_of_one_task_each_keep_their_own_tenant` fails on the old code |
| Secret filter left the rest of an unquoted passphrase (`password = correct horse battery staple`) and missed bare `token=`, `client_secret=`, `private_key=` | P2 | passphrase values run to the end of the line; key list widened |
| Egress sent headers and body in separate writes with Nagle on: up to 40/200 ms stall per request (webhooks too) | P2 (latency) | `TCP_NODELAY` on every egress connection |
| Organization pages scrolled horizontally at 320px when a table had rows | P2 (WCAG 1.4.10) | shell `main` and intelligence grid constrained |
| Rehearsal built "Phase 7" with the live role registry, hiding what `authz.0007` adds | process | faithful Phase 7 build; the check now demands exactly +6 grants |

## Exit conditions

| Condition | Met by |
|---|---|
| AI gateway is centralized | one invoke path; `ai/transport.py` is the only sender |
| provider secrets are isolated | environment + header only; fitness tests |
| product skills are namespace-controlled | credential-derived namespace; tests |
| AI entitlement/quotas are authoritative | access decision + database reservation |
| usage/cost metering is concurrency-safe | row locks, lease timers; concurrency suite |
| products cannot bypass AI governance | every product path goes through `authorize` + `reserve` |
| AI never becomes source of truth | import-linter contract, no tools, no state from output |
| RAG ingestion is remote-contract based | product submits content; no product DB access |
| vector retrieval is tenant-safe | predicate + RLS inside the ranking statement |
| cross-product retrieval is deny-by-default | calling product only; own sources |
| retrieval authorization occurs before search | same statement; no retrieve-then-filter |
| deleted/revoked sources stop being retrievable | same transaction / at commit; tests |
| citations preserve provenance | stored metadata only; tests |
| prompt injection cannot bypass authorization/tools | hostile-document test |
| AI failures cannot affect core readiness | readiness excludes AI; heavy queue only |
| analytics ingestion is event-driven | routing consumer |
| analytics does not query product DBs | only published events |
| analytics events are idempotent | routing + unique facts; 8-way test |
| tenant analytics isolation is proven | acceptance + isolation tests |
| metric namespace ownership is enforced | credential-derived namespace; tests |
| aggregates are rebuildable | rebuild/reconcile; test + command |
| late events are handled correctly | occurrence-time buckets; test |
| money/currency analytics are safe | integer minor units; currency in every grouping |
| no unresolved P0 | none |
| no Phase-9-blocking P1 | none |
| full backend/frontend gates pass | table above |
| fresh database passes | 16/16 |
| Phase 7→8 upgrade passes | 23/23 |
| lean-code review completes | item 43 |

## Decisions proposed to freeze for Phase 9

1. **One AI gateway.** Products reach models only through
   `integrations/ai/invoke` with registered skills; no product receives a
   provider credential or names an endpoint.
2. **AI is never an authority.** No model output becomes permission,
   entitlement, billing, identity, audit or security state; models get no
   tools; the import-linter contract stays.
3. **Reserve before you call.** Worst-case reservation under row locks, lease
   timers for lost calls, fail closed without the database.
4. **Skills and prompts are immutable versions** in product namespaces;
   sharing is explicit.
5. **Retrieval is authorized inside the ranking statement** — organization,
   calling product, own sources, serving version, access tags — and never
   retrieve-then-filter; pgvector in PostgreSQL until the documented scaling
   trigger.
6. **Analytics has one ingestion path** (Phase 6 events → routing consumer →
   immutable facts → rebuildable aggregates); metrics are product-defined over
   the product's own events with enumerated dimensions; queries are fixed
   statements over aggregates.
7. **Estimated AI cost is not billing.** If commercial ever bills AI, it does so
   from events, never by importing `intelligence/`.
8. **Task context lives on the per-run request**, never on the shared task
   object.

## The question

> Can independently deployed Workeasy360 products now consume shared AI/RAG
> and analytics infrastructure without sharing their databases, leaking
> tenant/product data, bypassing entitlements/cost controls, or making AI a
> business source of truth?

**YES.**
