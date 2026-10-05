# Phase 8 — Known debt and deliberate omissions

## 1. Deliberately not built

| Omitted | Why | When to reconsider |
|---|---|---|
| Product-specific AI (lead scoring, deal summaries, cash-flow advice, demand forecasting, employee analysis) and product dashboards | forbidden: products own business meaning | never in this repository |
| Response streaming (Task 56) | no acceptance flow needs it; adds disconnect, partial-usage and audit-completion states | a product needs token-by-token UX; design partial settlement first |
| Model tools / function calling | no backend action may be model-initiated (04 §2) | a proposal-only protocol reviewed against 04-ai-safety |
| Approximate vector index (HNSW) | exact search over the authorized candidate set is fast and has perfect recall at current scale | the thresholds in [05-rag.md](05-rag.md) §9 |
| File extraction (PDF/DOCX) for ingestion | products send text; extraction is a parsing attack surface | a shared, sandboxed extractor is justified by ≥ 2 products |
| Metric backfill from past events | the event store is not a replay log; products republish history as events | a replay facility exists in the integration platform |
| Organization-local time buckets | UTC hour/day buckets are correct and simple; local-day answers need per-zone aggregates | a customer-visible requirement for local-day reporting |
| Invoicing AI spend | estimated cost is spend control, not billing; `commercial/` may not import `intelligence/` | commercial decides to meter AI as a billable usage item (via events, not imports) |
| Semantic caching of answers | correctness and isolation risk outweighs cost at current volume | measured repeated-query share justifies it |
| UI for adding models and publishing prices | rare staff operations; API with the same permission, step-up and audit | frequency justifies it |

## 2. Accepted debt (debt register)

| ID | Item | Close by |
|---|---|---|
| D-023 | AI metering (`ai_budget_period`, `ai_execution`, `ai_usage_line`, `ai_execution_content`) and RAG tables are exempt from the tenancy suspension write block, so in-flight work settles and deletions purge while an organization is suspended; new work is refused by the access decision instead | review 2026-12-25 |
| D-024 | Token estimates before a call are heuristic (UTF-8 bytes / 3 + 8), deliberately conservative; the reservation can over-hold budget for a request's duration. Metering always uses provider-reported usage | when a vendor count-tokens endpoint is adopted |
| D-025 | Provider data-handling metadata (region, retention, training) is descriptive, entered by staff; nothing verifies it against the vendor contract | legal review of each vendor account before go-live |

The gateway's rate limits degrade per process when Redis is down — the
existing D-012, unchanged; spend and concurrency stay exact.

## 3. Open items

- **Vendor credentials.** Real `W360_AI_CREDENTIAL_*` values must be issued and
  placed in the secret manager per environment; until then AI answers `503`.
  Prices seeded in `ai.0002` must be confirmed against the vendor account's
  actual rates by staff (publish a new price version if they differ).
- **Deletion at the vendor.** Deleting knowledge cannot delete what a vendor
  retained (06 §6); choose vendor accounts with zero-retention terms where
  offered.
- **pgvector in production** needs the one-time superuser step in
  [09-operations.md](09-operations.md) §5 per database.
