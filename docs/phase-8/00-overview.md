# Phase 8 — The shared AI, retrieval and analytics platform

**What Phase 8 adds:** one place where independently deployed products get AI
(model invocation through registered skills), retrieval over their own
indexed knowledge, and analytics over their own events — with entitlement,
spend limits, tenant and product isolation, safety controls, metering, audit
and failure isolation that no product can bypass, and without the platform
owning any product's business meaning.

| Document | Covers |
|---|---|
| [01-ai-gateway.md](01-ai-gateway.md) | The gateway flow, providers and adapters, the model registry and selection, credentials, timeouts, retries, the breaker, fallback, the error model, request ids |
| [02-cost-and-quota.md](02-cost-and-quota.md) | AI entitlement, budgets and limits, reservation and settlement, concurrency, the cost-failure policy, estimated cost vs billing |
| [03-skills-and-prompts.md](03-skills-and-prompts.md) | The skill and prompt registry, namespaces, immutability, sharing, structured output, platform skills, context assembly |
| [04-ai-safety.md](04-ai-safety.md) | Prompt-injection threat model, tool and action safety, AI as non-authority, the secret filter, provider data policy, output handling |
| [05-rag.md](05-rag.md) | Document model, ingestion contract, idempotency, versions, chunking, embeddings, vector storage, tenant and product isolation, retrieval, provenance, deletion, staleness, scaling |
| [06-security-and-privacy.md](06-security-and-privacy.md) | Permissions, security test matrix, retention, privacy and deletion, organization purge discoverability, provider deletion limits, audit |
| [07-analytics.md](07-analytics.md) | Event model, metric registry, dimension control, facts, aggregation, late events, rebuild, query API, access, money, freshness |
| [08-frontend.md](08-frontend.md) | Settings → AI, Settings → Analytics, Super Admin → AI; caching, CSP, accessibility |
| [09-operations.md](09-operations.md) | Queues and timers, failure matrix (provider, Redis, RabbitMQ, database), observability, runbooks, upgrade (pgvector) and rollback |
| [10-product-integration-guide.md](10-product-integration-guide.md) | **Start here if you are building a product:** endpoints, payloads, errors |
| [11-performance.md](11-performance.md) | Local measurements |
| [12-known-debt.md](12-known-debt.md) | What is deliberately not built, and open items |
| [phase-8-status.md](phase-8-status.md) | Status, gates, evidence, findings, decisions to freeze |

Decisions: [ADR-030](../architecture/adrs/ADR-030-ai-gateway-and-provider-ownership.md)
(one gateway owns providers, spend and safety), [ADR-031](../architecture/adrs/ADR-031-rag-storage-and-vector-isolation.md)
(pgvector in PostgreSQL, authorized before it ranks), [ADR-032](../architecture/adrs/ADR-032-analytics-semantic-and-event-model.md)
(product-defined metrics over routed events, rebuildable aggregates).

## 1. Ownership

| Workeasy360 owns | Products own |
|---|---|
| model gateway, provider adapters, model registry and prices | what a skill *means*: its prompt, its input and output contract |
| AI policy, entitlement check, spend reservation and metering | domain prompts, domain tools, scoring rules, AI actions |
| the skill/prompt *registry* and its immutability | which content to index, its identifiers and access tags |
| retrieval infrastructure, embeddings, vector isolation | what a metric means, its dimensions' values |
| analytics ingestion, facts, aggregates, the bounded query | product dashboards |

There is no CRM deal logic, Books forecasting, inventory scoring or product
dashboard in this repository, and a fitness test fails the build if a module
in `intelligence/` compares anything to a product code.

## 2. The module map

```
intelligence/                         (beside commercial/, above integration/)
  gateway     skill invocation, async jobs, direct retrieval      -> ai, rag
  rag         sources, versioned documents, chunks, pgvector,
              ingestion, indexing, retrieval                       -> ai
  ai          provider adapters, transport (egress), credentials,
              model registry, policy, budget, invocation, skills,
              safety, schema subset, tokens, staff control plane
  analytics   metric registry, extraction, facts, projection,
              aggregates, query, rebuild                           (independent)
```

Import-linter: `top-level-layers` places `intelligence` beside `commercial`
(neither imports the other); `intelligence-internal-layers`;
`ai-providers-are-sandboxed`; `ai-is-never-an-authority`.

## 3. The AI flow

```mermaid
sequenceDiagram
    participant P as Product (separate app)
    participant G as POST /integrations/ai/invoke
    participant DB as PostgreSQL (RLS)
    participant V as pgvector
    participant E as egress
    participant M as AI provider

    P->>G: client credential + organization_id + {skill, input, retrieval}
    G->>G: rate limits (product / org / skill)
    G->>G: skill resolvable by this caller? input matches its schema?
    G->>DB: organization active? product entitled (write)? plan grants AI? AI on?
    G->>G: select approved model(s); instructions + input fit?
    G->>DB: reserve worst-case cost under the month's budget row locks; lease timer
    G->>E: embed the query (metered)
    E->>M: embeddings
    G->>V: nearest chunks of THIS org, THIS product, named sources, serving versions, caller's tags
    G->>E: preamble + instructions + <context> + <input> (no tools)
    E->>M: generation (bounded retries, deadline, breaker, permitted fallback)
    G->>G: validate output contract; map citations to stored provenance
    G->>DB: settle actual usage at pinned prices; keep result 15 min
    G-->>P: output, citations, usage, request_id
```

## 4. The analytics flow

```mermaid
sequenceDiagram
    participant P as Product
    participant I as POST /integrations/events
    participant R as route_event
    participant F as analytics_fact
    participant J as analytics.project (heavy)
    participant A as analytics_aggregate

    P->>I: product.x.record.created v1 (Phase 6 contract, unchanged)
    I->>R: accepted once (idempotent), routed
    R->>F: consumer: one fact per active metric over this event (same transaction)
    R->>R: webhooks, automations (unchanged)
    J->>F: claim unprojected facts (advisory lock per organization)
    J->>A: add sums to hour and day buckets of occurred_at; mark projected
    P->>A: POST /integrations/analytics/query (own or shared metric, bounded)
```

## 5. Deliberately not built

Product-specific AI (lead scoring, deal summaries, cash-flow advice, demand
forecasting, employee analysis), product dashboards, response streaming (not
needed for acceptance), model-proposed tool calls, organization-uploaded
platform knowledge (file extraction), a vector database, a warehouse, a
metric backfill from past events, organization-local time buckets. See
[12-known-debt.md](12-known-debt.md).
